import os
import sys
import threading
import time
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


class RoboxPrinterDevice(PrinterOutputDevice):
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
        self.acceptsCommandsChanged.emit()
        self._total_lines = 0
        self._current_temps = {"n0": 0, "n1": 0, "bed": 0}
        self._current_line = 0
        self._monitor_timer = None

        # Setup monitor view
        qml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MonitorItem.qml")
        if os.path.exists(qml_path):
            self._monitor_view_qml_path = qml_path

    def connectDevice(self):
        try:
            self._proto = RoboxProtocol()
            self._proto.connect(self._port)
            # Ensure heaters are off on connection (safety)
            try: self._proto.execute_gcode("M104 S0")
            except: pass
            try: self._proto.execute_gcode("M140 S0")
            except: pass
            self._init_printer_model()
            self._start_monitor()
            self.setConnectionState(ConnectionState.Connected)
        except Exception as e:
            Logger.log("d", f"Robox connect failed: {e}")
            self.setConnectionState(ConnectionState.Disconnected)

    def _start_monitor(self):
        if self._monitor_timer:
            return
        self._monitor_timer = QTimer()
        self._monitor_timer.setInterval(2000)
        self._monitor_timer.timeout.connect(self._update_monitor)
        self._monitor_timer.start()

    def _update_monitor(self):
        try:
            if not self._proto:
                return
            t = self._proto.get_temperatures()
            if t:
                self._current_temps = t
                self._update_printer_model_temps(t)
        except Exception:
            pass

    def _init_printer_model(self):
        container_stack = CuraApplication.getInstance().getGlobalContainerStack()
        extruders = 1
        if container_stack:
            extruders = container_stack.getProperty("machine_extruder_count", "value") or 1
        controller = GenericOutputController(self)
        self._printers = [PrinterOutputModel(output_controller=controller, number_of_extruders=extruders)]
        if container_stack:
            self._printers[0].updateName(container_stack.getName())

    def requestWrite(self, nodes=None, file_name=None,
                     limit_mimetypes=False, file_handler=None,
                     filter_by_machine=False, **kwargs):
        if self._is_printing:
            Message(text="Already printing", title="Robox").show()
            return
        self._stop_monitor()
        self.writeStarted.emit(self)
        CuraApplication.getInstance().getController().setActiveStage("MonitorStage")
        gcode_textio = StringIO()
        gcode_writer = cast(MeshWriter, PluginRegistry.getInstance().getPluginObject("GCodeWriter"))
        if not gcode_writer.write(gcode_textio, None):
            Message(text="Failed to generate G-code", title="Robox",
                    message_type=Message.MessageType.ERROR).show()
            return
        # Run on main thread to avoid all Qt threading issues
        self._run_print(gcode_textio.getvalue())

    def _run_print(self, gcode):
        self._is_printing = True
        try:
            import robox_postprocessor
            common = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")),
                                  "Documents", "CEL Robox", "Common")

            # Read material settings from Cura's active profile
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

            # Default to single material head. Dual material only with 2 extruders.
            head_type = "RBX01-SM"
            if extruder_count > 1:
                head_type = "RBX01-DM"

            pp = robox_postprocessor.PostProcessor(
                common, head_type=head_type,
                nozzle0_diameter=nozzle_size,
                nozzle_temp=nozzle_temp,
                bed_temp=bed_temp
            )
            processed = pp.process(gcode)
            gcode_lines = [l.strip() for l in processed.split("\n")
                           if l.strip() and not l.strip().startswith(";") and not l.strip().startswith("#")]
            self._total_lines = len(gcode_lines)
            Logger.log("i", f"Robox: {self._total_lines} G-code lines")

            for attempt in range(3):
                try:
                    if self._proto:
                        try: self._proto.disconnect()
                        except: pass
                    self._proto = RoboxProtocol()
                    self._proto.connect(self._port)
                    fw = self._proto.get_firmware_version().strip("\x00").strip()
                    Logger.log("i", f"Robox connected FW:{fw}")

                    try: self._proto.abort_print()
                    except: pass
                    try: self._proto.clear_errors()
                    except: pass
                    try: self._proto.execute_gcode("G28 B")
                    except: pass

                    # Auto-detect filament slot
                    try:
                        filament = self._proto.get_filament_status()
                        Logger.log("i", f"Filament: D(slot0)={filament['slot0_d']} E(slot1)={filament['slot1_e']}")
                        if filament["slot0_d"] and not filament["slot1_e"]:
                            Logger.log("i", "Using nozzle 0 (D extruder, slot 0)")
                        elif filament["slot1_e"] and not filament["slot0_d"]:
                            Logger.log("i", "Using nozzle 1 (E extruder, slot 1)")
                            # Re-process G-code with nozzle 1
                            pp2 = robox_postprocessor.PostProcessor(
                                common, head_type=head_type,
                                use_nozzle0=False, use_nozzle1=True,
                                nozzle0_diameter=nozzle_size,
                                nozzle_temp=nozzle_temp,
                                bed_temp=bed_temp
                            )
                            processed = pp2.process(gcode)
                            gcode_lines = [l.strip() for l in processed.split("\n")
                                           if l.strip() and not l.strip().startswith(";") and not l.strip().startswith("#")]
                            self._total_lines = len(gcode_lines)
                            Logger.log("i", f"Re-processed for nozzle 1: {self._total_lines} lines")
                        elif filament["slot0_d"] and filament["slot1_e"]:
                            Logger.log("i", "Both slots have filament - using nozzle 0 (D extruder)")
                        else:
                            Logger.log("w", "No filament detected in either slot")
                    except Exception as e:
                        Logger.log("d", f"Filament detect: {e}")

                    # Preheat - set BOTH standard and first-layer temps
                    try: self._proto.execute_gcode(f"M104 S{nozzle_temp}")
                    except: pass
                    try: self._proto.execute_gcode(f"M103 S{nozzle_temp}")
                    except: pass
                    try: self._proto.execute_gcode(f"M140 S{bed_temp}")
                    except: pass
                    try: self._proto.execute_gcode(f"M139 S{bed_temp}")
                    except: pass
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

            self._proto.start_data_file(is_job=True)

            # Update initial temps before upload so monitor shows them
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

            # Monitor heating/progress while keeping UI responsive
            # Use processEvents to keep Cura responsive during heating
            print_start = time.time()
            app = CuraApplication.getInstance()
            last_line = 0
            stable_count = 0
            for _ in range(300):  # Max 10 minutes monitoring
                try:
                    t = self._proto.get_temperatures()
                    if t:
                        self._current_temps = t
                        self._update_printer_model_temps(t)
                except Exception:
                    pass

                # Update elapsed time on the print job
                try:
                    if self._printers and hasattr(self._printers[0], '_active_print_job') and self._printers[0]._active_print_job:
                        elapsed = int(time.time() - print_start)
                        self._printers[0]._active_print_job.updateTimeElapsed(elapsed)
                except Exception:
                    pass

                # Check if print completed (line number stopped changing)
                try:
                    s = self._proto.get_status()
                    if s.print_line_number > 0:
                        if s.print_line_number == last_line:
                            stable_count += 1
                        else:
                            stable_count = 0
                        last_line = s.print_line_number
                    if stable_count > 10:  # 20 seconds stable = print done
                        Logger.log("i", f"Robox print completed at line {last_line}")
                        break
                except Exception:
                    pass

                # Keep UI responsive
                if app:
                    app.processEvents()
                time.sleep(2)

            # After monitoring loop, restart QTimer for ongoing temp display
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
    def sendCommand(self, command: str) -> None:
        if self._proto:
            try:
                cmd_upper = command.strip().upper()
                if cmd_upper in ("COOLDOWN",):
                    self._proto.execute_gcode("M104 S0")
                    self._proto.execute_gcode("M140 S0")
                    self._proto.clear_errors()
                    return
                if any(cmd_upper.startswith(p) for p in ("G0", "G1", "G28", "G37", "G91", "G90")):
                    try: self._proto.execute_gcode("G28 B")
                    except: pass
                self._proto.execute_gcode(command)
            except Exception as e:
                Logger.log("d", f"Robox sendCommand: {e}")

    def _update_printer_model_temps(self, t):
        """Update Cura's printer model with current temperatures."""
        try:
            if not self._printers:
                return
            printer = self._printers[0]
            if not printer:
                return
            extruders = printer.extruders
            if extruders and len(extruders) > 0 and extruders[0]:
                extruders[0].updateHotendTemperature(float(t.get("n0", 0)))
                tn0 = t.get("target_n0")
                if tn0 is not None:
                    extruders[0].updateTargetHotendTemperature(float(tn0))
            if extruders and len(extruders) > 1 and extruders[1]:
                extruders[1].updateHotendTemperature(float(t.get("n1", 0)))
                tn1 = t.get("target_n1")
                if tn1 is not None:
                    extruders[1].updateTargetHotendTemperature(float(tn1))
            if printer:
                printer.updateBedTemperature(float(t.get("bed", 0)))
                tb = t.get("target_bed")
                if tb is not None:
                    printer.updateTargetBedTemperature(float(tb))
        except Exception:
            pass

    def _stop_monitor(self):
        if self._monitor_timer:
            self._monitor_timer.stop()
            self._monitor_timer = None

    def close(self):
        self._stop_monitor()
        if self._proto:
            try: self._proto.disconnect()
            except: pass
        super().close()

    def pausePrint(self):
        if self._proto:
            try: self._proto.pause_resume(pause=True)
            except: pass

    def resumePrint(self):
        if self._proto:
            try: self._proto.pause_resume(pause=False)
            except: pass

    def cancelPrint(self):
        self._is_printing = False
        self._stop_monitor()
        if self._proto:
            try: self._proto.abort_print()
            except: pass


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
                try: time.sleep(0.1)
                except: break

    def stop(self):
        self._check_updates = False
        if self._device:
            self._device.close()
