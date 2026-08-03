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

# Clear bed levelling so Z tracks commands 1:1
p.execute_gcode("G39")
time.sleep(2)
p.execute_gcode("G28 Y")
time.sleep(8)
p.execute_gcode("G28 Z")
time.sleep(12)

# Move X to the LEFT limit (clamped move - safe, no switch needed)
log("=== MOVING X TO LEFT LIMIT (G0 X-100) ===")
p.execute_gcode("G90")
p.execute_gcode("G0 X-100 F1500")
time.sleep(8)
pos = p.execute_gcode("M114", timeout=2).strip().split("\r\n")[0]
sw = p.execute_gcode("M119", timeout=2).strip().split("\r\n")[0]
log(f"X at left: {pos} | {sw}")

# Raise Z in small steps, watching Z+ (lid arm contact)
log("\n=== RAISING Z AT X LEFT LIMIT, watching Z+ top switch ===")
log("NOTE: Z max speed is 3.5mm/s - allow full time for each move")
z = 0.0
contact_z = None
while z < 80.0:
    target = min(z + 5.0, 80.0)
    p.execute_gcode(f"G0 Z{target} F200")
    # 5mm at 3.5mm/s = 1.43s + margin
    time.sleep(3.5)
    pos = p.execute_gcode("M114", timeout=2).strip().split("\r\n")[0]
    sw = p.execute_gcode("M119", timeout=2).strip().split("\r\n")[0]
    z_actual = float(pos.split("Z:")[1].split(" ")[0])
    zplus = "Z+:1" in sw
    log(f"Z->{target}: {pos} | {'Z+ TRIPPED (LID CONTACT!)' if zplus else 'Z+ clear'}")
    if zplus:
        contact_z = z_actual
        log(f"\n*** LID ARM CONTACT AT Z={z_actual}mm WITH X AT LEFT ***")
        break
    z = target

# Back off Z to safe 20mm
log("\n=== LOWERING Z TO 20mm ===")
p.execute_gcode("G0 Z20 F200")
time.sleep(8)

e = p.report_errors()
log(f"Errors: {e}")

# Return to safe center
p.execute_gcode("G0 X105 Y75 Z5 F2000")
time.sleep(8)
p.disconnect()

log(f"\nRESULT: safe Z at X-left = {contact_z - 10.0 if contact_z else 75.0}mm (contact at {contact_z})")
with open(r"C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print\z_left_limit.log", "w") as f:
    f.write("\n".join(results) + "\n")
