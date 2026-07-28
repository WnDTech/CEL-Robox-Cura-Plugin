import os
import sys
import time
import logging
import argparse
import serial

from .robox_protocol import (
    RoboxProtocol, RoboxError, RoboxConnectionError,
    RoboxFirmwareError, StatusResponse
)
from .robox_postprocessor import PostProcessor

logger = logging.getLogger(__name__)

CEL_ROBOX_DIR = os.path.join(os.environ.get("USERPROFILE", ""), "Documents", "CEL Robox")
DEFAULT_COMMON = os.path.join(CEL_ROBOX_DIR, "Common")


def find_common_dir():
    candidates = [
        DEFAULT_COMMON,
        os.path.join(CEL_ROBOX_DIR, "..", "Common"),
        os.path.join("C:\\Program Files\\CEL", "Common"),
    ]
    for c in candidates:
        p = os.path.abspath(c)
        if os.path.isdir(os.path.join(p, "Macros")):
            return p
    return DEFAULT_COMMON


def cmd_detect(args):
    proto = RoboxProtocol()
    try:
        port = proto.find_printer()
        print(port)
        return 0
    except RoboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_status(args):
    proto = RoboxProtocol()
    try:
        proto.connect()
        while True:
            status = proto.get_status()
            line = (f"T=[{status.temp_nozzle0}C/{status.temp_nozzle1}C] "
                    f"Bed={status.temp_bed}C "
                    f"Line={status.print_line_number}")
            print(line)
            if status.has_errors():
                print(f"  WARNING: Error code 0x{status.error_code:02X}")
            if not args.monitor:
                break
            time.sleep(1)
    except RoboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        proto.disconnect()
    return 0


def cmd_info(args):
    proto = RoboxProtocol()
    try:
        proto.connect()
        fw = proto.get_firmware_version()
        pid = proto.get_printer_id()
        print(f"Firmware: {fw}")
        print(f"Printer ID: {pid}")
    except RoboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        proto.disconnect()
    return 0


def cmd_process(args):
    common_dir = args.common or find_common_dir()
    pp = PostProcessor(
        common_dir,
        head_type=args.head_type,
        use_nozzle0=args.nozzle0,
        use_nozzle1=args.nozzle1,
        safeties=not args.no_safeties,
    )
    try:
        with open(args.input) as f:
            input_gcode = f.read()
    except IOError as e:
        print(f"Error reading input: {e}", file=sys.stderr)
        return 1
    output = pp.process(input_gcode)
    if args.output:
        try:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Processed G-code written to {args.output}")
        except IOError as e:
            print(f"Error writing output: {e}", file=sys.stderr)
            return 1
    else:
        print(output)
    return 0


def cmd_print(args):
    common_dir = args.common or find_common_dir()

    pp = PostProcessor(
        common_dir,
        head_type=args.head_type,
        use_nozzle0=args.nozzle0,
        use_nozzle1=args.nozzle1,
        safeties=not args.no_safeties,
    )

    try:
        with open(args.file) as f:
            input_gcode = f.read()
    except IOError as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        return 1

    processed = pp.process(input_gcode)
    if args.output:
        try:
            with open(args.output, "w") as f:
                f.write(processed)
            print(f"Processed G-code written to {args.output}")
        except IOError as e:
            print(f"Error writing output: {e}", file=sys.stderr)
            return 1

    gcode_lines = [l.strip() for l in processed.split("\n")
                   if l.strip() and not l.strip().startswith(";") and not l.strip().startswith("#")]
    print(f"Post-processed: {len(gcode_lines)} operative lines from {args.file}")

    if args.dry_run:
        print("Dry run complete (no printer connection)")
        return 0

    proto = RoboxProtocol()
    try:
        proto.connect(args.port)
        print(f"Connected. Firmware: {proto.get_firmware_version()}")

        if args.detect_head:
            try:
                pid = proto.get_printer_id()
                print(f"Printer ID: {pid}")
            except RoboxError:
                pass

        # Upload file as 512-byte chunks
        print("Uploading G-code...")
        proto.start_data_file()
        seq = -1
        buffer = ""
        for line in gcode_lines:
            line_to_add = line + "\r"
            if len(buffer) + len(line_to_add) > 512:
                if buffer:
                    seq += 1
                    ack = proto.send_data_chunk(seq, buffer)
                    if ack and ack.has_errors():
                        print(f"  Warning: firmware errors: {ack.errors}")
                buffer = line_to_add
            else:
                buffer += line_to_add
            if seq >= 0 and seq % 10 == 0:
                print(f"  Sent {seq+1} chunks...")

        if buffer:
            seq += 1
            proto.end_data_file(seq, buffer.encode("ascii"))
        else:
            proto.end_data_file(seq, b"")
        print(f"Upload complete ({seq+1} chunks)")

        # Start print
        print("Starting print...")
        proto.initiate_print()

        # Monitor with safety checks
        print("\nMonitoring print (Ctrl+C to stop):")
        try:
            while True:
                status = proto.get_status()
                info = (f"T=[{status.temp_nozzle0}/{status.temp_nozzle1}C] "
                        f"Bed={status.temp_bed}C "
                        f"Line={status.print_line_number}")
                print(f"\r{info}", end="")
                if status.has_errors():
                    print(f"\n  PRINTER ERROR: code 0x{status.error_code:02X}")
                if status.error_code:
                    print("\n  Aborting print due to printer error...")
                    proto.abort_print()
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping print...")
            try:
                proto.abort_print()
                print("Print aborted")
            except Exception:
                print("Could not send abort (printer may be disconnected)")

    except (RoboxConnectionError, RoboxFirmwareError) as e:
        print(f"Printer error: {e}", file=sys.stderr)
        print("Print stopped. If heaters are on, turn off the printer.", file=sys.stderr)
        return 1
    except RoboxError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except serial.SerialException as e:
        print(f"USB error: {e}", file=sys.stderr)
        print("Printer may be disconnected. Turn off the printer if heaters are on.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            proto.disconnect()
        except Exception:
            pass
    return 0


def main():
    parser = argparse.ArgumentParser(description="CEL Robox standalone print host")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")

    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_args(p):
        p.add_argument("--common", help="Path to Common directory")
        p.add_argument("--head-type", default="RBX01-SM",
                       help="Head type code (e.g. RBX01-DM, RBX01-SM)")
        p.add_argument("--no-nozzle0", action="store_false", dest="nozzle0",
                       help="Disable nozzle 0")
        p.add_argument("--nozzle1", action="store_true", default=False,
                       help="Use nozzle 1")
        p.add_argument("--no-safeties", action="store_true",
                       help="Disable safety features")

    p_detect = sub.add_parser("detect", help="Detect Robox printer")
    p_detect.set_defaults(func=cmd_detect)

    p_info = sub.add_parser("info", help="Get printer info")
    p_info.set_defaults(func=cmd_info)

    p_status = sub.add_parser("status", help="Get printer status")
    p_status.add_argument("--monitor", "-m", action="store_true",
                          help="Continuously monitor")
    p_status.set_defaults(func=cmd_status)

    p_process = sub.add_parser("process", help="Process G-code for Robox")
    add_common_args(p_process)
    p_process.add_argument("input", help="Input G-code file")
    p_process.add_argument("--output", "-o", help="Output file (default: stdout)")
    p_process.set_defaults(func=cmd_process)

    p_print = sub.add_parser("print", help="Process and print a G-code file")
    add_common_args(p_print)
    p_print.add_argument("file", help="G-code file to print")
    p_print.add_argument("--port", "-p", help="Serial port (auto-detect if omitted)")
    p_print.add_argument("--detect-head", action="store_true",
                         help="Detect head type from printer")
    p_print.add_argument("--output", "-o", help="Write processed G-code to file")
    p_print.add_argument("--dry-run", action="store_true",
                         help="Process only, don't connect to printer")
    p_print.set_defaults(func=cmd_print)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG,
                            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.command in ("detect", "info", "status"):
        args.nozzle0 = getattr(args, "nozzle0", True)
        args.nozzle1 = getattr(args, "nozzle1", False)
        args.no_safeties = getattr(args, "no_safeties", False)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
