import sys
sys.path.insert(0, r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print')
from robox_protocol import RoboxProtocol
import time

ERROR_NAMES = {
    18:"B_STUCK", 25:"B_POSITION_LOST", 33:"B_POSITION_WARNING",
    14:"E_FILAMENT_SLIP", 28:"E_UNLOAD_SLIP", 29:"D_UNLOAD_SLIP",
    31:"E_NO_FILAMENT", 32:"D_NO_FILAMENT"
}

p = RoboxProtocol()
p.connect("COM9")

print("Connected. Clearing errors...")
p.clear_errors()
time.sleep(0.5)
e = p.report_errors()
print(f"Errors after clear: {[ERROR_NAMES.get(x, f'BIT{x}') for x in e]}")

# We can't open the printer manually. The B axis selector is stuck, likely
# holding the E filament. Let's try reversing the E extruder to relieve pressure.
print("\nPhase 1: Try to reverse E extruder to free filament...")
# Heat nozzle 0 (the actual nozzle) to soften filament
p.execute_gcode("M104 S230")

# Wait for temp to rise
for i in range(15):
    t = p.get_temperatures()
    print(f"  Temp: n0={t.get('n0',0)}C n1={t.get('n1',0)}C bed={t.get('bed',0)}C")
    if float(t.get("n0",0)) >= 200:
        print("  Nozzle hot enough")
        break
    time.sleep(2)

print("\nPhase 2: Attempt reverse extrude E to unjam...")
p.execute_gcode("G1 E-20 F300")
time.sleep(3)
e = p.report_errors()
print(f"Errors: {[ERROR_NAMES.get(x, f'BIT{x}') for x in e]}")

p.execute_gcode("G1 E-30 F200")
time.sleep(3)
e = p.report_errors()
print(f"Errors after reverse E: {[ERROR_NAMES.get(x, f'BIT{x}') for x in e]}")

print("\nPhase 3: Try T1 to move selector (maybe freed by reverse) ...")
for attempt in range(3):
    p.clear_errors()
    try:
        p.execute_gcode("T1")
        print(f"  T1 sent (attempt {attempt+1})")
    except Exception as ex:
        print(f"  T1 error: {ex}")
    time.sleep(2)
    e = p.report_errors()
    names = [ERROR_NAMES.get(x, f'BIT{x}') for x in e]
    print(f"  Errors after T1: {names}")
    if not e:
        print("  SUCCESS: T1 worked!")
        break

print("\nPhase 4: Check final state...")
t = p.get_temperatures()
print(f"Temps: n0={t.get('n0',0)}C n1={t.get('n1',0)}C bed={t.get('bed',0)}C")
e = p.report_errors()
print(f"Final errors: {[ERROR_NAMES.get(x, f'BIT{x}') for x in e]}")

p.disconnect()
