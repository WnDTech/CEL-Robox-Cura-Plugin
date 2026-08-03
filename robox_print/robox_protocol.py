import serial
import serial.tools.list_ports
import struct
import time
import threading
import re
import logging

logger = logging.getLogger(__name__)

ROBOX_VID = 0x16D0
ROBOX_PID = 0x081B
BAUD = 115200
TIMEOUT = 2
POLL_TIMEOUT = 0.3
WRITE_TIMEOUT = 3

# TX command bytes
CMD_STATUS_REQUEST      = 0xB0
CMD_EXECUTE_GCODE       = 0x95
CMD_DATA_FILE_CHUNK     = 0x91
CMD_START_OF_DATA_FILE  = 0x90
CMD_START_JOB_FILE      = 0x97
CMD_END_OF_DATA_FILE    = 0x92
CMD_INITIATE_PRINT      = 0x94
CMD_ABORT_PRINT         = 0xFF
CMD_PAUSE_RESUME_PRINT  = 0x98
CMD_QUERY_FIRMWARE      = 0xB4
CMD_READ_PRINTER_ID     = 0xB2
CMD_SET_TEMPERATURES    = 0xC3
CMD_SET_AMBIENT_LED     = 0xC2
CMD_CLEAR_ERRORS        = 0xC0
CMD_REPORT_ERRORS       = 0xB3
CMD_READ_HEAD_EEPROM    = 0xA1
CMD_READ_REEL0_EEPROM   = 0xA3
CMD_READ_REEL1_EEPROM   = 0xA5
CMD_SET_AMBIENT_LED     = 0xC2
CMD_SET_BUTTON_LED      = 0xC5
CMD_SET_HEAD_POWER      = 0xCA
CMD_SET_E_FEED_RATE_MULTIPLIER = 0xC7
CMD_SET_D_FEED_RATE_MULTIPLIER = 0xC4

# RX response bytes  (all have bit 7 set)
RSP_ACK            = 0xE3
RSP_FIRMWARE       = 0xE4
RSP_PRINTER_ID     = 0xE5
RSP_STATUS         = 0xE1
RSP_GCODE          = 0xE7
RSP_HEAD_EEPROM    = 0xE2
RSP_REEL0_EEPROM   = 0xE6
RSP_REEL1_EEPROM   = 0xE8

# Fixed RX packet sizes (firmware >= 768)
FW_VER_PACKET_SIZE = 9       # 1 type + 8 ASCII chars
PRINTER_ID_SIZE    = 257     # 1 type + 256 data
STATUS_PACKET_SIZE = 222     # for firmware >= 768
ACK_PACKET_SIZE    = 65      # 1 type + 64 error bits as '0'/'1' chars
EEPROM_PACKET_SIZE = 193     # 1 type + 192 bytes (EEPROM_VIRTUAL_LENGTH = 0xC0)

# Firmware error bit positions
CRITICAL_ERRORS = {
    21,  # HEAD_POWER_OVERTEMP
    22,  # BED_THERMISTOR
    23,  # NOZZLE0_THERMISTOR
    24,  # NOZZLE1_THERMISTOR
    10,  # GCODE_BUFFER_OVERRUN
    18,  # B_STUCK
    12,  # MAX_GANTRY_ADJUSTMENT
    34,  # HEAD_SHORTED
    35,  # X_DRIVER
    36,  # Y_DRIVER
    37,  # ZA_DRIVER
    38,  # ZB_DRIVER
    39,  # E_DRIVER
    40,  # D_DRIVER
    41,  # BED_TEMPERATURE_DROOP
}

ERROR_NAMES = {
    0:"SD_CARD",1:"CHUNK_SEQUENCE",2:"FILE_TOO_LARGE",3:"GCODE_LINE_TOO_LONG",
    4:"USB_RX",5:"USB_TX",6:"BAD_COMMAND",7:"HEAD_EEPROM",
    8:"BAD_FIRMWARE_FILE",9:"FLASH_CHECKSUM",10:"GCODE_BUFFER_OVERRUN",
    11:"FILE_READ_CLOBBERED",12:"MAX_GANTRY_ADJUSTMENT",13:"REEL0_EEPROM",
    14:"E_FILAMENT_SLIP",15:"D_FILAMENT_SLIP",16:"NOZZLE_FLUSH_NEEDED",
    17:"Z_TOP_SWITCH",18:"B_STUCK",19:"REEL1_EEPROM",20:"HEAD_POWER_EEPROM",
    21:"HEAD_POWER_OVERTEMP",22:"BED_THERMISTOR",23:"NOZZLE0_THERMISTOR",
    24:"NOZZLE1_THERMISTOR",25:"B_POSITION_LOST",26:"E_LOAD_SLIP",
    27:"D_LOAD_SLIP",28:"E_UNLOAD_SLIP",29:"D_UNLOAD_SLIP",
    30:"POWEROFF_WHILST_HOT",31:"E_NO_FILAMENT",32:"D_NO_FILAMENT",
    33:"B_POSITION_WARNING",34:"HEAD_SHORTED",35:"X_DRIVER",36:"Y_DRIVER",
    37:"ZA_DRIVER",38:"ZB_DRIVER",39:"E_DRIVER",40:"D_DRIVER",
    41:"BED_TEMPERATURE_DROOP",
}

MAX_NOZZLE_TEMP = 300
MAX_BED_TEMP = 120
MAX_CHAMBER_TEMP = 80


class RoboxError(Exception):
    pass

class RoboxFirmwareError(RoboxError):
    def __init__(self, errors):
        names = [ERROR_NAMES.get(e, f"BIT{e}") for e in errors]
        super().__init__(f"Firmware errors: {', '.join(names)}")
        self.errors = errors

class RoboxConnectionError(RoboxError):
    pass


class AckPacket:
    def __init__(self, data):
        self.raw = data
        self.errors = []
        # Bytes 1-65 are ASCII '0'/'1' error flags
        for byte_pos in range(1, min(len(data), ACK_PACKET_SIZE)):
            if data[byte_pos] == ord('1'):
                self.errors.append(byte_pos - 1)

    def has_errors(self):
        return len(self.errors) > 0

    def has_critical_errors(self):
        return any(e in CRITICAL_ERRORS for e in self.errors)

    def __repr__(self):
        if not self.errors:
            return "ACK(ok)"
        return f"ACK(errors={[ERROR_NAMES.get(e, f'BIT{e}') for e in self.errors]})"


class RoboxProtocol:
    def __init__(self, port=None):
        self.ser = None
        self._port = port
        self._firmware_version = 0
        self._lock = threading.Lock()

    def find_printer(self):
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if p.vid == ROBOX_VID and p.pid == ROBOX_PID:
                logger.info(f"Robox on {p.device}")
                return p.device
        raise RoboxError("No Robox printer found on USB")

    def connect(self, port=None):
        p = port or self._port or self.find_printer()
        try:
            self.ser = serial.Serial(p, BAUD, timeout=TIMEOUT, write_timeout=WRITE_TIMEOUT)
        except serial.SerialException as e:
            raise RoboxConnectionError(f"Cannot open {p}: {e}")
        self.ser.flushInput()
        self.ser.flushOutput()
        self.ser.send_break(duration=0.1)
        time.sleep(0.3)
        self.ser.flushInput()
        self.ser.flushOutput()
        time.sleep(0.5)
        logger.info(f"Connected to {p}")
        return p

    def disconnect(self):
        if self.ser:
            try: self.ser.close()
            except: pass

    def _send(self, data):
        if not self.ser or not self.ser.is_open:
            raise RoboxConnectionError("Not connected")
        try:
            self.ser.write(data)
            self.ser.flush()
        except serial.SerialException as e:
            raise RoboxConnectionError(f"Write failed: {e}")

    def _recv(self, length):
        if not self.ser or not self.ser.is_open:
            raise RoboxConnectionError("Not connected")
        try:
            data = self.ser.read(length)
        except serial.SerialException as e:
            raise RoboxConnectionError(f"Read failed: {e}")
        if len(data) < length:
            raise RoboxError(f"Timeout: expected {length} bytes, got {len(data)}")
        return data

    def _drain(self):
        if self.ser and self.ser.is_open:
            try:
                n = self.ser.in_waiting
                if n > 0:
                    self.ser.read(n)
            except Exception:
                pass

    def _read_ack(self):
        data = self._recv(ACK_PACKET_SIZE)
        if data[0] != RSP_ACK:
            raise RoboxError(f"Expected ACK (0x{RSP_ACK:02X}), got 0x{data[0]:02X}")
        ack = AckPacket(data)
        if ack.has_critical_errors():
            raise RoboxFirmwareError(ack.errors)
        return ack

    def get_status(self):
        with self._lock:
            self._send(bytes([CMD_STATUS_REQUEST]))
            data = self._recv(STATUS_PACKET_SIZE)
            if data[0] != RSP_STATUS:
                raise RoboxError(f"Expected status 0x{RSP_STATUS:02X}, got 0x{data[0]:02X}")
            self._drain()
            return StatusResponse(data)

    def get_firmware_version(self):
        with self._lock:
            self._send(bytes([CMD_QUERY_FIRMWARE]))
            data = self._recv(FW_VER_PACKET_SIZE)
            if data[0] != RSP_FIRMWARE:
                raise RoboxError(f"Expected firmware 0x{RSP_FIRMWARE:02X}, got 0x{data[0]:02X}")
            self._drain()
            fw_str = data[1:9].decode("ascii", errors="replace").strip()
            return fw_str

    def get_printer_id(self):
        with self._lock:
            self._send(bytes([CMD_READ_PRINTER_ID]))
            data = self._recv(PRINTER_ID_SIZE)
            if data[0] != RSP_PRINTER_ID:
                raise RoboxError(f"Expected printer ID 0x{RSP_PRINTER_ID:02X}, got 0x{data[0]:02X}")
            self._drain()
            return data[1:].decode("ascii", errors="replace").strip()

    def execute_gcode(self, gcode_line, timeout=None):
        if timeout is not None:
            acquired = self._lock.acquire(timeout=timeout)
            if not acquired:
                return None
            try:
                return self._execute_gcode_impl(gcode_line)
            finally:
                self._lock.release()
        else:
            with self._lock:
                return self._execute_gcode_impl(gcode_line)

    def _execute_gcode_impl(self, gcode_line):
        clean = gcode_line.strip()
        clean = re.sub(";.*$", "", clean).rstrip()
        payload = (clean + "\n").encode("ascii")
        length_str = f"{len(payload):04X}".encode("ascii")
        self._send(bytes([CMD_EXECUTE_GCODE]) + length_str + payload)
        rsp_byte = self._recv(1)[0]
        if rsp_byte != RSP_GCODE:
            raise RoboxError(f"Expected GCODE response 0x{RSP_GCODE:02X}, got 0x{rsp_byte:02X}")
        resp_length = int(self._recv(4).decode("ascii"), 16)
        if resp_length > 2048:
            raise RoboxError("Invalid G-code response length")
        resp_data = self._recv(resp_length) if resp_length > 0 else b""
        self._drain()
        return resp_data.decode("ascii", errors="replace").strip()

    def get_filament_status(self):
        """Check which extruder slots have filament. Returns dict with slot0/slot1 presence."""
        resp = self.execute_gcode("M119", timeout=2)
        result = {"slot0_d": False, "slot1_e": False}
        if not resp:
            return result
        # Parse: M119 X:1 Y:1 Z:0 Z+:0 E:1 D:1 B:0 Eindex:0 Dindex:0
        for part in resp.replace("\r\n", " ").replace("\n", " ").split():
            if part.startswith("D:"):
                result["slot0_d"] = part[2:] == "1"
            elif part.startswith("E:"):
                result["slot1_e"] = part[2:] == "1"
        return result

    def get_temperatures(self):
        """Get temperatures via M105. Returns dict or None on timeout."""
        resp = self.execute_gcode("M105", timeout=0.2)
        if resp is None:
            return None
        temps = {"n0": 0, "n1": 0, "bed": 0, "chamber": 0, "ambient": 0,
                 "target_n0": None, "target_n1": None, "target_bed": None}
        parts = resp.replace("\r\n", " ").replace("\n", " ").split()
        i = 0
        while i < len(parts):
            p = parts[i].strip()
            if len(p) >= 2 and p[1] == ":":
                key = p[0]
                try:
                    val = int(p[2:])
                except ValueError:
                    i += 1
                    continue
                if key in ("S", "N0"):
                    temps["n0"] = val
                    # Next part might be target like @200
                    if i + 1 < len(parts) and parts[i+1].startswith("@"):
                        temps["target_n0"] = int(parts[i+1][1:])
                elif key == "T":
                    temps["n1"] = val
                    if i + 1 < len(parts) and parts[i+1].startswith("@"):
                        temps["target_n1"] = int(parts[i+1][1:])
                elif key == "B":
                    temps["bed"] = val
                    if i + 1 < len(parts) and parts[i+1].startswith("^"):
                        temps["target_bed"] = int(parts[i+1][1:])
                elif key == "A":
                    temps["ambient"] = val
            i += 1
        return temps

    def start_data_file(self, file_id=b"gcode", is_job=True):
        """Start a data file transfer with a 16-byte file identifier.
        Use is_job=True (COMMAND_START_JOB_FILE 0x97) for printable jobs."""
        cmd = CMD_START_JOB_FILE if is_job else CMD_START_OF_DATA_FILE
        payload = file_id[:16].ljust(16, b"\x00")
        with self._lock:
            self._send(bytes([cmd]) + payload)
            return self._read_ack()

    def send_data_chunk(self, sequence, gcode_line, retries=3):
        """Send a data chunk. Each chunk carries up to 512 bytes of data
        (padded with \r to 512). Total packet: 1+8+512 = 521 bytes."""
        payload = gcode_line.encode("ascii")
        if len(payload) > 512:
            raise RoboxError(f"Chunk data too long: {len(payload)} > 512")
        if len(payload) < 512:
            payload = payload + b"\r" * (512 - len(payload))
        seq_str = f"{sequence:08X}".encode("ascii")
        for attempt in range(retries):
            try:
                with self._lock:
                    self._send(bytes([CMD_DATA_FILE_CHUNK]) + seq_str + payload)
                    return self._read_ack()
            except (RoboxError, RoboxConnectionError) as e:
                if attempt < retries - 1:
                    logger.warning(f"Chunk {sequence} failed, retry {attempt+1}: {e}")
                    time.sleep(0.1)
                else:
                    raise RoboxError(f"Failed chunk {sequence}: {e}")

    def end_data_file(self, sequence, remaining_data=b""):
        """End a data file transfer. Packet: 0x92 + 8hex seq + 4hex len + data."""
        with self._lock:
            seq_str = f"{sequence:08X}".encode("ascii")
            rem_len = f"{len(remaining_data):04X}".encode("ascii")
            self._send(bytes([CMD_END_OF_DATA_FILE]) + seq_str + rem_len + remaining_data)
            return self._read_ack()

    def initiate_print(self, job_id=b"gcode"):
        with self._lock:
            payload = job_id[:16].ljust(16, b"\x00")
            self._send(bytes([CMD_INITIATE_PRINT]) + payload)
            ack = self._read_ack()
            if ack.has_errors():
                raise RoboxFirmwareError(ack.errors)
            return True

    def abort_print(self):
        try:
            with self._lock:
                self._send(bytes([CMD_ABORT_PRINT]))
                return self._read_ack()
        except Exception:
            pass
        return True

    def get_head_type(self):
        """Read the head type string from the head EEPROM.
        Sends [0xA1], expects HEAD_EEPROM response (0xE2) with ASCII name."""
        CMD_READ_HEAD_EEPROM = 0xA1
        with self._lock:
            self._send(bytes([CMD_READ_HEAD_EEPROM]))
            data = self._recv(128)
            if data[0] != RSP_HEAD_EEPROM:
                raise RoboxError(f"Expected head EEPROM 0x{RSP_HEAD_EEPROM:02X}, got 0x{data[0]:02X}")
            name = data[1:9].decode("ascii", errors="replace").strip("\x00").strip()
            return name

    def clear_errors(self):
        """Clear all firmware error flags. Sends [0xC0], expects ACK."""
        with self._lock:
            self._send(bytes([CMD_CLEAR_ERRORS]))
            return self._read_ack()

    def report_errors(self):
        """Query firmware error flags. Sends [0xB3], expects ACK with 64 error bits."""
        with self._lock:
            self._send(bytes([CMD_REPORT_ERRORS]))
            data = self._recv(ACK_PACKET_SIZE)
            if data[0] != RSP_ACK:
                raise RoboxError(f"Expected ACK 0x{RSP_ACK:02X}, got 0x{data[0]:02X}")
            error_bits = data[1:65].decode("ascii", errors="replace")
            errors = []
            for i, bit in enumerate(error_bits):
                if bit == '1':
                    errors.append(i)
            return errors

    def pause_resume(self, pause=True):
        """Pause or resume the current print job.
        Sends [0x98] + '1'/'0' (firmware requires 2-byte minimum packet)."""
        with self._lock:
            payload = b"1" if pause else b"0"
            self._send(bytes([CMD_PAUSE_RESUME_PRINT]) + payload)
            return self._read_ack()

    def set_temperatures(self, nozzle0=None, nozzle1=None, bed=None, chamber=None):
        with self._lock:
            parts = []
            if nozzle0 is not None:
                temp = max(0, min(int(nozzle0), MAX_NOZZLE_TEMP))
                parts.append(f"N0:{temp}")
            if nozzle1 is not None:
                temp = max(0, min(int(nozzle1), MAX_NOZZLE_TEMP))
                parts.append(f"N1:{temp}")
            if bed is not None:
                temp = max(0, min(int(bed), MAX_BED_TEMP))
                parts.append(f"B:{temp}")
            if chamber is not None:
                temp = max(0, min(int(chamber), MAX_CHAMBER_TEMP))
                parts.append(f"C:{temp}")
            payload = " ".join(parts).encode("ascii")
            self._send(bytes([CMD_SET_TEMPERATURES]) + payload)
            return self._read_ack()

    def set_ambient_led(self, r, g, b):
        """Set the ambient (enclosure) LED colour. Sends [0xC2] + 6 hex chars
        (RRGGBB), expects ACK."""
        with self._lock:
            payload = f"{r:02x}{g:02x}{b:02x}".encode("ascii")
            self._send(bytes([CMD_SET_AMBIENT_LED]) + payload)
            return self._read_ack()

    def set_button_led(self, r, g, b):
        """Set the front button LED colour. Sends [0xC5] + 6 hex chars
        (RRGGBB), expects ACK."""
        with self._lock:
            payload = f"{r:02x}{g:02x}{b:02x}".encode("ascii")
            self._send(bytes([CMD_SET_BUTTON_LED]) + payload)
            return self._read_ack()

    def set_head_power(self, on):
        """Set head power on/off. Sends [0xCA] + '1'/'0', expects ACK."""
        with self._lock:
            self._send(bytes([CMD_SET_HEAD_POWER]) + (b"1" if on else b"0"))
            return self._read_ack()

    def set_feed_rate_multiplier(self, extruder, multiplier):
        """Set feed rate multiplier for E (0) or D (1) extruder.
        Sends [0xC7]/[0xC4] + 8-char ASCII float, expects ACK."""
        cmd = CMD_SET_D_FEED_RATE_MULTIPLIER if extruder else CMD_SET_E_FEED_RATE_MULTIPLIER
        with self._lock:
            payload = f"{float(multiplier):8.4f}".encode("ascii")[:8]
            self._send(bytes([cmd]) + payload)
            return self._read_ack()

    def get_reel_eeprom(self, reel):
        """Read a reel EEPROM (reel=0 for slot 0/D, reel=1 for slot 1/E).
        Sends [0xA3]/[0xA5], expects the reel EEPROM report (192 data bytes).
        Returns the raw EEPROM bytes, or None if the reel is not present."""
        cmd = CMD_READ_REEL1_EEPROM if reel else CMD_READ_REEL0_EEPROM
        rsp = RSP_REEL1_EEPROM if reel else RSP_REEL0_EEPROM
        with self._lock:
            self._send(bytes([cmd]))
            data = self._recv(EEPROM_PACKET_SIZE)
            if data[0] != rsp:
                raise RoboxError(f"Expected reel EEPROM 0x{rsp:02X}, got 0x{data[0]:02X}")
            return data[1:]

    def get_reel_temperatures(self, reel):
        """Read nozzle/bed/ambient target temperatures from a reel EEPROM.
        Layout (firmware heaters.c get_nozzle_targets_from_reel /
        get_bed_targets_from_reel):
          nozzle first layer 0x28, nozzle normal 0x30
          bed first layer 0x38, bed normal 0x40
          ambient 0x48
        Returns dict or None if reel absent."""
        try:
            data = self.get_reel_eeprom(reel)
        except Exception:
            return None
        if not data or len(data) < 0x50:
            return None
        def f(addr):
            try:
                return float(data[addr:addr+8].decode("ascii", errors="replace").strip() or 0)
            except Exception:
                return 0.0
        return {
            "nozzle_first_layer": f(0x28),
            "nozzle": f(0x30),
            "bed_first_layer": f(0x38),
            "bed": f(0x40),
            "ambient": f(0x48),
        }


class StatusResponse:
    def __init__(self, data):
        self.raw = data
        self.door_open = False
        self.error_code = 0
        self.print_line_number = 0
        self.temp_nozzle0 = 0
        self.temp_nozzle1 = 0
        self.temp_bed = 0

    def _parse_float(self, data, offset):
        try:
            s = data[offset:offset+8].decode("ascii", errors="replace").strip()
            return int(float(s)) if s else 0
        except:
            return 0

    def has_errors(self):
        return self.error_code != 0

    def __repr__(self):
        return (f"ST(T={self.temp_nozzle0}/{self.temp_nozzle1}C "
                f"Bed={self.temp_bed}C Line={self.print_line_number})")

