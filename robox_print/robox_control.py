import os
import sys
import json
import time
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from robox_protocol import (
    RoboxProtocol, RoboxError, RoboxConnectionError,
    MAX_NOZZLE_TEMP, MAX_BED_TEMP
)

HOST = "127.0.0.1"
PORT = 8080
CEL_DIR = Path(os.environ.get("USERPROFILE", "")) / "Documents" / "CEL Robox"

printer = None
printer_lock = threading.Lock()
update_event = threading.Event()


def get_printer():
    global printer
    if printer is None:
        printer = RoboxProtocol()
    return printer


def close_printer():
    global printer
    if printer:
        try:
            printer.disconnect()
        except Exception:
            pass
        printer = None


class RobustRobox:
    def __init__(self):
        self.proto = None
        self._port = None
        self._connected = False

    def connect(self):
        close_printer()
        self.proto = get_printer()
        self.proto.connect()
        self._port = self.proto.ser.port
        self._connected = True
        return True

    def disconnect(self):
        if self.proto:
            self.proto.disconnect()
        self._connected = False

    @property
    def connected(self):
        return self._connected and self.proto and self.proto.ser and self.proto.ser.is_open

    def status(self):
        if not self.connected:
            return None
        s = self.proto.get_status()
        return {
            "nozzle0": s.temp_nozzle0,
            "nozzle1": s.temp_nozzle1,
            "bed": s.temp_bed,
            "line": s.print_line_number,
            "door_open": s.door_open,
            "job": s.print_job_id,
            "error": s.error_code,
        }

    def gcode(self, cmd):
        if not self.connected:
            return "Not connected"
        return self.proto.execute_gcode(cmd)

    def open_door(self):
        return self.gcode("G37")

    def home(self, axes="XYZ"):
        for a in axes:
            self.gcode(f"G28 {a}")

    def move(self, x=None, y=None, z=None, feedrate=1500):
        parts = [f"G91"]
        self.gcode(" ".join(parts))
        cmd = "G0"
        if x is not None:
            cmd += f" X{x}"
        if y is not None:
            cmd += f" Y{y}"
        if z is not None:
            cmd += f" Z{z}"
        cmd += f" F{feedrate}"
        self.gcode(cmd)
        self.gcode("G90")

    def set_temp(self, nozzle0=None, nozzle1=None, bed=None):
        self.proto.set_temperatures(nozzle0=nozzle0, nozzle1=nozzle1, bed=bed)

    def extrude(self, length=10, feedrate=300):
        self.gcode("M83")
        self.gcode(f"G1 E{length} F{feedrate}")
        self.gcode("G90")

    def retract(self, length=10, feedrate=300):
        self.gcode("M83")
        self.gcode(f"G1 E-{length} F{feedrate}")
        self.gcode("G90")

    def head_light(self, on=True):
        self.gcode("M129" if on else "M128")

    def fan(self, speed=0):
        if speed > 0:
            self.gcode(f"M106 S{speed}")
        else:
            self.gcode("M107")

    def motors_off(self):
        self.gcode("M84")

    def firmware(self):
        if self.connected:
            return self.proto.get_firmware_version()
        return ""

    def printer_id(self):
        if self.connected:
            return self.proto.get_printer_id()
        return ""


controller = RobustRobox()
current_status = {"connected": False, "nozzle0": 0, "nozzle1": 0, "bed": 0,
                  "door_open": False, "line": 0, "error": 0, "firmware": "", "printer_id": ""}


def status_poller():
    while True:
        if controller.connected:
            try:
                s = controller.status()
                if s:
                    current_status.update(s)
                    current_status["connected"] = True
            except Exception:
                current_status["connected"] = False
        else:
            current_status["connected"] = False
        time.sleep(0.5)


class APIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "/":
                self.send_html(INDEX_HTML)
            elif path == "/api/status":
                self.send_json(current_status)
            elif path == "/api/connect":
                try:
                    controller.connect()
                    current_status["firmware"] = controller.firmware()
                    current_status["printer_id"] = controller.printer_id()
                    self.send_json({"ok": True})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)})
            elif path == "/api/disconnect":
                controller.disconnect()
                current_status["connected"] = False
                self.send_json({"ok": True})
            elif path == "/api/open_door":
                controller.open_door()
                self.send_json({"ok": True})
            elif path == "/api/home":
                axes = query.get("axes", ["XYZ"])[0]
                controller.home(axes)
                self.send_json({"ok": True})
            elif path == "/api/move":
                x = float(query.get("x", [0])[0]) if "x" in query else None
                y = float(query.get("y", [0])[0]) if "y" in query else None
                z = float(query.get("z", [0])[0]) if "z" in query else None
                f = int(query.get("f", [1500])[0])
                controller.move(x=x, y=y, z=z, feedrate=f)
                self.send_json({"ok": True})
            elif path == "/api/extrude":
                length = float(query.get("length", ["10"])[0])
                speed = int(query.get("speed", ["300"])[0])
                controller.extrude(length, speed)
                self.send_json({"ok": True})
            elif path == "/api/retract":
                length = float(query.get("length", ["10"])[0])
                speed = int(query.get("speed", ["300"])[0])
                controller.retract(length, speed)
                self.send_json({"ok": True})
            elif path == "/api/set_temp":
                n0 = int(query.get("nozzle0", [""])[0]) if "nozzle0" in query else None
                n1 = int(query.get("nozzle1", [""])[0]) if "nozzle1" in query else None
                b = int(query.get("bed", [""])[0]) if "bed" in query else None
                controller.set_temp(nozzle0=n0, nozzle1=n1, bed=b)
                self.send_json({"ok": True})
            elif path == "/api/gcode":
                cmd = query.get("cmd", [""])[0]
                result = controller.gcode(cmd)
                self.send_json({"ok": True, "result": result})
            elif path == "/api/head_light":
                on = query.get("on", ["1"])[0] == "1"
                controller.head_light(on)
                self.send_json({"ok": True})
            elif path == "/api/fan":
                speed = int(query.get("speed", ["0"])[0])
                controller.fan(speed)
                self.send_json({"ok": True})
            elif path == "/api/motors_off":
                controller.motors_off()
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode())

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, *a):
        pass


def main():
    poller = threading.Thread(target=status_poller, daemon=True)
    poller.start()

    server = HTTPServer((HOST, PORT), APIHandler)
    print(f"\n  CEL Robox Control Panel")
    print(f"  {"="*40}")
    print(f"  Open browser to: http://{HOST}:{PORT}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.disconnect()
        server.server_close()


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CEL Robox Control</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;display:flex;justify-content:center;min-height:100vh}
.container{max-width:800px;width:100%;padding:20px}
h1{font-size:24px;margin-bottom:20px;color:#58a6ff;display:flex;align-items:center;gap:10px}
h1 span{font-size:14px;color:#8b949e;font-weight:400}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:12px}
.card h2{font-size:14px;color:#8b949e;margin-bottom:12px;text-transform:uppercase;letter-spacing:.5px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.temp-group{display:flex;gap:16px;flex-wrap:wrap}
.temp{text-align:center;min-width:100px;padding:8px;background:#0d1117;border-radius:6px;border:1px solid #30363d}
.temp .label{font-size:11px;color:#8b949e}
.temp .value{font-size:28px;font-weight:700;display:block}
.temp .target{font-size:12px;color:#8b949e;display:block;margin-top:4px}
.temp .value.n0{color:#f0883e}
.temp .value.n1{color:#da3633}
.temp .value.bed{color:#58a6ff}
.btn{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:6px 16px;border-radius:6px;cursor:pointer;font-size:13px;transition:all .15s}
.btn:hover{background:#30363d;border-color:#8b949e}
.btn:active{background:#161b22}
.btn.danger{color:#f0883e}
.btn.danger:hover{border-color:#f0883e}
.btn.primary{background:#238636;border-color:#238636;color:#fff}
.btn.primary:hover{background:#2ea043}
.btn.warn{background:#d29922;border-color:#d29922;color:#fff}
.btn.sm{padding:4px 10px;font-size:12px}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-group{display:flex;gap:4px;flex-wrap:wrap}
input[type=number]{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 8px;border-radius:6px;width:60px;font-size:13px}
input[type=text]{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 8px;border-radius:6px;width:200px;font-size:13px;font-family:monospace}
.status-dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px}
.status-dot.on{background:#3fb950}
.status-dot.off{background:#da3633}
.door-indicator{display:inline-flex;align-items:center;gap:6px;font-size:14px}
#gcode-result{font-family:monospace;font-size:12px;color:#8b949e;margin-top:8px;padding:8px;background:#0d1117;border-radius:4px;max-height:100px;overflow:auto}
@media(max-width:600px){.temp{min-width:80px}.temp .value{font-size:22px}}
</style>
</head>
<body>
<div class="container">
  <h1>🖨️ CEL Robox <span id="connection-status">Disconnected</span></h1>

  <div class="card" id="connect-card">
    <div class="row">
      <button class="btn primary" onclick="connect()" id="connect-btn">Connect</button>
      <button class="btn danger" onclick="disconnect()" id="disconnect-btn" style="display:none">Disconnect</button>
      <span id="printer-info" style="font-size:13px;color:#8b949e"></span>
    </div>
  </div>

  <div class="card">
    <h2>Temperatures</h2>
    <div class="temp-group">
      <div class="temp"><span class="label">Nozzle 0</span><span class="value n0" id="temp-n0">0</span><span class="target" id="targ-n0">°C</span></div>
      <div class="temp"><span class="label">Nozzle 1</span><span class="value n1" id="temp-n1">0</span><span class="target" id="targ-n1">°C</span></div>
      <div class="temp"><span class="label">Bed</span><span class="value bed" id="temp-bed">0</span><span class="target" id="targ-bed">°C</span></div>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="btn sm" onclick="setTemp(200,0,60)">PLA 200°C / 60°C</button>
      <button class="btn sm" onclick="setTemp(235,0,80)">PETG 235°C / 80°C</button>
      <button class="btn sm" onclick="setTemp(260,0,90)">ABS 260°C / 90°C</button>
      <button class="btn sm danger" onclick="setTemp(0,0,0)">Off</button>
    </div>
  </div>

  <div class="card">
    <h2>Printer Controls</h2>
    <div class="row" style="margin-bottom:8px">
      <button class="btn warn" onclick="api('/api/open_door')">🚪 Open Door</button>
      <button class="btn" onclick="api('/api/head_light?on=1')">💡 Light On</button>
      <button class="btn" onclick="api('/api/head_light?on=0')">💡 Light Off</button>
      <button class="btn" onclick="api('/api/motors_off')">🔌 Motors Off</button>
      <button class="btn" onclick="api('/api/fan?speed=255')">🌀 Fan Full</button>
      <button class="btn" onclick="api('/api/fan?speed=0')">🌀 Fan Off</button>
      <span id="door-status"></span>
    </div>
  </div>

  <div class="card">
    <h2>Jog</h2>
    <div class="row" style="margin-bottom:8px">
      <button class="btn" onclick="api('/api/home?axes=XYZ')">🏠 Home All</button>
      <button class="btn sm" onclick="api('/api/home?axes=X')">Home X</button>
      <button class="btn sm" onclick="api('/api/home?axes=Y')">Home Y</button>
      <button class="btn sm" onclick="api('/api/home?axes=Z')">Home Z</button>
    </div>
    <div class="row">
      <span style="font-size:12px;color:#8b949e;margin-right:4px">Step:</span>
      <input type="number" id="step-size" value="10" min="0.1" max="100" step="0.1" style="width:60px">
      <span style="font-size:12px;color:#8b949e;margin:0 8px">Feedrate:</span>
      <input type="number" id="feedrate" value="1500" min="100" max="5000" step="100" style="width:70px">
    </div>
    <div style="display:flex;gap:4px;justify-content:center;margin-top:8px">
      <div style="display:grid;grid-template-columns:50px 50px 50px;gap:4px">
        <div></div>
        <button class="btn sm" onclick="jog(0,1,0)">Y+</button>
        <div></div>
        <button class="btn sm" onclick="jog(-1,0,0)">X-</button>
        <button class="btn sm" onclick="jog(0,0,0)">X/Y</button>
        <button class="btn sm" onclick="jog(1,0,0)">X+</button>
        <div></div>
        <button class="btn sm" onclick="jog(0,-1,0)">Y-</button>
        <div></div>
      </div>
      <div style="display:grid;grid-template-columns:50px;gap:4px;margin-left:12px">
        <button class="btn sm" onclick="jog(0,0,1)">Z+</button>
        <button class="btn sm" onclick="jog(0,0,-1)">Z-</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Extruder</h2>
    <div class="row">
      <input type="number" id="extrude-length" value="10" min="0.1" max="100" step="0.1" style="width:60px"> mm
      <input type="number" id="extrude-speed" value="300" min="10" max="3000" style="width:60px"> mm/min
      <button class="btn" onclick="extrude()">⬇️ Extrude</button>
      <button class="btn" onclick="retract()">⬆️ Retract</button>
    </div>
  </div>

  <div class="card">
    <h2>Raw G-code</h2>
    <div class="row">
      <input type="text" id="gcode-input" placeholder="e.g. G1 X10 F1500" onkeydown="if(event.key==='Enter')sendGcode()">
      <button class="btn" onclick="sendGcode()">Send</button>
    </div>
    <div id="gcode-result">Enter a command above</div>
  </div>
</div>

<script>
async function api(url){
  let btn = event?.target;
  if(btn) btn.disabled = true;
  try {
    let r = await fetch(url);
    let d = await r.json();
    if(d.ok === false && d.error) console.error(d.error);
    if(d.result !== undefined) document.getElementById('gcode-result').textContent = d.result;
  } catch(e) { console.error(e) }
  if(btn) setTimeout(()=>btn.disabled=false,500);
}

async function connect(){
  let r = await fetch('/api/connect');
  let d = await r.json();
  if(d.ok){
    document.getElementById('connect-btn').style.display = 'none';
    document.getElementById('disconnect-btn').style.display = '';
  } else {
    alert('Failed to connect: ' + d.error);
  }
}

async function disconnect(){
  await fetch('/api/disconnect');
  document.getElementById('connect-btn').style.display = '';
  document.getElementById('disconnect-btn').style.display = 'none';
  document.getElementById('printer-info').textContent = '';
}

function setTemp(n0,n1,b){ api(`/api/set_temp?nozzle0=${n0}&nozzle1=${n1}&bed=${b}`) }

function jog(dx,dy,dz){
  let step = parseFloat(document.getElementById('step-size').value) || 10;
  let feed = parseInt(document.getElementById('feedrate').value) || 1500;
  let x = dx*step, y = dy*step, z = dz*step;
  if(x===0&&y===0&&z===0) return;
  api(`/api/move?x=${x}&y=${y}&z=${z}&f=${feed}`);
}

function extrude(){
  let l = parseFloat(document.getElementById('extrude-length').value) || 10;
  let s = parseInt(document.getElementById('extrude-speed').value) || 300;
  api(`/api/extrude?length=${l}&speed=${s}`);
}

function retract(){
  let l = parseFloat(document.getElementById('extrude-length').value) || 10;
  let s = parseInt(document.getElementById('extrude-speed').value) || 300;
  api(`/api/retract?length=${l}&speed=${s}`);
}

function sendGcode(){
  let cmd = document.getElementById('gcode-input').value;
  if(!cmd) return;
  api('/api/gcode?cmd=' + encodeURIComponent(cmd));
  document.getElementById('gcode-result').textContent = 'Sent: ' + cmd + '...';
}

async function poll(){
  try {
    let r = await fetch('/api/status');
    let s = await r.json();
    document.getElementById('connection-status').textContent = s.connected ? 'Connected' : 'Disconnected';
    document.getElementById('connection-status').style.color = s.connected ? '#3fb950' : '#da3633';
    document.getElementById('temp-n0').textContent = s.nozzle0;
    document.getElementById('temp-n1').textContent = s.nozzle1;
    document.getElementById('temp-bed').textContent = s.bed;
    if(s.door_open) {
      document.getElementById('door-status').innerHTML = '🚪 Open';
      document.getElementById('door-status').style.color = '#d29922';
    } else {
      document.getElementById('door-status').innerHTML = '🚪 Closed';
      document.getElementById('door-status').style.color = '#8b949e';
    }
    if(s.firmware) document.getElementById('printer-info').textContent = `FW: ${s.firmware} | ID: ${s.printer_id}`;
    if(s.error) document.getElementById('gcode-result').textContent = `⚠️ Printer error code: 0x${s.error.toString(16)}`;
  } catch(e) { console.error(e) }
}
setInterval(poll, 600);
poll();
</script>
</body>
</html>"""

if __name__ == "__main__":
    main()
