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
        if stable >= 4:
            return last
        time.sleep(0.5)
    return last

# Test at THREE X positions: left (-14), center (105), right (212)
for x_pos, label in [(-14, "LEFT"), (105, "CENTER"), (212, "RIGHT")]:
    log(f"\n=== X={x_pos} ({label}) ===")
    p.execute_gcode("G90")
    p.execute_gcode(f"G0 X{x_pos} F1500")
    time.sleep(6)
    move_z_wait(0, timeout=60)
    log(f"  Z at bottom, X at {label}")
    contact = None
    z = 78.0
    while z <= 100.0:
        z_actual = move_z_wait(z, timeout=90)
        sw = get_switches()
        if "Z+:1" in sw:
            contact = z_actual
            log(f"  Z->{z}: settled {z_actual} | *** CONTACT ***")
            break
        z += 2.0
    if contact is None:
        log(f"  Z->100: no contact up to max")
        contact = "none"
    log(f"  RESULT {label}: contact at Z={contact}")
    # back off
    move_z_wait(20, timeout=60)

e = p.report_errors()
log(f"\nErrors: {e}")
p.execute_gcode("G0 X105 Y75 Z5 F2000")
time.sleep(8)
p.disconnect()

with open(r"C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print\z_contact_map.log", "w") as f:
    f.write("\n".join(results) + "\n")
