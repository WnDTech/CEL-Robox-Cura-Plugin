import sys, time
sys.path.insert(0, r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print')
import robox_protocol as rp

LOG = r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print\axis_limits.log'

p = rp.RoboxProtocol()
p.connect("COM9")
p.clear_errors()
p.abort_print()
time.sleep(2)
p.clear_errors()

log_lines = []
def log(msg):
    print(msg)
    log_lines.append(msg)

def probe(tag):
    pos = p.execute_gcode("M114", timeout=2).strip().split("\r\n")[0]
    sw = p.execute_gcode("M119", timeout=2).strip().split("\r\n")[0]
    log(f"  {tag}: {pos} | {sw}")
    return pos, sw

# --- Establish reference: home Y and Z (working switches) ---
log("=== SETUP: Home Y and Z (switches work), skip X (faulty switch) ===")
p.execute_gcode("G90")
p.execute_gcode("G28 Y")
time.sleep(8)
probe("G28 Y")
p.execute_gcode("G28 Z")
time.sleep(10)
probe("G28 Z")

# --- X AXIS LIMITS ---
log("\n=== X AXIS LIMITS (no working home switch - using firmware clamps) ===")
log("Firmware: travel[X]=226, tool_offset[X]=14 -> nozzle range [-14, 212]")
p.execute_gcode("G0 X50 Y75 Z5 F2000")
time.sleep(4)
probe("X start (50)")

# Left limit - drive to firmware clamp
log("Driving X LEFT (nozzle -14 = carriage 0)...")
p.execute_gcode("G0 X-100 F800")
time.sleep(8)
probe("X left limit")
if "X:1" in p.execute_gcode("M119", timeout=2):
    log("  X switch TRIPPED at left limit")
else:
    log("  X switch NOT tripped (faulty) - relying on clamp")

# Right limit
log("Driving X RIGHT (nozzle 212 = carriage 226)...")
p.execute_gcode("G0 X300 F800")
time.sleep(10)
probe("X right limit")

# --- Y AXIS LIMITS ---
log("\n=== Y AXIS LIMITS (switch works) ===")
log("Firmware: travel[Y]=155, tool_offset[Y]=4 -> nozzle range [-4, 151]")
p.execute_gcode("G0 X100 Y50 Z5 F2000")
time.sleep(4)
probe("Y start (50)")

# Front limit
log("Driving Y FRONT (nozzle -4 = carriage 0)...")
p.execute_gcode("G0 Y-100 F800")
time.sleep(8)
probe("Y front limit")
if "Y:1" in p.execute_gcode("M119", timeout=2):
    log("  Y switch TRIPPED at front limit")

# Back limit
log("Driving Y BACK (nozzle 151 = carriage 155)...")
p.execute_gcode("G0 Y300 F800")
time.sleep(8)
probe("Y back limit")

# --- Z AXIS LIMITS ---
log("\n=== Z AXIS LIMITS (switch works, top switch at 79) ===")
log("Firmware: travel[Z]=100.2, z_top_switch_min=79, safe max 75")
p.execute_gcode("G0 X100 Y75 Z5 F1000")
time.sleep(4)
probe("Z start (5)")

# Bottom
log("Driving Z BOTTOM...")
p.execute_gcode("G0 Z-20 F400")
time.sleep(8)
probe("Z bottom")

# Top (safe - stay below 75 to avoid lid arm)
log("Driving Z TOP (capped at 75 by plugin post-processor)...")
p.execute_gcode("G0 Z75 F400")
time.sleep(10)
probe("Z top (75)")
if "Z+:1" in p.execute_gcode("M119", timeout=2):
    log("  WARNING: Z+ top switch tripped at Z=75 - lid arm contact!")
else:
    log("  Z+ top switch clear at Z=75 (good)")

# --- Return to safe center ---
log("\n=== RETURN TO SAFE ===")
p.execute_gcode("G0 X105 Y75 Z5 F2000")
time.sleep(5)
probe("Safe position")

e = p.report_errors()
log(f"\nFinal errors: {e}")
p.disconnect()

with open(LOG, "w") as f:
    f.write("\n".join(log_lines) + "\n")
print(f"\nLogged to {LOG}")
