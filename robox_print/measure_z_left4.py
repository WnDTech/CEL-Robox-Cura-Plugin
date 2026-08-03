import sys, time
sys.path.insert(0, r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print')
import robox_protocol as rp

p = rp.RoboxProtocol()
p.connect("COM9")
p.clear_errors()
p.abort_print()
time.sleep(2)
p.clear_errors()

results = []
def log(msg):
    print(msg)
    results.append(msg)

def get_z():
    pos = p.execute_gcode("M114", timeout=2)
    try:
        return float(pos.split("Z:")[1].split(" ")[0])
    except Exception:
        return None

def get_switches():
    return p.execute_gcode("M119", timeout=2)

def move_z_wait(target, timeout=90):
    """Command Z move, then poll M114 until it stops changing."""
    p.execute_gcode(f"G0 Z{target} F200")
    last = None
    stable = 0
    start = time.time()
    while time.time() - start < timeout:
        z = get_z()
        if z is not None and last is not None and abs(z - last) < 0.05:
            stable += 1
        else:
            stable = 0
        if z is not None:
            last = z
        if stable >= 4:  # 4 consecutive stable reads (0.5s apart)
            return last
        time.sleep(0.5)
    return last

# X at left limit
log("=== X AT LEFT LIMIT ===")
p.execute_gcode("G90")
p.execute_gcode("G0 X-100 F1500")
time.sleep(8)
pos = p.execute_gcode("M114", timeout=2).strip().split("\r\n")[0]
sw = get_switches().strip().split("\r\n")[0]
log(f"{pos} | {sw}")

# Z to bottom first (poll until settled)
z0 = move_z_wait(0, timeout=60)
log(f"Z settled at bottom: {z0}")

# Raise Z in 2mm steps, poll until settled each time, watch Z+
log("\n=== RAISING Z, polling until settled, watching Z+ ===")
z = 78.0
contact_z = None
while z <= 100.0:
    z_actual = move_z_wait(z, timeout=90)
    sw = get_switches()
    zplus = "Z+:1" in sw
    log(f"Z target {z}: settled at Z={z_actual} | {'*** Z+ TRIPPED (LID CONTACT) ***' if zplus else 'Z+ clear'}")
    if zplus:
        contact_z = z_actual
        log(f"\n*** LID ARM CONTACT AT Z={z_actual}mm WITH X AT LEFT ***")
        break
    z += 2.0

# Back off to safe
log("\n=== LOWERING Z TO 20mm ===")
move_z_wait(20, timeout=90)
e = p.report_errors()
log(f"Errors: {e}")

p.execute_gcode("G0 X105 Y75 Z5 F2000")
time.sleep(8)
p.disconnect()

safe_z = (contact_z - 10.0) if contact_z else 75.0
log(f"\nRESULT: contact at Z={contact_z}, safe Z cap at X-left = {safe_z}mm")
with open(r"C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print\z_left_limit.log", "w") as f:
    f.write("\n".join(results) + "\n")
