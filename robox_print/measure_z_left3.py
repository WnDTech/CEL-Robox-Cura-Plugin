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

def move_wait(cmd, settle=1.5):
    p.execute_gcode(cmd)
    p.execute_gcode("M400")  # wait for motion buffer to empty
    time.sleep(settle)

# X at left limit
log("=== X AT LEFT LIMIT ===")
p.execute_gcode("G90")
move_wait("G0 X-100 F1500")
pos = p.execute_gcode("M114", timeout=2).strip().split("\r\n")[0]
sw = p.execute_gcode("M119", timeout=2).strip().split("\r\n")[0]
log(f"{pos} | {sw}")

# Z at bottom first
move_wait("G0 Z0 F200", settle=3)
log("Z at bottom: " + p.execute_gcode("M114", timeout=2).strip().split("\r\n")[0])

# Raise Z in 2mm steps from 78 up, watching Z+ switch
log("\n=== RAISING Z, watching Z+ (M400 after each move) ===")
z = 78.0
contact_z = None
while z <= 100.0:
    move_wait(f"G0 Z{z} F200", settle=1.5)
    pos = p.execute_gcode("M114", timeout=2).strip().split("\r\n")[0]
    sw = p.execute_gcode("M119", timeout=2).strip().split("\r\n")[0]
    z_actual = float(pos.split("Z:")[1].split(" ")[0])
    zplus = "Z+:1" in sw
    log(f"Z->{z}: {pos} | {'*** Z+ TRIPPED (LID CONTACT) ***' if zplus else 'Z+ clear'}")
    if zplus:
        contact_z = z_actual
        log(f"\n*** LID ARM CONTACT AT Z={z_actual}mm WITH X AT LEFT ***")
        break
    z += 2.0

# Back off to safe
log("\n=== LOWERING Z TO 20mm ===")
move_wait("G0 Z20 F200", settle=3)
e = p.report_errors()
log(f"Errors: {e}")

p.execute_gcode("G0 X105 Y75 Z5 F2000")
time.sleep(8)
p.disconnect()

safe_z = (contact_z - 10.0) if contact_z else 75.0
log(f"\nRESULT: contact at Z={contact_z}, safe Z cap at X-left = {safe_z}mm")
with open(r"C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print\z_left_limit.log", "w") as f:
    f.write("\n".join(results) + "\n")
