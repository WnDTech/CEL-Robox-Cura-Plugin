import sys, time
sys.path.insert(0, r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print')
from robox_protocol import RoboxProtocol
p = RoboxProtocol()
p.connect("COM9")
p.clear_errors()
p.execute_gcode("M127")
p.execute_gcode("M104 S240")
for i in range(10):
    time.sleep(6)
    t = p.get_temperatures()
    n0 = t.get("n0", 0)
    bed = t.get("bed", 0)
    print(f"n0={n0}C bed={bed}C")
e = p.report_errors()
print("Errors:", e)
p.disconnect()
