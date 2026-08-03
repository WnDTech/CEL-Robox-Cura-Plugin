import sys, time
sys.path.insert(0, r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print')
from robox_protocol import RoboxProtocol
p = RoboxProtocol()
p.connect("COM9")
p.clear_errors()

# Try set_temperatures command (0xC3) instead of M104
print("Trying set_temperatures command...")
p.set_temperatures(nozzle0=240, bed=100)
time.sleep(15)

t = p.get_temperatures()
print(f"n0={t.get('n0',0)}C bed={t.get('bed',0)}C")
e = p.report_errors()
print("Errors:", e)

# Try M104 with M127 again
print("Trying M127 + M104...")
p.execute_gcode("M127")
time.sleep(2)
p.execute_gcode("M104 S250")
time.sleep(15)
t = p.get_temperatures()
print(f"n0={t.get('n0',0)}C bed={t.get('bed',0)}C")
e = p.report_errors()
print("Errors:", e)

p.disconnect()
