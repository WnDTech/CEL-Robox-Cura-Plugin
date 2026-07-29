import sys
import os
sys.path.insert(0, r'C:\Users\paul_\OneDrive\Documents\APP\Cel Robox Software\robox_print')

from robox_protocol import RoboxProtocol

ERROR_NAMES = {
    0:"SD_CARD",1:"CHUNK_SEQUENCE",2:"FILE_TOO_LARGE",3:"GCODE_LINE_TOO_LONG",
    4:"USB_RX",5:"USB_TX",6:"BAD_COMMAND",7:"HEAD_EEPROM",
    8:"BAD_FIRMWARE_FILE",9:"FLASH_CHECKSUM",10:"GCODE_BUFFER_OVERRUN",
    11:"FILE_READ_CLOBBERED",12:"MAX_GANTRY_ADJUSTMENT",13:"REEL0_EEPROM",
    14:"E_FILAMENT_SLIP",15:"D_FILAMENT_SLIP",16:"NOZZLE_FLUSH_NEEDED",
    17:"Z_TOP_SWITCH",18:"B_STUCK",19:"REEL1_EEPROM",20:"HEAD_POWER_EEPROM",
    21:"HEAD_POWER_OVERTEMP",22:"BED_THERMISTOR",23:"NOZZLE0_THERMISTOR",
    24:"NOZZLE1_THERMISTOR",25:"B_POSITION_LOST",26:"E_LOAD_SLIP",
    27:"D_LOAD_SLIP",28:"E_UNLOAD_SLIP",29:"D_UNLOAD_SLIP",
    30:"POWEROFF_WHILST_HOT",31:"E_NO_FILAMENT",32:"D_NO_FILAMENT",
    33:"B_POSITION_WARNING",34:"HEAD_SHORTED",35:"X_DRIVER",36:"Y_DRIVER",
    37:"ZA_DRIVER",38:"ZB_DRIVER",39:"E_DRIVER",40:"D_DRIVER",
    41:"BED_TEMPERATURE_DROOP",
}

def main():
    try:
        proto = RoboxProtocol()
        proto.connect("COM9")
        
        print("=== Printer Status ===")
        status = proto.get_status()
        print(f"Print line: {status.print_line_number}")
        print(f"Bed temp: {status.temp_bed}C")
        print(f"Nozzle0 temp: {status.temp_nozzle0}C")
        print(f"Nozzle1 temp: {status.temp_nozzle1}C")
        print(f"Has errors: {status.has_errors()}")
        print(f"Error code: {status.error_code}")
        
        print("\n=== Error Status ===")
        errors = proto.report_errors()
        if errors:
            print(f"Active errors ({len(errors)}):")
            for e in errors:
                name = ERROR_NAMES.get(e, f"BIT{e}")
                print(f"  - {name} (bit {e})")
        else:
            print("No errors")
        
        print("\n=== Temperatures ===")
        temps = proto.get_temperatures()
        if temps:
            print(f"Nozzle 0: {temps.get('n0', 0)}C")
            print(f"Nozzle 1: {temps.get('n1', 0)}C")
            print(f"Bed: {temps.get('bed', 0)}C")
        
        proto.disconnect()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
