import sys, os, threading, time, queue, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from robox_protocol import RoboxProtocol, RoboxError

# Safe travel limits from printer profile + M533 Z switch position
MAX_STEP = {"x": 50, "y": 50, "z": 25}

try:
    import tkinter as tk
    from tkinter import messagebox
except ImportError:
    tk = None


class RoboxControl:
    def __init__(self):
        self.proto = None
        self._connected = False
        self._cmd_queue = queue.Queue()
        self._worker = None
        self._running = False

    def connect(self):
        self.proto = RoboxProtocol()
        self.proto.connect()
        time.sleep(0.5)
        self._connected = True
        # Home B axis to prevent head lifter jamming
        try:
            self.proto.execute_gcode("G28 B")
        except Exception:
            pass
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _worker_loop(self):
        while self._running and self._connected:
            try:
                cmd, args, kwargs, result_q = self._cmd_queue.get(timeout=0.5)
                try:
                    fn = getattr(self.proto, cmd)
                    result = fn(*args, **kwargs)
                    if result_q:
                        result_q.put(("ok", result))
                except Exception as e:
                    if result_q:
                        result_q.put(("error", str(e)))
            except queue.Empty:
                pass

    def _submit(self, cmd, *args, **kwargs):
        result_q = queue.Queue()
        self._cmd_queue.put((cmd, args, kwargs, result_q))
        try:
            status, result = result_q.get(timeout=30)
            if status == "error":
                raise RoboxError(result)
            return result
        except queue.Empty:
            raise RoboxError("Command timed out")

    def disconnect(self):
        self._running = False
        if self.proto:
            try: self.proto.disconnect()
            except: pass
        self.proto = None
        self._connected = False

    @property
    def connected(self):
        return self._connected and self.proto is not None

    def status(self):
        return self.proto.get_status()

    def firmware(self):
        return self._submit("get_firmware_version")

    def printer_id(self):
        return self._submit("get_printer_id")

    def gcode(self, cmd):
        return self._submit("execute_gcode", cmd)

    def open_door(self):
        self.gcode("G37")

    def home(self, axes):
        for a in axes:
            self.gcode(f"G28 {a}")

    def move(self, x=None, y=None, z=None, f=1500):
        # Relative jog with safe step size limits
        if x is not None:
            x = max(min(x, 50), -50)
        if y is not None:
            y = max(min(y, 50), -50)
        if z is not None:
            z = max(min(z, 25), -25)
        self.gcode("G91")
        cmd = "G0"
        if x is not None: cmd += f" X{x}"
        if y is not None: cmd += f" Y{y}"
        if z is not None: cmd += f" Z{z}"
        cmd += f" F{f}"
        self.gcode(cmd)
        self.gcode("G90")

    def set_temp(self, n0=None, n1=None, bed=None):
        if n0 is not None and n0 > 0:
            self.gcode(f"M104 S{min(n0, 300)}")
        elif n0 is not None:
            self.gcode("M104 S0")
        if n1 is not None and n1 > 0:
            self.gcode(f"M104 S{min(n1, 300)}")  # same nozzle, different command
        if bed is not None and bed > 0:
            self.gcode(f"M140 S{min(bed, 120)}")
        elif bed is not None:
            self.gcode("M140 S0")

    def extrude(self, length=10, speed=300):
        self.gcode("M83"); self.gcode(f"G1 E{length} F{speed}"); self.gcode("G90")

    def retract(self, length=10, speed=300):
        self.gcode("M83"); self.gcode(f"G1 E-{length} F{speed}"); self.gcode("G90")

    def head_light(self, on=True):
        self.gcode("M129" if on else "M128")

    def fan(self, speed=0):
        self.gcode(f"M106 S{speed}") if speed > 0 else self.gcode("M107")

    def motors_off(self):
        self.gcode("M84")

    def get_temps(self):
        try:
            resp = self._submit("execute_gcode", "M105")
            if not resp:
                return {"n0": 0, "n1": 0, "bed": 0}
            temps = {"n0": 0, "n1": 0, "bed": 0}
            for p in resp.replace("\r\n", " ").split():
                p = p.strip()
                if len(p) >= 2 and p[1] == ":":
                    try:
                        v = int(p[2:])
                    except: continue
                    if p[0] in ("S", "N0"): temps["n0"] = v
                    elif p[0] == "T": temps["n1"] = v
                    elif p[0] == "B": temps["bed"] = v
            return temps
        except:
            return None


ctrl = RoboxControl()


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CEL Robox Control")
        self.root.geometry("680x540")
        self.root.minsize(600, 480)
        self._polling = True
        self._build()
        self.root.after(500, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        print("Window created. Click Connect USB.")

    def _make_frame(self, parent, label=None):
        f = tk.Frame(parent, relief=tk.GROOVE, bd=1)
        f.pack(fill=tk.X, padx=5, pady=3)
        if label:
            tk.Label(f, text=label, font=("Segoe UI", 9, "bold"), anchor=tk.W).pack(fill=tk.X, padx=4, pady=(2, 0))
        return f

    def _btn(self, parent, text, cmd=None, width=None, fg=None, bg=None):
        kwargs = {"text": text, "relief": tk.RAISED, "bd": 1, "padx": 8, "pady": 2, "font": ("Segoe UI", 9)}
        if cmd: kwargs["command"] = cmd
        if width: kwargs["width"] = width
        if fg: kwargs["fg"] = fg
        if bg: kwargs["bg"] = bg
        return tk.Button(parent, **kwargs)

    def _build(self):
        self.sbar = tk.Label(self.root, text="Not connected", bd=1, relief=tk.SUNKEN, anchor=tk.W, font=("Segoe UI", 9))
        self.sbar.pack(side=tk.BOTTOM, fill=tk.X)

        main = tk.Frame(self.root, padx=5, pady=5)
        main.pack(fill=tk.BOTH, expand=True)

        top = tk.Frame(main)
        top.pack(fill=tk.X, pady=(0, 5))
        self.con_btn = self._btn(top, "Connect USB", self._connect, fg="white", bg="#2d8a2d")
        self.con_btn.pack(side=tk.LEFT, padx=2)
        self.dis_btn = self._btn(top, "Disconnect", self._disconnect, fg="white", bg="#a33")
        self.dis_btn.pack(side=tk.LEFT, padx=2)
        self.dis_btn.config(state=tk.DISABLED)
        self.stat_lbl = tk.Label(top, text="Disconnected", fg="#a00", font=("Segoe UI", 9, "bold"))
        self.stat_lbl.pack(side=tk.LEFT, padx=10)
        self.info_lbl = tk.Label(top, text="", fg="#555", font=("Segoe UI", 8))
        self.info_lbl.pack(side=tk.RIGHT, padx=5)

        f1 = self._make_frame(main, "Temperatures")
        tr = tk.Frame(f1)
        tr.pack(pady=3)
        self.t0 = tk.Label(tr, text="N0: --C", font=("Segoe UI", 20, "bold"), fg="#e07b20", width=10)
        self.t0.pack(side=tk.LEFT, padx=8)
        self.t1 = tk.Label(tr, text="N1: --C", font=("Segoe UI", 20, "bold"), fg="#c33", width=10)
        self.t1.pack(side=tk.LEFT, padx=8)
        self.tb = tk.Label(tr, text="Bed: --C", font=("Segoe UI", 20, "bold"), fg="#2a7ab5", width=10)
        self.tb.pack(side=tk.LEFT, padx=8)

        f2 = self._make_frame(main, "Printer")
        pr = tk.Frame(f2)
        pr.pack(pady=2)
        self._btn(pr, "Open Door", lambda: self._send("open_door")).pack(side=tk.LEFT, padx=2)
        self._btn(pr, "Light On", lambda: self._send("head_light", True)).pack(side=tk.LEFT, padx=2)
        self._btn(pr, "Light Off", lambda: self._send("head_light", False)).pack(side=tk.LEFT, padx=2)
        self._btn(pr, "Fan", lambda: self._send("fan", 255)).pack(side=tk.LEFT, padx=2)
        self._btn(pr, "Fan Off", lambda: self._send("fan", 0)).pack(side=tk.LEFT, padx=2)
        self._btn(pr, "Motors Off", lambda: self._send("motors_off")).pack(side=tk.LEFT, padx=2)

        f3 = self._make_frame(main, "Motion")
        hr = tk.Frame(f3)
        hr.pack(pady=2)
        tk.Label(hr, text="Home:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=2)
        self._btn(hr, "All", lambda: self._send("home", "XYZ"), width=4).pack(side=tk.LEFT, padx=1)
        self._btn(hr, "X", lambda: self._send("home", "X"), width=3).pack(side=tk.LEFT, padx=1)
        self._btn(hr, "Y", lambda: self._send("home", "Y"), width=3).pack(side=tk.LEFT, padx=1)
        self._btn(hr, "Z", lambda: self._send("home", "Z"), width=3).pack(side=tk.LEFT, padx=1)
        tk.Label(hr, text="   Step:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 2))
        self.step_v = tk.StringVar(value="10")
        tk.Entry(hr, textvariable=self.step_v, width=5, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        tk.Label(hr, text="mm  Speed:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(5, 2))
        self.spd_v = tk.StringVar(value="1500")
        tk.Entry(hr, textvariable=self.spd_v, width=5, font=("Segoe UI", 9)).pack(side=tk.LEFT)
        tk.Label(hr, text="mm/min", font=("Segoe UI", 9)).pack(side=tk.LEFT)

        jf = tk.Frame(f3)
        jf.pack(pady=3)
        def jog(dx, dy, dz):
            s = min(float(self.step_v.get() or 10), 50)
            f = min(int(self.spd_v.get() or 1500), 3000)
            # Home B axis first to prevent head lifter jamming
            self._send("gcode", "G28 B")
            self._send("move", x=dx * s, y=dy * s, z=dz * s, f=f)
        jg = tk.Frame(jf)
        jg.pack()
        tk.Label(jg, text="", width=4).grid(row=0, column=0)
        self._btn(jg, "Y+", lambda: jog(0, 1, 0), width=4).grid(row=0, column=1, padx=1, pady=1)
        tk.Label(jg, text="", width=4).grid(row=0, column=2)
        self._btn(jg, "X-", lambda: jog(-1, 0, 0), width=4).grid(row=1, column=0, padx=1, pady=1)
        self._btn(jg, "Z+", lambda: jog(0, 0, 1), width=4).grid(row=1, column=1, padx=1, pady=1)
        self._btn(jg, "X+", lambda: jog(1, 0, 0), width=4).grid(row=1, column=2, padx=1, pady=1)
        tk.Label(jg, text="", width=4).grid(row=2, column=0)
        self._btn(jg, "Y-", lambda: jog(0, -1, 0), width=4).grid(row=2, column=1, padx=1, pady=1)
        self._btn(jg, "Z-", lambda: jog(0, 0, -1), width=4).grid(row=2, column=2, padx=1, pady=1)

        bottom = tk.Frame(main)
        bottom.pack(fill=tk.BOTH, expand=True, pady=(3, 0))

        left = tk.LabelFrame(bottom, text="Temperatures", padx=5, pady=5, font=("Segoe UI", 9, "bold"))
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 3))
        tg = tk.Frame(left)
        tg.pack()
        tk.Label(tg, text="N0:", font=("Segoe UI", 9)).grid(row=0, column=0, sticky=tk.E, padx=2, pady=2)
        self.en0 = tk.Entry(tg, width=6, font=("Segoe UI", 9))
        self.en0.grid(row=0, column=1, padx=2, pady=2)
        tk.Label(tg, text="C").grid(row=0, column=2, sticky=tk.W)
        tk.Label(tg, text="N1:", font=("Segoe UI", 9)).grid(row=1, column=0, sticky=tk.E, padx=2, pady=2)
        self.en1 = tk.Entry(tg, width=6, font=("Segoe UI", 9))
        self.en1.grid(row=1, column=1, padx=2, pady=2)
        tk.Label(tg, text="C").grid(row=1, column=2, sticky=tk.W)
        tk.Label(tg, text="Bed:", font=("Segoe UI", 9)).grid(row=2, column=0, sticky=tk.E, padx=2, pady=2)
        self.eb = tk.Entry(tg, width=6, font=("Segoe UI", 9))
        self.eb.grid(row=2, column=1, padx=2, pady=2)
        tk.Label(tg, text="C").grid(row=2, column=2, sticky=tk.W)
        def do_set():
            try:
                n0 = int(self.en0.get()) if self.en0.get() else None
                n1 = int(self.en1.get()) if self.en1.get() else None
                b = int(self.eb.get()) if self.eb.get() else None
            except: return
            self._send("set_temp", n0=n0, n1=n1, bed=b)
        self._btn(tg, "Set", do_set, width=6).grid(row=3, column=0, columnspan=3, pady=5)
        pk = tk.Frame(left)
        pk.pack()
        for label, n0, n1, b in [("PLA 200/60", 200, 0, 60), ("PETG 235/80", 235, 0, 80),
                                  ("ABS 250/90", 250, 0, 90), ("All Off", 0, 0, 0)]:
            self._btn(pk, label, lambda n=n0, n1=n1, b=b: self._send("set_temp", n0=n, n1=n1, bed=b)).pack(side=tk.LEFT, padx=1)

        ex = tk.LabelFrame(left, text="Extruder", padx=5, pady=5, font=("Segoe UI", 9, "bold"))
        ex.pack(fill=tk.X, pady=(3, 0))
        eg = tk.Frame(ex)
        eg.pack()
        tk.Label(eg, text="Len:", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.el = tk.Entry(eg, width=5, font=("Segoe UI", 9))
        self.el.insert(0, "10"); self.el.pack(side=tk.LEFT, padx=2)
        tk.Label(eg, text="mm  Spd:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(5, 0))
        self.es = tk.Entry(eg, width=5, font=("Segoe UI", 9))
        self.es.insert(0, "300"); self.es.pack(side=tk.LEFT, padx=2)
        tk.Label(eg, text="mm/min", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._btn(eg, "Extrude", lambda: self._send("extrude", length=float(self.el.get() or 10),
                   speed=int(self.es.get() or 300))).pack(side=tk.LEFT, padx=3)
        self._btn(eg, "Retract", lambda: self._send("retract", length=float(self.el.get() or 10),
                   speed=int(self.es.get() or 300))).pack(side=tk.LEFT, padx=3)

        right = tk.LabelFrame(bottom, text="Terminal", padx=5, pady=5, font=("Segoe UI", 9, "bold"))
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(3, 0))
        self.term = tk.Text(right, height=6, font=("Consolas", 9), bd=0, relief=tk.SUNKEN)
        self.term.pack(fill=tk.BOTH, expand=True)
        self.term.insert(tk.END, "G-code terminal ready\n")
        te = tk.Frame(right)
        te.pack(fill=tk.X, pady=(3, 0))
        self.te = tk.Entry(te, font=("Consolas", 9))
        self.te.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self.te.bind("<Return>", lambda e: self._send_term())
        self._btn(te, "Send", self._send_term).pack(side=tk.RIGHT)

    def _connect(self):
        def task():
            try:
                ctrl.connect()
                fw = ctrl.firmware()
                pid = ctrl.printer_id()
                self.root.after(0, lambda f=fw, p=pid: self._on_conn(f, p))
            except Exception as ex:
                self.root.after(0, lambda e=str(ex): self._err(e))
        threading.Thread(target=task, daemon=True).start()

    def _on_conn(self, fw, pid):
        self.con_btn.config(state=tk.DISABLED)
        self.dis_btn.config(state=tk.NORMAL)
        self.stat_lbl.config(text="Connected", fg="#080")
        self.info_lbl.config(text=f"FW: {fw}")
        self.sbar.config(text=f"Connected - {pid}")

    def _disconnect(self):
        ctrl.disconnect()
        self.con_btn.config(state=tk.NORMAL)
        self.dis_btn.config(state=tk.DISABLED)
        self.stat_lbl.config(text="Disconnected", fg="#a00")
        self.info_lbl.config(text="")
        self.sbar.config(text="Disconnected")
        self.t0.config(text="N0: --C")
        self.t1.config(text="N1: --C")
        self.tb.config(text="Bed: --C")

    def _send(self, cmd, *args, **kwargs):
        self.sbar.config(text=f"Sending: {cmd}...")
        def task():
            try:
                getattr(ctrl, cmd)(*args, **kwargs)
                self.root.after(0, lambda: self.sbar.config(text=f"Done: {cmd}"))
            except Exception as ex:
                self.root.after(0, lambda e=str(ex): self._err(e))
        threading.Thread(target=task, daemon=True).start()

    def _send_term(self):
        cmd = self.te.get().strip()
        if not cmd: return
        self.te.delete(0, tk.END)
        self.term.insert(tk.END, f"> {cmd}\n")
        self.term.see(tk.END)
        def task():
            try:
                r = ctrl.gcode(cmd)
                self.root.after(0, lambda: self.term.insert(tk.END, f"{r}\n"))
                self.root.after(0, lambda: self.term.see(tk.END))
            except Exception as ex:
                msg = str(ex)
                self.root.after(0, lambda m=msg: self.term.insert(tk.END, f"Error: {m}\n"))
        threading.Thread(target=task, daemon=True).start()

    def _err(self, msg):
        print(f"ERROR: {msg}")
        self.sbar.config(text=f"Error: {msg}")

    def _poll(self):
        if not self._polling:
            return
        if ctrl.connected:
            def task():
                try:
                    t = ctrl.get_temps()
                    if t:
                        self.root.after(0, lambda t=t: self._update_temps(t))
                except:
                    self.root.after(0, self._disconnect)
            threading.Thread(target=task, daemon=True).start()
        self.root.after(1000, self._poll)

    def _update_temps(self, t):
        self.t0.config(text=f"N0: {t['n0']}C")
        self.t1.config(text=f"N1: {t['n1']}C")
        self.tb.config(text=f"Bed: {t['bed']}C")

    def _close(self):
        self._polling = False
        ctrl.disconnect()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if tk is None:
        print("tkinter not available")
        return 1
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
