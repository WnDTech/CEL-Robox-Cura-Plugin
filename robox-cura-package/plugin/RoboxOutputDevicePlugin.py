import os
import sys
import threading
import time
import re
from io import StringIO
from typing import List, Optional, cast, TYPE_CHECKING

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "robox_print"))
from robox_protocol import RoboxProtocol, RoboxError, RoboxConnectionError
import serial.tools.list_ports

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from UM.i18n import i18nCatalog
from UM.Logger import Logger
from UM.Mesh.MeshWriter import MeshWriter
from UM.Message import Message
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin
from UM.PluginRegistry import PluginRegistry

from cura.CuraApplication import CuraApplication
from cura.PrinterOutput.PrinterOutputDevice import PrinterOutputDevice, ConnectionState, ConnectionType
from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from cura.PrinterOutput.Models.PrintJobOutputModel import PrintJobOutputModel
from cura.PrinterOutput.GenericOutputController import GenericOutputController

if TYPE_CHECKING:
    from UM.Scene.SceneNode import SceneNode

catalog = i18nCatalog("cura")
ROBOX_VID = 0x16D0
ROBOX_PID = 0x081B


def get_material_info(stack):
    """Read material name and temperatures from Cura's active profile."""
    nozzle_temp = 210
    bed_temp = 60
    material_name = "PLA"
    if not stack:
        return nozzle_temp, bed_temp, material_name
    try:
        nozzle_temp = stack.getProperty("material_print_temperature", "value") or 210
        bed_temp = stack.getProperty("material_bed_temperature", "value") or 60
    except Exception:
        pass
    try:
        extruders = getattr(stack, "extruders", None)
        if extruders is None:
            extruders = getattr(stack, "extruderList", [])
        if extruders:
            mat = extruders[0].material
            if mat:
                material_name = mat.getMetaDataEntry("material", "PLA")
    except Exception:
        try:
            material_name = stack.getProperty("material_type", "value") or "PLA"
        except Exception:
            material_name = "PLA"
    return nozzle_temp, bed_temp, material_name


def build_advisory(nozzle_temp, bed_temp, material_name):
    """Build material-specific advisory text."""
    mat = material_name.upper()
    if not mat or mat in ("EMPTY", "GENERIC", ""):
        if nozzle_temp >= 260 or bed_temp >= 100:
            mat = "PC"
        elif nozzle_temp >= 240 or bed_temp >= 85:
            mat = "ABS"
        elif nozzle_temp >= 230 or bed_temp >= 75:
            mat = "PETG"
        elif nozzle_temp >= 220 and bed_temp >= 70:
            mat = "PETG"
        elif nozzle_temp >= 220:
            mat = "TPU"
        else:
            mat = "PLA"

    tips = []
    if "ABS" in mat:
        tips.append("Enclosure required - prevents warping")
        tips.append("No drafts - keep door closed")
        tips.append("Good ventilation - ABS fumes harmful")
    elif "ASA" in mat:
        tips.append("Enclosure recommended - UV resistant")
        tips.append("Good ventilation")
    elif "PETG" in mat:
        tips.append("Print slower than PLA")
        tips.append("Keep door closed for stable temp")
    elif "PA" in mat or "NYLON" in mat:
        tips.append("Keep filament dry - absorbs moisture")
        tips.append("Enclosure recommended")
    elif "PC" in mat:
        tips.append("Enclosure required - high shrinkage")
        tips.append("Good ventilation")
    elif "TPU" in mat:
        tips.append("Print slowly (20-30mm/s)")
        tips.append("Direct drive recommended")
    else:
        mat = "PLA"
        tips.append("No enclosure needed - open door OK")

    msg = f"Material: {mat}\nNozzle: {nozzle_temp}C  Bed: {bed_temp}C"
    if tips:
        msg += "\n\n" + "\n".join(tips)
    return msg


class RoboxPrinterDevice(PrinterOutputDevice):
    updateTemps = pyqtSignal(dict)
    updateProgressSignal = pyqtSignal(int, int)

    def __init__(self, port):
        super().__init__(port, connection_type=ConnectionType.UsbConnection)
        self.setName("CEL Robox")
        self.setShortDescription("Print with Robox")
        self.setDescription("Print with CEL Robox via USB")
        self.setIconName("print")
        self.setPriority(2)
        self._port = port
        self._is_printing = False
        self._proto = None
        self._accepts_commands = True
        self._pending_gcode = None
        self._total_lines = 0
        self._current_temps = {"n0": 0, "n1": 0, "bed": 0}
        self._current_line = 0
        self._relative_moves = False
        self._reel_temps = {}
        self._monitor_timer = None
        self._print_start_time = 0

        # Setup monitor view
        qml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MonitorItem.qml")
        if os.path.exists(qml_path):
            self._monitor_view_qml_path = qml_path

    def connectDevice(self):
        try:
            self._proto = RoboxProtocol()
            self._proto.connect(self._port)
            try:
                self._proto.execute_gcode("M104 S0")
            except Exception:
                pass
            try:
                self._proto.execute_gcode("M140 S0")
            except Exception:
                pass
            # Load tool 0 offsets from the head EEPROM (X=14, Y=4, Z=-0.4 on
            # this head). Without T0, the firmware uses offset 0 and the
            # physical limits are wrong (X clamps at 0 instead of -14), so
            # jog clamps and homing would not match the measured limits.
            try:
                self._proto.execute_gcode("T0")
            except Exception:
                pass
            # The E index wheel sensor is FAULTY on this machine (Eindex never
            # registers during extrusion). The firmware's slip detection relies
            # on it and falsely reports E_FILAMENT_SLIP (14) during normal
            # moves. M909 S100 T100 sets the slip threshold above 0x7fffffff, which
            # the firmware treats as "slip detection off" (motion.c:181).
            try:
                self._proto.execute_gcode("M909 S100 T100")
            except Exception:
                pass
            # Read reel EEPROM temperatures (material targets) - M150/154/157
            # equivalents via the USB reel EEPROM read. Used to sanity-check
            # the configured nozzle/bed temperatures against the reel chip.
            self._reel_temps = {}
            try:
                t = self._proto.get_reel_temperatures(1)  # E reel (slot 1)
                if t:
                    self._reel_temps = t
                    Logger.log("i", f"Reel E temps: {t}")
            except Exception:
                pass
            try:
                self._proto.clear_errors()
            except Exception:
                pass
            self._init_printer_model()
            self._start_monitor()
            self.setConnectionState(ConnectionState.Connected)
        except Exception as e:
            Logger.log("d", f"Robox connect failed: {e}")
            self.setConnectionState(ConnectionState.Disconnected)

    def _init_printer_model(self):
        container_stack = CuraApplication.getInstance().getGlobalContainerStack()
        extruders = 1
        if container_stack:
            extruders = container_stack.getProperty("machine_extruder_count", "value") or 1
        controller = GenericOutputController(self)
        self._printers = [PrinterOutputModel(output_controller=controller, number_of_extruders=extruders)]
        if container_stack:
            self._printers[0].updateName(container_stack.getName())

    def _start_monitor(self):
        if self._monitor_timer:
            return
        self._monitor_timer = QTimer()
        self._monitor_timer.setInterval(3000)
        self._monitor_timer.timeout.connect(self._update_monitor)
        self._monitor_timer.start()

    def _stop_monitor(self):
        if self._monitor_timer:
            self._monitor_timer.stop()
            self._monitor_timer = None

    def _update_monitor(self):
        try:
            if not self._proto:
                return
            t = self._proto.get_temperatures()
            if t:
                self._current_temps = t
                self._update_printer_model_temps(t)
                self._check_temp_safety(t)
        except Exception:
            pass

    def _check_temp_safety(self, t):
        if not self._is_printing:
            return
        try:
            bed_current = float(t.get("bed", 0))
            if not self._printers:
                return
            printer = self._printers[0]
            if not printer:
                return
            bed_target = printer.targetBedTemperature
            if bed_target <= 0:
                return
            
            if not hasattr(self, '_bed_overheat_start'):
                self._bed_overheat_start = None
            if not hasattr(self, '_last_bed_temp'):
                self._last_bed_temp = bed_current
            
            if bed_current > bed_target + 10:
                if self._bed_overheat_start is None:
                    self._bed_overheat_start = time.time()
                
                time_over = time.time() - self._bed_overheat_start
                temp_rising = bed_current > self._last_bed_temp
                
                if temp_rising or time_over > 30:
                    Logger.log("w", f"Robox: Bed temp runaway detected! Current={bed_current}C, Target={bed_target}C, Duration={time_over:.1f}s, Rising={temp_rising}")
                    try:
                        self._proto.execute_gcode("M140 S0")
                        self._proto.execute_gcode("M139 S0")
                        Message(
                            text=f"Bed temperature runaway detected!\nCurrent: {bed_current}°C\nTarget: {bed_target}°C\n\nHeaters turned off for safety.",
                            title="Robox - SAFETY WARNING",
                            message_type=Message.MessageType.ERROR
                        ).show()
                    except Exception:
                        pass
            else:
                self._bed_overheat_start = None
            
            self._last_bed_temp = bed_current
        except Exception:
            pass

    def _update_printer_model_temps(self, t):
        """Update Cura's printer model with current temperatures only.
        Target temps come from Cura's profile (set during preheat), not M105."""
        try:
            if not self._printers:
                return
            printer = self._printers[0]
            if not printer:
                return
            extruders = printer.extruders
            if extruders and len(extruders) > 0 and extruders[0]:
                extruders[0].updateHotendTemperature(float(t.get("n0", 0)))
            if extruders and len(extruders) > 1 and extruders[1]:
                extruders[1].updateHotendTemperature(float(t.get("n1", 0)))
            if printer:
                printer.updateBedTemperature(float(t.get("bed", 0)))
        except Exception:
            pass

    def requestWrite(self, nodes=None, file_name=None,
                     limit_mimetypes=False, file_handler=None,
                     filter_by_machine=False, **kwargs):
        if self._is_printing:
            Message(text="Already printing", title="Robox").show()
            return

        gcode_textio = StringIO()
        gcode_writer = cast(MeshWriter, PluginRegistry.getInstance().getPluginObject("GCodeWriter"))
        if not gcode_writer.write(gcode_textio, None):
            Message(text="Failed to generate G-code", title="Robox",
                    message_type=Message.MessageType.ERROR).show()
            return

        self._pending_gcode = gcode_textio.getvalue()

        stack = CuraApplication.getInstance().getGlobalContainerStack()
        nozzle_temp, bed_temp, material_name = get_material_info(stack)

        advisory = build_advisory(nozzle_temp, bed_temp, material_name)
        advisory += "\n\nReview settings in Monitor tab, then click Send to Printer."

        CuraApplication.getInstance().getController().setActiveStage("MonitorStage")
        Message(text=advisory, title="Robox - Ready to Print").show()

    def _run_print(self, gcode):
        self._is_printing = True
        try:
            import robox_postprocessor
            common = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")),
                                  "Documents", "CEL Robox", "Common")

            stack = CuraApplication.getInstance().getGlobalContainerStack()
            extruder_count = 1
            nozzle_temp = 210
            bed_temp = 60
            nozzle_size = 0.4
            if stack:
                extruder_count = stack.getProperty("machine_extruder_count", "value") or 1
                nozzle_temp = stack.getProperty("material_print_temperature", "value") or 210
                bed_temp = stack.getProperty("material_bed_temperature", "value") or 60
                nozzle_size = stack.getProperty("machine_nozzle_size", "value") or 0.4

            # Fall back to reel chip temperatures when Cura has no profile temps
            if self._reel_temps:
                reel_nozzle = self._reel_temps.get("nozzle", 0) or self._reel_temps.get("nozzle_first_layer", 0)
                reel_bed = self._reel_temps.get("bed", 0) or self._reel_temps.get("bed_first_layer", 0)
                if nozzle_temp <= 0 and reel_nozzle > 0:
                    nozzle_temp = reel_nozzle
                    Logger.log("i", f"Using reel nozzle temp: {nozzle_temp}")
                if bed_temp <= 0 and reel_bed > 0:
                    bed_temp = reel_bed
                    Logger.log("i", f"Using reel bed temp: {bed_temp}")

            head_type = "RBX01-SM"
            if extruder_count > 1:
                head_type = "RBX01-DM"

            use_nozzle0 = True
            use_nozzle1 = False

            for attempt in range(3):
                try:
                    if self._proto:
                        try:
                            self._proto.disconnect()
                        except Exception:
                            pass
                    self._proto = RoboxProtocol()
                    self._proto.connect(self._port)
                    fw = self._proto.get_firmware_version().strip("\x00").strip()
                    Logger.log("i", f"Robox connected FW:{fw}")

                    try:
                        self._proto.abort_print()
                    except Exception:
                        pass
                    try:
                        self._proto.clear_errors()
                    except Exception:
                        pass

                    try:
                        filament = self._proto.get_filament_status()
                        Logger.log("i", f"Filament: D(slot0)={filament['slot0_d']} E(slot1)={filament['slot1_e']}")
                        # SM head plumbing (position.c:561-565): E filament feeds
                        # nozzle 0 (LEFT), D filament feeds nozzle 1 (RIGHT).
                        # So filament in slot 1 (E) => nozzle 0, NOT nozzle 1.
                        if filament["slot1_e"]:
                            Logger.log("i", "E filament (slot 1) -> nozzle 0 (left, working nozzle)")
                            use_nozzle0 = True
                            use_nozzle1 = False
                        elif filament["slot0_d"]:
                            Logger.log("i", "D filament (slot 0) -> nozzle 1 (right, unfinished)")
                            use_nozzle0 = False
                            use_nozzle1 = True
                        else:
                            Logger.log("w", "No filament detected - defaulting to nozzle 0 (E path)")
                            use_nozzle0 = True
                            use_nozzle1 = False
                    except Exception as e:
                        Logger.log("d", f"Filament detect: {e}")
                        use_nozzle0 = True
                        use_nozzle1 = False

                    pp = robox_postprocessor.PostProcessor(
                        common, head_type=head_type,
                        use_nozzle0=use_nozzle0, use_nozzle1=use_nozzle1,
                        nozzle0_diameter=nozzle_size,
                        nozzle_temp=nozzle_temp,
                        bed_temp=bed_temp
                    )
                    processed = pp.process(gcode)
                    gcode_lines = [l.strip() for l in processed.split("\n")
                                   if l.strip() and not l.strip().startswith(";") and not l.strip().startswith("#")]
                    self._total_lines = len(gcode_lines)
                    Logger.log("i", f"Robox: {self._total_lines} G-code lines (nozzle0={use_nozzle0}, nozzle1={use_nozzle1})")

                    # Select tool 0 BEFORE homing - on SM heads, tool-1 EEPROM offsets
                    # are unprogrammed garbage which corrupt homing (Z reads 90mm and
                    # the Z axis crashes into the lid). T0 sets correct offsets from
                    # head EEPROM address 0x40. The B axis gate is functional now that
                    # the jammed plastic was removed, so T0 completes cleanly.
                    try:
                        self._proto.execute_gcode("T0")
                    except Exception:
                        pass
                    try:
                        self._proto.clear_errors()
                    except Exception:
                        pass

                    # Set preheat targets via execute_gcode. These are queued and processed
                    # by the firmware BEFORE the job file (maintain_job checks execute_buffer
                    # first). The before_print macros then wait for temps with M190/M109.
                    try:
                        self._proto.execute_gcode(f"M104 S{nozzle_temp}")
                    except Exception:
                        pass
                    try:
                        self._proto.execute_gcode(f"M103 S{nozzle_temp}")
                    except Exception:
                        pass
                    try:
                        self._proto.execute_gcode(f"M140 S{bed_temp}")
                    except Exception:
                        pass
                    try:
                        self._proto.execute_gcode(f"M139 S{bed_temp}")
                    except Exception:
                        pass

                    # Disable filament slip detection (faulty E index wheel on
                    # this machine) - see connectDevice for details
                    try:
                        self._proto.execute_gcode("M909 S100 T100")
                    except Exception:
                        pass

                    # Debug: Log first 20 lines of processed gcode
                    for i, line in enumerate(gcode_lines[:20]):
                        Logger.log("d", f"GCODE[{i}]: {line}")

                    if self._printers:
                        p = self._printers[0]
                        ex = p.extruders
                        if ex and len(ex) > 0:
                            ex[0].updateTargetHotendTemperature(float(nozzle_temp))
                        if ex and len(ex) > 1:
                            ex[1].updateTargetHotendTemperature(float(nozzle_temp))
                        p.updateTargetBedTemperature(float(bed_temp))
                    break
                except RoboxConnectionError as e:
                    if "Access is denied" in str(e):
                        raise RoboxError("USB port busy")
                    raise
                except Exception as e:
                    if "Timeout" in str(e) and attempt == 2:
                        raise RoboxError("Printer not responding")
                    if attempt < 2:
                        time.sleep(3)

            self._proto.start_data_file()

            try:
                t = self._proto.get_temperatures()
                if t:
                    self._current_temps = t
            except Exception:
                pass

            seq = -1
            buffer = ""
            for line in gcode_lines:
                line_to_add = line + "\r"
                if len(buffer) + len(line_to_add) > 512:
                    if buffer:
                        seq += 1
                        self._proto.send_data_chunk(seq, buffer)
                    buffer = line_to_add
                else:
                    buffer += line_to_add
            if buffer:
                seq += 1
                self._proto.end_data_file(seq, buffer.encode("ascii"))
            else:
                self._proto.end_data_file(seq, b"")

            self._proto.initiate_print()
            Logger.log("i", f"Robox print started: {seq+1} chunks")

            if self._printers:
                controller = self._printers[0].getController()
                job = PrintJobOutputModel(output_controller=controller, name="CEL Robox")
                job.updateState("printing")
                job.updateTimeElapsed(0)
                self._printers[0].updateActivePrintJob(job)

            self.writeFinished.emit(self)

            print_start = time.time()
            app = CuraApplication.getInstance()
            last_line = 0
            stall_count = 0
            for _ in range(600):
                try:
                    t = self._proto.get_temperatures()
                    if t:
                        self._current_temps = t
                        self._update_printer_model_temps(t)
                except Exception:
                    pass
                try:
                    s = self._proto.get_status()
                    # Print is complete when the firmware has processed all lines
                    if s.print_line_number >= self._total_lines - 1:
                        Logger.log("i", f"Robox print completed at line {s.print_line_number}/{self._total_lines}")
                        break
                    # Track stalls (e.g. M190/M109 waits) but allow up to 90s of no progress
                    if s.print_line_number == last_line:
                        stall_count += 1
                        if stall_count > 45:  # 90 seconds of no line progress
                            Logger.log("w", f"Robox: no progress for 90s at line {s.print_line_number}, still monitoring")
                            stall_count = 0
                    else:
                        stall_count = 0
                    last_line = s.print_line_number
                except Exception:
                    pass
                if app:
                    app.processEvents()
                time.sleep(2)

            self._start_monitor()

        except RoboxError as e:
            err = str(e)
            Logger.log("e", f"Robox print failed: {err}")
            Message(text=err, title="Robox", message_type=Message.MessageType.WARNING).show()
            self.writeError.emit(self)
        except Exception as e:
            err = f"Error: {e}"
            Logger.log("e", f"Robox print error: {err}")
            Message(text=err, title="Robox", message_type=Message.MessageType.ERROR).show()
            self.writeError.emit(self)
        finally:
            self._is_printing = False

    @pyqtSlot(str)
    def sendCommand(self, command):
        try:
            cmd_upper = command.strip().upper()
            if cmd_upper == "PRINT":
                if self._pending_gcode and not self._is_printing:
                    self.writeStarted.emit(self)
                    self._run_print(self._pending_gcode)
                    self._pending_gcode = None
                return
            if not self._proto:
                return
            if cmd_upper in ("COOLDOWN",):
                self._proto.execute_gcode("M104 S0")
                self._proto.execute_gcode("M140 S0")
                self._proto.clear_errors()
                return
            if cmd_upper == "LOAD_FILAMENT":
                self._load_filament()
                return
            if cmd_upper == "UNLOAD_FILAMENT":
                self._unload_filament()
                return
            if cmd_upper == "PURGE_NOZZLE":
                self._purge_nozzle()
                return
            if cmd_upper == "EJECT_STUCK_MATERIAL":
                self._eject_stuck_material()
                return
            if cmd_upper == "REMOVE_HEAD":
                self._remove_head()
                return
            # NOTE: do NOT send G28 B before commands - the B axis is never homed
            # on SM heads (home_distance[B_AXIS] = 0.0; unused) and G28 B does
            # nothing useful. Sending it before every command wastes time and
            # could interfere with homing/movement sequences.
            #
            # The X home switch is FAULTY on this machine (measured 2026-07-31:
            # M119 X:0 at the left stop). G28 X makes home_axis() grind the
            # motor until the 339mm abandon counter expires - the head bangs
            # into the left wall. Replace X homing with a clamped move to
            # X-100: firmware clamps carriage to [0,226] (nozzle -14), so this
            # safely drives to the left stop without relying on the switch.
            # CRITICAL: after G28 Z the nozzle sits at Z=0 (bed level) - any
            # X/Y move would drag it across the bed. Raise Z first (the
            # original Home_all_Axis_in_sequence macro does G0 Z10 after
            # G28 Z for exactly this reason).
            if cmd_upper in ("G28", "G28 X", "G28 X Y", "G28 X Y Z"):
                try:
                    self._proto.execute_gcode("G28 Y")
                except Exception:
                    pass
                try:
                    self._proto.execute_gcode("G28 Z")
                except Exception:
                    pass
                try:
                    self._proto.execute_gcode("G0 Z10 F600")
                except Exception:
                    pass
                try:
                    self._proto.execute_gcode("G0 X-100 F1500")
                except Exception:
                    pass
                return
            # Jog buttons send G91 -> G0 X±n Y±n Z±n -> G90. These are RELATIVE
            # moves that bypass the post-processor clamps, so the monitor jog
            # can drive into the left wall or the lid bracket. Intercept them,
            # apply the measured physical limits, and send a clamped absolute
            # move instead.
            if cmd_upper in ("G91", "G90"):
                self._relative_moves = (cmd_upper == "G91")
                self._proto.execute_gcode(command)
                return
            if cmd_upper.startswith("G0") or cmd_upper.startswith("G1"):
                clamped_commands = self._clamp_move_command(command)
                if clamped_commands is not None:
                    for c in clamped_commands:
                        self._proto.execute_gcode(c)
                    return
            # Temperature commands: Cura sends "M104 S{temp} T{position}".
            # The T parameter targets nozzle 1, which does NOT exist on the SM
            # head - T1 triggers NOZZLE1_THERMISTOR. Strip T and clamp S.
            if cmd_upper.startswith("M104") or cmd_upper.startswith("M103"):
                clean = re.sub(r"\bT\s*[0-9.]+", "", command, flags=re.IGNORECASE).strip()
                sm = re.search(r"\bS\s*(-?[0-9.]+)", clean, flags=re.IGNORECASE)
                if sm:
                    temp = float(sm.group(1))
                    temp = max(0.0, min(300.0, temp))
                    clean = re.sub(r"\bS\s*-?[0-9.]+", f"S{temp:g}", clean, flags=re.IGNORECASE)
                self._proto.execute_gcode(clean)
                return
            if cmd_upper.startswith("M140") or cmd_upper.startswith("M139"):
                sm = re.search(r"\bS\s*(-?[0-9.]+)", command, flags=re.IGNORECASE)
                if sm:
                    temp = float(sm.group(1))
                    temp = max(0.0, min(120.0, temp))
                    clean = re.sub(r"\bS\s*-?[0-9.]+", f"S{temp:g}", command, flags=re.IGNORECASE)
                    self._proto.execute_gcode(clean)
                else:
                    self._proto.execute_gcode(command)
                return
            self._proto.execute_gcode(command)
        except Exception as e:
            Logger.log("d", f"Robox sendCommand: {e}")

    def _clamp_move_command(self, command):
        """Clamp a G0/G1 move to the measured physical limits.
        Returns a list of gcode commands (clamped absolute move, with mode
        restore if needed), or None if the command has no X/Y/Z movement."""
        try:
            cmd = "G1" if command.strip().upper().startswith("G1") else "G0"
            xm = re.search(r"\bX(-?[0-9.]+)", command)
            ym = re.search(r"\bY(-?[0-9.]+)", command)
            zm = re.search(r"\bZ(-?[0-9.]+)", command)
            fm = re.search(r"\bF([0-9.]+)", command)
            if not (xm or ym or zm):
                return None
            dx = float(xm.group(1)) if xm else 0.0
            dy = float(ym.group(1)) if ym else 0.0
            dz = float(zm.group(1)) if zm else 0.0
            feed = fm.group(1) if fm else "3000"

            # Get current position from firmware (nozzle coordinates)
            cur_x, cur_y, cur_z = 0.0, 0.0, 0.0
            try:
                resp = self._proto.execute_gcode("M114", timeout=2)
                for part in resp.replace("\r\n", " ").split():
                    if part.startswith("X:"):
                        cur_x = float(part[2:])
                    elif part.startswith("Y:"):
                        cur_y = float(part[2:])
                    elif part.startswith("Z:"):
                        cur_z = float(part[2:])
            except Exception:
                pass

            if self._relative_moves:
                target_x = cur_x + dx
                target_y = cur_y + dy
                target_z = cur_z + dz
            else:
                target_x = dx
                target_y = dy
                target_z = dz

            # Measured physical limits (nozzle coordinates) from axis_limits.log
            target_x = max(-14.0, min(212.0, target_x))
            target_y = max(-4.0, min(151.0, target_y))
            # Coupled X+Z limit: Z capped at 40mm when X < 0 (lid bracket
            # collision at top-left corner), else 75mm (below 88mm lid contact)
            z_max = 40.0 if target_x < 0.0 else 75.0
            target_z = max(0.0, min(z_max, target_z))

            # Build the clamped absolute move. If we were in relative mode,
            # switch to absolute, move, then restore relative mode.
            commands = []
            if self._relative_moves:
                commands.append("G90")
            commands.append(f"{cmd} X{target_x:.2f} Y{target_y:.2f} Z{target_z:.2f} F{feed}")
            if self._relative_moves:
                commands.append("G91")
            return commands
        except Exception as e:
            Logger.log("d", f"Robox clamp move: {e}")
            return None

    def _safe_home_axes(self):
        """Home Y and Z via their (working) switches, then move X to the left
        stop with a clamped move and mark it homed with G92.

        This avoids G28 X entirely: the X home switch is faulty, and
        do_filament_load()/do_filament_unload() call do_homing("X") if X is not
        already homed - which would grind 339mm into the left wall. G92 sets
        homing_done[X]=1 (position.c set_position), so the firmware skips the
        dangerous X homing in M120/M121."""
        try:
            self._proto.execute_gcode("G28 Y")
        except Exception:
            pass
        try:
            self._proto.execute_gcode("G28 Z")
        except Exception:
            pass
        try:
            self._proto.execute_gcode("G0 Z10 F600")
        except Exception:
            pass
        try:
            self._proto.execute_gcode("G0 X-100 F1500")
        except Exception:
            pass
        try:
            self._proto.execute_gcode("G92 X-14")
        except Exception:
            pass
        try:
            self._proto.clear_errors()
        except Exception:
            pass

    def _load_filament(self):
        """Load E filament: safe home, heat to unload/load temperature, M120 E.
        Runs in a background thread so the UI stays responsive."""
        threading.Thread(target=self._load_filament_worker, daemon=True).start()

    def _load_filament_worker(self):
        try:
            self._safe_home_axes()
            # Firmware load sequence (do_filament_load, position.c:497):
            # grab_filament -> move X to load position -> load_filament ->
            # move_filament_until_slip. The last stage pushes filament until
            # slip is detected, i.e. until the filament reaches the nozzle.
            # M120 E handles all of this internally.
            #
            # Temperature: the nozzle must be hot enough that the filament can
            # be pushed through. Use the printing temperature for reliability.
            self._proto.execute_gcode("M104 S230")
            # Wait for nozzle to reach temperature
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("n0", 0)) >= 225:
                        break
                except Exception:
                    pass
                time.sleep(3)
            # Disable slip detection (faulty index wheel) - also the T
            # threshold used during the load/until-slip phase
            self._proto.execute_gcode("M909 S100 T100")
            # M120 E: grab filament, move to load position, push until slip.
            # If it slips (no filament at the gear yet), push filament forward
            # first so the gear can grab it, then retry.
            for attempt in range(3):
                self._proto.execute_gcode("M120 E")
                time.sleep(20)
                e = self._proto.report_errors()
                crit = self._critical_errors(e)
                if not crit:
                    self._wait_motion_idle(timeout=120)
                    Message(
                        text="Filament loaded. Ready to print.",
                        title="Robox - Load Filament"
                    ).show()
                    return
                Logger.log("w", f"Robox load attempt {attempt+1} errors: {crit}")
                # Push filament forward to seat it in the gear, then retry
                self._proto.clear_errors()
                self._proto.execute_gcode("G91")
                self._proto.execute_gcode("G1 E300 F2000")
                time.sleep(10)
                self._proto.execute_gcode("G90")
            Message(
                text=f"Filament load reported errors: {crit}\nCheck the filament is inserted in the E (slot 1, right side facing front) path.",
                title="Robox - Load Filament",
                message_type=Message.MessageType.WARNING
            ).show()
        except Exception as ex:
            Logger.log("d", f"Robox load filament: {ex}")

    def _unload_filament(self):
        """Unload E filament: safe home, pre-heat to 140C (UNLOAD_NOZZLE_
        TEMPERATURE), M122 E (unload WITHOUT pause - M121 would park first,
        moving the faulty B gate and risking a jam), then extra reverse pulls
        to fully free the filament from the gear.
        Runs in a background thread so the UI stays responsive."""
        threading.Thread(target=self._unload_filament_worker, daemon=True).start()

    def _unload_filament_worker(self):
        try:
            self._safe_home_axes()

            # Firmware do_filament_unload (position.c:495) expects the nozzle at
            # UNLOAD_NOZZLE_TEMPERATURE (140C). Pre-heat and wait so the
            # firmware's internal heat_for_filament_unload() is quick.
            self._proto.execute_gcode("M104 S140")
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    n0 = float(t.get("n0", 0))
                    if 130 <= n0 <= 150:
                        break
                except Exception:
                    pass
                time.sleep(3)

            # Disable slip detection (faulty index wheel) - unload uses the
            # until-slip mechanism (T threshold) and normal moves (S threshold)
            self._proto.execute_gcode("M909 S100 T100")

            # M122 E: unload WITHOUT pause. M121 would call pause_and_park()
            # first, which moves the B axis gate (faulty on this machine) and
            # can jam with B_STUCK/B_POSITION_LOST. M122 skips the park step.
            # The firmware then reverses FILAMENT_UNLOAD_DISTANCE (35mm) until
            # slip is detected.
            self._proto.execute_gcode("M122 E")
            time.sleep(20)
            self._wait_motion_idle(timeout=120)

            # The firmware only reverses 35mm - pull much more so the filament
            # tip comes fully clear of the extruder gear (proven sequence).
            self._proto.execute_gcode("G91")
            for _ in range(4):
                self._proto.execute_gcode("G1 E-400 F3000")
                time.sleep(6)
            self._proto.execute_gcode("G90")
            self._wait_motion_idle(timeout=120)

            e = self._proto.report_errors()
            crit = self._critical_errors(e)
            if crit:
                Logger.log("w", f"Robox unload filament errors: {crit}")
                Message(
                    text=f"Filament unload reported errors: {crit}\nThe filament may be stuck - try gently pulling it from the reel.",
                    title="Robox - Unload Filament",
                    message_type=Message.MessageType.WARNING
                ).show()
            else:
                Message(
                    text="Filament unloaded. You can remove the reel.",
                    title="Robox - Unload Filament"
                ).show()
        except Exception as ex:
            Logger.log("d", f"Robox unload filament: {ex}")

    def _wait_motion_idle(self, timeout=120):
        """Wait until the motion buffer is empty by polling M114 until the
        position stops changing. Commands are queued asynchronously by the
        firmware, so fixed sleeps can fire the 'complete' message while moves
        are still physically executing."""
        last = None
        stable = 0
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = self._proto.execute_gcode("M114", timeout=2)
                x = y = z = 0.0
                for part in resp.replace("\r\n", " ").split():
                    if part.startswith("X:"):
                        x = float(part[2:])
                    elif part.startswith("Y:"):
                        y = float(part[2:])
                    elif part.startswith("Z:"):
                        z = float(part[2:])
                pos = (x, y, z)
                if last is not None and all(abs(a - b) < 0.05 for a, b in zip(pos, last)):
                    stable += 1
                else:
                    stable = 0
                last = pos
                if stable >= 4:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    # Errors that must NOT abort maintenance operations. From the firmware's
    # ERROR_PAUSE_MASK (command.h:107): B_POSITION_WARNING (33), B_POSITION_LOST
    # (25), POWEROFF_WHILST_HOT (30), E/D_NO_FILAMENT (31/32) must not cause a
    # pause. E/D_FILAMENT_SLIP (14/15) are disabled anyway via M909. Only
    # genuinely critical errors (thermistors, drivers, overtemp) should abort.
    _NON_FATAL_ERRORS = {25, 30, 31, 32, 33}

    def _critical_errors(self, errors):
        if not errors:
            return []
        return [e for e in errors if e not in self._NON_FATAL_ERRORS]

    def _purge_nozzle(self):
        """Purge the working nozzle (nozzle 0, left - E filament path on SM
        head). Heats to printing temperature, selects T0, moves to the purge
        position (X11 - nozzle 0 over the rubber wiper, per Short_Purge#N0),
        then pushes filament with G36 until slip is detected to clear old
        material. Runs in a background thread."""
        threading.Thread(target=self._purge_nozzle_worker, daemon=True).start()

    def _purge_nozzle_worker(self):
        try:
            self._safe_home_axes()

            # Official PurgeMaterial sequence (PurgeMaterial.gcode), adapted
            # for the SM head: E filament feeds nozzle 0 only, so use the
            # Purge_T0 pattern (nozzle 0) for all purge lines.
            # 1. Bed to 90C, nozzle to 240C (ABS)
            # 2. Level gantry
            # 3. G36 un-park/purge at the wiper
            # 4. 8 purge lines across the bed (Purge_T0 pattern)
            # 5. Cool down, unlock door
            self._proto.execute_gcode("M140 S90")
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("bed", 0)) >= 85:
                        break
                except Exception:
                    pass
                time.sleep(3)

            self._proto.execute_gcode("M104 S240")
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("n0", 0)) >= 235:
                        break
                except Exception:
                    pass
                time.sleep(3)

            # Disable slip detection (faulty index wheel)
            self._proto.execute_gcode("M909 S100 T100")

            # Level gantry (2-points, from the macro)
            self._proto.execute_gcode("G90")
            self._proto.execute_gcode("G0 X20 Y75 F2000")
            time.sleep(4)
            self._proto.execute_gcode("G28 Z")
            time.sleep(8)
            self._proto.execute_gcode("G0 Z4 F600")
            time.sleep(3)
            self._proto.execute_gcode("G0 X190 Y75 F2000")
            time.sleep(5)
            self._proto.execute_gcode("G28 Z")
            time.sleep(8)
            self._proto.execute_gcode("G0 Z4 F600")
            time.sleep(3)
            self._proto.execute_gcode("G38")
            time.sleep(3)
            self._proto.clear_errors()

            # Head LED on, fan on
            self._proto.execute_gcode("M129")
            self._proto.execute_gcode("M106")
            time.sleep(2)

            # Un-park: push filament until slip at the wiper position
            # (G36 E1500 F400 from PurgeMaterial, reduced to avoid gate stress)
            self._proto.execute_gcode("G0 Y-6 X11 Z8 F2000")
            time.sleep(5)
            self._proto.execute_gcode("G0 Z4 F600")
            time.sleep(3)
            self._proto.execute_gcode("G1 Y-4 F400")
            time.sleep(3)
            self._proto.execute_gcode("G36 E400 F400")
            time.sleep(15)
            e = self._proto.report_errors()
            crit = self._critical_errors(e)
            if crit:
                Logger.log("w", f"Robox purge un-park critical errors: {crit}")
            elif e:
                Logger.log("d", f"Robox purge un-park warnings (ignored): {e}")
            self._proto.clear_errors()

            # Purge lines: Purge_T0 pattern (nozzle 0) at 8 Y positions
            # T0 / Z0.3 / B1 / E15 / Z0.8 / X20->X190 E80 / Z0.3 / E10 / B0 / Z8
            purge_ok = True
            for line_idx in range(8):
                y_pos = 20 + (line_idx * 4)
                self._proto.clear_errors()
                self._proto.execute_gcode(f"G0 X20 Y{y_pos} Z8 F2000")
                time.sleep(3)
                self._proto.execute_gcode("T0")
                time.sleep(2)
                self._proto.execute_gcode("G0 Z0.3 F600")
                time.sleep(3)
                self._proto.execute_gcode("G0 B1")
                time.sleep(1)
                self._proto.execute_gcode("G1 E15 F100")
                time.sleep(8)
                self._proto.execute_gcode("G0 Z0.8 F600")
                time.sleep(2)
                self._proto.execute_gcode("G1 X190 E80 F800")
                time.sleep(15)
                self._proto.execute_gcode("G0 Z0.3 F600")
                time.sleep(2)
                self._proto.execute_gcode("G1 E10 F100")
                time.sleep(6)
                self._proto.execute_gcode("G1 E5 B0 F100")
                time.sleep(4)
                self._proto.execute_gcode("G0 Z8 F600")
                time.sleep(2)
                e = self._proto.report_errors()
                crit = self._critical_errors(e)
                if crit:
                    Logger.log("w", f"Robox purge line {line_idx+1} critical errors: {crit}")
                    purge_ok = False
                    break
                elif e:
                    Logger.log("d", f"Robox purge line {line_idx+1} warnings (ignored): {e}")
                self._proto.clear_errors()

            # Cool down and unlock the door
            self._proto.execute_gcode("M104 S0")
            self._proto.execute_gcode("M140 S0")
            self._proto.execute_gcode("M107")
            self._proto.execute_gcode("M128")
            time.sleep(5)
            try:
                self._proto.execute_gcode("G37")
            except Exception:
                pass

            # Wait for all queued moves to finish before reporting complete
            self._wait_motion_idle(timeout=120)

            e = self._proto.report_errors()
            crit = self._critical_errors(e)
            if crit:
                Logger.log("w", f"Robox purge critical errors: {crit}")
                Message(
                    text=f"Purge reported critical errors: {crit}\nCheck the nozzle for blockage.",
                    title="Robox - Purge Nozzle",
                    message_type=Message.MessageType.WARNING
                ).show()
            elif not purge_ok:
                Message(
                    text="Purge line failed - the nozzle may be blocked. Check the lines and re-purge.",
                    title="Robox - Purge Nozzle",
                    message_type=Message.MessageType.WARNING
                ).show()
            else:
                Message(
                    text="Purge complete.\nCheck the two test lines on the bed: if the last lines show old colour or debris, purge again.",
                    title="Robox - Purge Nozzle"
                ).show()
        except Exception as ex:
            Logger.log("d", f"Robox purge nozzle: {ex}")

    def _eject_stuck_material(self):
        """Official eject-stuck-material sequence (eject_stuck_material#RBX01-SM
        macro): purge both nozzle paths, heat to 140C, create a filament neck
        (E-50), cool to 125C, then eject (E-1500). Runs in a background thread."""
        threading.Thread(target=self._eject_stuck_material_worker, daemon=True).start()

    def _eject_stuck_material_worker(self):
        try:
            self._safe_home_axes()

            # Set & heat first layer nozzle temp (macro M103 S with temp)
            self._proto.execute_gcode("M103 S230")
            self._proto.execute_gcode("M106")  # Fan on full
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("n0", 0)) >= 225:
                        break
                except Exception:
                    pass
                time.sleep(3)
            self._proto.execute_gcode("G4 S5")  # Dwell 5s
            self._proto.execute_gcode("M129")  # Head LED on
            self._proto.execute_gcode("M909 S100 T100")

            # Short_Purge#N0 (nozzle 0, X11)
            self._proto.execute_gcode("G90")
            self._proto.execute_gcode("G0 Y-6 X11 Z8 F2000")
            time.sleep(5)
            self._proto.execute_gcode("T0")
            time.sleep(2)
            self._proto.execute_gcode("G0 Z4 F600")
            time.sleep(3)
            self._proto.execute_gcode("G1 Y-4 F400")
            time.sleep(3)
            self._proto.execute_gcode("G36 E400 F400")
            time.sleep(15)
            self._proto.execute_gcode("G0 B1")
            time.sleep(2)
            self._proto.execute_gcode("G1 E2 F200")
            time.sleep(3)
            self._proto.execute_gcode("G1 E30 X23 F100")
            time.sleep(10)
            self._proto.execute_gcode("G0 B0")
            time.sleep(2)
            self._proto.clear_errors()

            # Eject sequence: neck then snap
            self._proto.execute_gcode("M104 S140")
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("n0", 0)) <= 150 and float(t.get("n0", 0)) >= 135:
                        break
                except Exception:
                    pass
                time.sleep(3)
            self._proto.execute_gcode("G91")
            self._proto.execute_gcode("G0 E-50")  # Create neck
            time.sleep(5)
            self._proto.execute_gcode("M104 S125")  # Snap temperature
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("n0", 0)) <= 130:
                        break
                except Exception:
                    pass
                time.sleep(3)
            self._proto.execute_gcode("G0 E-1500")  # Eject
            time.sleep(20)
            self._proto.execute_gcode("G90")

            self._wait_motion_idle(timeout=120)
            self._proto.execute_gcode("M104 S0")
            self._proto.execute_gcode("M140 S0")
            self._proto.execute_gcode("M107")
            self._proto.execute_gcode("M128")

            e = self._proto.report_errors()
            crit = self._critical_errors(e)
            if crit:
                Logger.log("w", f"Robox eject errors: {crit}")
                Message(
                    text=f"Eject reported errors: {crit}\nThe filament may be very stuck.",
                    title="Robox - Eject Stuck Material",
                    message_type=Message.MessageType.WARNING
                ).show()
            else:
                Message(
                    text="Eject complete. The filament should now be free - pull it from the reel.",
                    title="Robox - Eject Stuck Material"
                ).show()
        except Exception as ex:
            Logger.log("d", f"Robox eject stuck material: {ex}")

    def _remove_head(self):
        """Official Remove_Head sequence: home, move to eject position, eject
        all material, open door. Runs in a background thread."""
        threading.Thread(target=self._remove_head_worker, daemon=True).start()

    def _remove_head_worker(self):
        try:
            self._safe_home_axes()

            # Move to eject position (macro: T0 B0, G0 X210 Y120 Z8)
            self._proto.execute_gcode("G90")
            self._proto.execute_gcode("T0")
            time.sleep(2)
            self._proto.execute_gcode("G0 B0")
            time.sleep(2)
            self._proto.execute_gcode("G0 X210 Y120 Z8 F2000")
            time.sleep(6)
            self._proto.execute_gcode("M909 S100 T100")

            # Eject all material (macro: heat 160C, neck E-50, snap 125C, eject E-1500)
            self._proto.execute_gcode("M104 S160")
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("n0", 0)) >= 155:
                        break
                except Exception:
                    pass
                time.sleep(3)
            self._proto.execute_gcode("G91")
            self._proto.execute_gcode("G0 E-50")
            time.sleep(5)
            self._proto.execute_gcode("M104 S125")
            for _ in range(60):
                try:
                    t = self._proto.get_temperatures()
                    if float(t.get("n0", 0)) <= 130:
                        break
                except Exception:
                    pass
                time.sleep(3)
            self._proto.execute_gcode("G0 E-1500")
            time.sleep(20)
            self._proto.execute_gcode("G90")

            # Fan on, open door, fan off (macro)
            self._proto.execute_gcode("M106")
            time.sleep(2)
            self._proto.execute_gcode("G37")
            time.sleep(3)
            self._proto.execute_gcode("M107")

            self._wait_motion_idle(timeout=120)
            self._proto.execute_gcode("M104 S0")
            self._proto.execute_gcode("M140 S0")

            e = self._proto.report_errors()
            crit = self._critical_errors(e)
            if crit:
                Logger.log("w", f"Robox remove head errors: {crit}")
                Message(
                    text=f"Remove Head reported errors: {crit}",
                    title="Robox - Remove Head",
                    message_type=Message.MessageType.WARNING
                ).show()
            else:
                Message(
                    text="Head ready to remove. The door is open - disconnect the head when safe.",
                    title="Robox - Remove Head"
                ).show()
        except Exception as ex:
            Logger.log("d", f"Robox remove head: {ex}")

    def close(self):
        self._stop_monitor()
        if self._proto:
            try:
                self._proto.disconnect()
            except Exception:
                pass
        super().close()

    def pausePrint(self):
        if self._proto:
            try:
                self._proto.pause_resume(pause=True)
            except Exception:
                pass

    def resumePrint(self):
        if self._proto:
            try:
                self._proto.pause_resume(pause=False)
            except Exception:
                pass

    def cancelPrint(self):
        self._is_printing = False
        self._stop_monitor()
        if self._proto:
            try:
                self._proto.abort_print()
            except Exception:
                pass


class RoboxOutputDevicePlugin(QObject, OutputDevicePlugin):
    addDeviceSignal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._device = None
        self._device_lock = threading.Lock()
        self._check_updates = True
        self.addDeviceSignal.connect(self._addDevice)

    def start(self):
        self._check_updates = True
        thread = threading.Thread(target=self._detection_loop, daemon=True)
        thread.start()

    def _addDevice(self, port):
        if self._device:
            return
        device = RoboxPrinterDevice(port)
        device.connectionStateChanged.connect(self._onStateChanged)
        self._device = device
        device.connectDevice()

    def _onStateChanged(self):
        if not self._device:
            return
        if self._device.connectionState == ConnectionState.Connected:
            try:
                self.getOutputDeviceManager().addOutputDevice(self._device)
                Logger.log("i", "Robox print button added")
            except Exception as e:
                Logger.log("d", f"Robox add: {e}")
        else:
            try:
                self.getOutputDeviceManager().removeOutputDevice(self._device._port)
            except Exception:
                pass

    def _detection_loop(self):
        while self._check_updates:
            try:
                for p in serial.tools.list_ports.comports():
                    if p.vid == ROBOX_VID and p.pid == ROBOX_PID:
                        with self._device_lock:
                            if self._device is None and self._check_updates:
                                Logger.log("i", f"Robox found on {p.device}")
                                try:
                                    self.addDeviceSignal.emit(p.device)
                                except RuntimeError:
                                    pass
                        break
                else:
                    if self._device is not None:
                        dev = None
                        with self._device_lock:
                            if self._device is not None:
                                dev = self._device
                                self._device = None
                        if dev:
                            dev.close()
                            try:
                                mgr = self.getOutputDeviceManager()
                                if mgr:
                                    mgr.removeOutputDevice(dev._port)
                            except Exception:
                                pass
            except Exception as e:
                Logger.log("d", f"Robox detection: {e}")
            if not self._check_updates:
                break
            for _ in range(50):
                if not self._check_updates:
                    break
                try:
                    time.sleep(0.1)
                except Exception:
                    break

    def stop(self):
        self._check_updates = False
        if self._device:
            self._device.close()
