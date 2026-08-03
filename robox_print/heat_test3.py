import sys, time
sys.path.insert(0, r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print')
from robox_protocol import RoboxProtocol
p = RoboxProtocol()
p.connect("COM9")
p.clear_errors()
p.abort_print()
time.sleep(2)
p.clear_errors()

# Check status
s = p.get_status()
print(f"Print line: {s.print_line_number}")
print(f"Error code: {s.error_code}")

# Turn head power on, heat, wait longer
p.execute_gcode("M127")
time.sleep(1)
p.execute_gcode("M104 S240")
for i in range(20):
    time.sleep(5)
    t = p.get_temperatures()
    n0 = t.get("n0", 0)
    bed = t.get("bed", 0)
    print(f"[{i*5}s] n0={n0}C bed={bed}C")
    if int(n0) > 100:
        print("Heating!")
        break

e = p.report_errors()
print("Errors:", e)
p.disconnect()
