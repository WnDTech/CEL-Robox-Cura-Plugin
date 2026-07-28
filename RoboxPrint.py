# Cura post-processing script for CEL Robox
# Saves processed G-code and optionally sends to printer
# Place in: %APPDATA%\cura\<version>\scripts\

from ..Script import Script
import subprocess
import os

class RoboxPrint(Script):
    def __init__(self):
        super().__init__()

    def getSettingDataString(self):
        return """{
            "name": "Robox Print",
            "key": "RoboxPrint",
            "metadata": {},
            "version": 2,
            "settings": {
                "head_type": {
                    "label": "Head Type",
                    "description": "Select your Robox print head",
                    "type": "enum",
                    "options": {
                        "RBX01-DM": "Dual Material 0.4mm",
                        "RBX01-SM": "Single Material 0.3/0.8mm",
                        "RBXDV-S1": "SingleLite"
                    },
                    "default_value": "RBX01-DM"
                },
                "action": {
                    "label": "Action",
                    "description": "What to do with processed G-code",
                    "type": "enum",
                    "options": {
                        "save": "Save processed file only",
                        "print": "Save and print"
                    },
                    "default_value": "save"
                }
            }
        }"""

    def execute(self, data):
        head_type = self.getSettingValueByKey("head_type")
        action = self.getSettingValueByKey("action")

        gcode = "\n".join(data)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        robox_dir = os.path.join(script_dir, "..", "..", "..", "..", "..",
                                  "..", "OneDrive", "Documents", "APP",
                                  "Cel Robox Software")

        output_path = os.path.join(
            os.path.expanduser("~"),
            "Documents", "CEL Robox", "GCode",
            "processed.gcode"
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cmd = [
            "python", "-m", "robox_print", "process",
            "--common", os.path.join(os.path.expanduser("~"),
                                     "Documents", "CEL Robox", "Common"),
            "--head-type", head_type,
            "-o", output_path
        ]

        try:
            proc = subprocess.run(
                cmd,
                input=gcode,
                text=True,
                capture_output=True,
                timeout=30,
                cwd=robox_dir
            )
        except Exception as e:
            print(f"Robox post-processor error: {e}")

        return data
