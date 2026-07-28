# CEL Robox Cura Plugin

A Cura plugin that adds native USB printing support for the CEL Robox 3D printer.

## Features

- **Direct USB printing** — connects to your Robox via USB and sends G-code directly
- **Binary protocol support** — full implementation of the Robox USB protocol
- **Live monitoring** — nozzle/bed temperatures, print progress, time elapsed/remaining in Cura's Monitor tab
- **Preheat on print** — sends correct temperatures from your selected profile before printing
- **Safety controls** — Cool Down button, Open Door button (with temperature safety check)
- **Dual extruder support** — works with both single (RBX01-SM) and dual material (RBX01-DM) heads
- **All quality profiles** — Extra Fine / Fine / Standard / Draft / Coarse
- **Macro injection** — automatically injects bed leveling, purge, and calibration routines
- **Standalone control app** — included for diagnostics and manual printer control

## Installation

### Option 1: Installer (recommended)

Download CEL_Robox_Cura_Setup.exe from the Releases page and run it. Restart Cura, then go to **Settings → Printers → Add Printer → CEL → CEL Robox**.

### Option 2: Manual

1. Install [Python 3.10+](https://python.org) and run:
   `
   pip install pyserial
   `
2. Copy the plugins/RoboxPrint folder to:
   %APPDATA%/cura/5.13/plugins/
3. Copy the definitions files:
   - cel_robox.def.json → %APPDATA%/cura/5.13/definitions/
   - cel_robox_extruder_0.def.json → %APPDATA%/cura/5.13/extruders/
   - cel_robox_extruder_1.def.json → %APPDATA%/cura/5.13/extruders/
4. Copy the quality/cel_robox/*.inst.cfg files to:
   %APPDATA%/cura/5.13/quality/cel_robox/
5. Restart Cura

## Usage

1. Connect your CEL Robox via USB
2. In Cura, go to **Settings → Printers → Add Printer → CEL → CEL Robox**
3. Select a quality profile (Standard, Fine, Draft, etc.)
4. Select a material (PLA, ABS, PETG, etc.) — the plugin reads the material temperature
5. Slice your model and click **Print with Robox**

The plugin will:
1. Post-process the G-code (inject macros, map extruders)
2. Send M104/M140 with your profile temperatures (immediate preheat)
3. Upload the G-code to the printer's SD card via binary protocol
4. Start the print
5. Show live temperatures and progress in the Monitor tab

### Controls in Monitor Tab

| Button | Function |
|--------|----------|
| **Cool Down** | Turns off nozzle and bed heaters immediately |
| **Open Door** | Moves head to door-unlock position (disabled when hot) |

## Project Structure

`
├── robox_print/                    # Python backend package
│   ├── robox_protocol.py           # Binary USB protocol implementation
│   ├── robox_postprocessor.py      # G-code post-processor (macros, extruder mapping)
│   ├── robox_control_gui.py        # Standalone control app (tkinter GUI)
│   └── robox_control.py            # Web-based control panel
├── robox-cura-package/
│   ├── plugin/                     # Cura plugin
│   │   ├── RoboxOutputDevicePlugin.py
│   │   ├── MonitorItem.qml         # Monitor view UI
│   │   ├── plugin.json
│   │   └── resources/robox_print/  # Bundled Python backend
│   ├── definitions/                # Printer + extruder definitions
│   ├── quality/                    # Quality profiles
│   ├── installer/                  # Batch installers
│   └── installer.iss              # Inno Setup script
├── run_automaker.bat              # AutoMaker launcher
└── robox_control_launcher.py      # Control app entry point
`

## Requirements

- Cura 5.13+
- Python 3.10+ (for USB backend)
- pyserial (pip install pyserial) — auto-installed by the installer
- CEL Robox printer with USB connection

## License

This project is open-source. Original CEL Robox source code by CEL-UK.

## Credits

Based on the open-source CEL Robox software available at [github.com/celsworthy](https://github.com/celsworthy).
