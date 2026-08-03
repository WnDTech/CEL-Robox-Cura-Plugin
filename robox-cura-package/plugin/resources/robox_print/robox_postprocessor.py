import os
import re
import logging

logger = logging.getLogger(__name__)

MACRO_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Common", "Macros")
MACRO_EXT = ".gcode"
MACRO_PREFIX = "Macro:"

def resolve_macro_path(common_dir, macro_name, head_type=None, use_nozzle0=False,
                       use_nozzle1=False, safeties=True):
    macro_dir = os.path.join(common_dir, "Macros")
    if not os.path.isdir(macro_dir):
        raise FileNotFoundError(f"Macro directory not found: {macro_dir}")

    base_name = macro_name.split("#")[0]
    candidates = []
    for f in os.listdir(macro_dir):
        if f.startswith(base_name) and f.endswith(MACRO_EXT):
            candidates.append(f)
    if not candidates:
        raise FileNotFoundError(f"No macro files found for {macro_name} in {macro_dir}")

    base_name = macro_name.split("#")[0]
    best_score = -9999
    best_file = None
    for f in candidates:
        score = _score_macro_file(f, base_name, head_type, use_nozzle0, use_nozzle1, safeties)
        logger.debug(f"Macro {f}: score {score}")
        if score > best_score:
            best_score = score
            best_file = f

    if best_file is None:
        raise FileNotFoundError(f"No suitable macro for {macro_name}")
    return os.path.join(macro_dir, best_file)


def _score_macro_file(filename, base_macro_name, head_type, use_nozzle0,
                      use_nozzle1, safeties):
    if not filename.endswith(MACRO_EXT):
        return -9999

    name_no_ext = filename[:-len(MACRO_EXT)]
    parts = name_no_ext.split("#")
    if parts[0] != base_macro_name:
        return -9999

    score = 0
    file_head = None
    file_nozzle = None
    file_safety = None

    for part in parts[1:]:
        if part in ("N0", "N1", "NB"):
            file_nozzle = part
        elif part in ("S", "U"):
            file_safety = part
        else:
            file_head = part

    # Head matching
    if head_type:
        if file_head == head_type:
            score += 2
        elif file_head is None:
            score += 1
        else:
            score -= 2
    else:
        if file_head is None:
            score += 2

    # Nozzle indicator matching
    spec_nozzle = None
    if use_nozzle0 and not use_nozzle1:
        spec_nozzle = "N0"
    elif not use_nozzle0 and use_nozzle1:
        spec_nozzle = "N1"
    elif use_nozzle0 and use_nozzle1:
        spec_nozzle = "NB"

    if spec_nozzle is None:
        if file_nozzle is None:
            score += 2
    else:
        if file_nozzle == spec_nozzle:
            score += 2
        elif file_nozzle is None:
            score += 1
        else:
            score -= 2

    # Safety indicator matching
    spec_safety = "S" if safeties else "U"
    if file_safety == spec_safety:
        score += 2
    elif file_safety is None:
        score += 1
    else:
        score -= 2

    return score


class Macros:
    def __init__(self, common_dir, head_type="RBX01-SM", use_nozzle0=True,
                 use_nozzle1=False, safeties=True):
        self.common_dir = common_dir
        self.head_type = head_type
        self.use_nozzle0 = use_nozzle0
        self.use_nozzle1 = use_nozzle1
        self.safeties = safeties

    def get_contents(self, macro_name, _expanding=None):
        if _expanding is None:
            _expanding = []
        clean_name = macro_name.replace(MACRO_PREFIX, "").strip()
        if clean_name in _expanding:
            raise RuntimeError(f"Circular macro dependency: {clean_name}")

        file_path = resolve_macro_path(
            self.common_dir, clean_name, self.head_type,
            self.use_nozzle0, self.use_nozzle1, self.safeties
        )

        _expanding.append(clean_name)
        lines = []
        try:
            with open(file_path) as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith(MACRO_PREFIX):
                        sub_macro_name = stripped.replace(MACRO_PREFIX, "").strip()
                        if "#N0" in sub_macro_name and not self.use_nozzle0:
                            continue
                        if "#N1" in sub_macro_name and not self.use_nozzle1:
                            continue
                        sub = self.get_contents(stripped, _expanding)
                        lines.extend(sub)
                    else:
                        lines.append(stripped)
        finally:
            _expanding.pop()
        return lines

    def get_before_print(self):
        macro = Macros(self.common_dir, self.head_type,
                       self.use_nozzle0, self.use_nozzle1, self.safeties)
        return macro.get_contents("before_print")

    def get_after_print(self):
        macro = Macros(self.common_dir, self.head_type,
                       self.use_nozzle0, self.use_nozzle1, self.safeties)
        return macro.get_contents("after_print")


class PostProcessor:
    TOOL_PATTERN = re.compile(r"^T([0-9]+)\b")
    GCODE_LINE = re.compile(r"^(G0|G1|G28|G90|G91|M82|M83|M104|M109|M106|M107|M140|M190|M84)\b")
    COMMENT = re.compile(r"^;")

    def __init__(self, common_dir, head_type="RBX01-SM", use_nozzle0=True,
                 use_nozzle1=False, safeties=True, nozzle0_diameter=0.4,
                 bed_temp=60, nozzle_temp=210, print_speed=50):
        self.common_dir = common_dir
        self.head_type = head_type
        self.use_nozzle0 = use_nozzle0
        self.use_nozzle1 = use_nozzle1
        self.safeties = safeties
        self.nozzle0_diameter = nozzle0_diameter
        self.bed_temp = bed_temp
        self.nozzle_temp = nozzle_temp
        self.print_speed = print_speed

    def process(self, input_gcode):
        mac = Macros(self.common_dir, self.head_type,
                     self.use_nozzle0, self.use_nozzle1, self.safeties)

        lines = []
        lines.append("; Post-processed for CEL Robox by robox_print")
        lines.append(f"; Head: {self.head_type}")
        lines.append(f"; Safety: {'ON' if self.safeties else 'OFF'}")
        lines.append(f"; Nozzle0: {self.use_nozzle0}, Nozzle1: {self.use_nozzle1}")
        lines.append("")

        before_print_lines = mac.get_before_print()
        for i, line in enumerate(before_print_lines):
            command_part = line.split(';')[0].strip()
            command_upper = command_part.upper()

            # Set temperatures
            if command_upper.startswith("M139") and "S" not in command_upper:
                before_print_lines[i] = f"M139 S{self.bed_temp}"
            elif command_upper.startswith("M103") and "S" not in command_upper and "T" not in command_upper:
                before_print_lines[i] = f"M103 S{self.nozzle_temp}"
            elif command_upper.startswith("M140") and "S" not in command_upper:
                before_print_lines[i] = f"M140 S{self.bed_temp}"
            elif command_upper.startswith("M104") and "S" not in command_upper and "T" not in command_upper:
                before_print_lines[i] = f"M104 S{self.nozzle_temp}"

            # Keep T0 (tool 0) - it sets correct tool offsets from head EEPROM.
            # On SM heads, tool-1 EEPROM offsets are unprogrammed and corrupt
            # homing (Z reads 90mm -> Z crashes into lid). T0 is harmless now
            # that the B axis gate is free.
            if command_upper.startswith("T1"):
                before_print_lines[i] = ""
                continue

            # X home switch is FAULTY (does not trip). G28 X makes home_axis()
            # grind the motor until the abandon counter (339mm) expires - the
            # head bangs into the left wall. Replace with a clamped move to
            # X-100: the firmware clamps carriage to [0,226] (nozzle -14),
            # so this safely drives to the left stop without grinding.
            if command_upper.startswith("G28 X"):
                before_print_lines[i] = "G0 X-100 F1500"
                continue

        lines.extend(before_print_lines)
        lines.append("")

        # Track tool state for nozzle open/close
        current_tool = 0 if not self.use_nozzle1 or self.use_nozzle0 else 1
        first_tool = True
        last_x = 0.0

        for raw_line in input_gcode.split("\n"):
            stripped = raw_line.strip()

            if not stripped or self.COMMENT.match(stripped):
                lines.append(stripped)
                continue

            # Detect tool change - keep T0 (correct offsets), strip T1 (garbage offsets on SM)
            m = self.TOOL_PATTERN.match(stripped)
            if m:
                new_tool = int(m.group(1))
                if new_tool == 1:
                    continue

            # Replace G28 X anywhere in the gcode - X home switch is faulty,
            # firmware would grind 339mm into the left wall. Use clamped move.
            if stripped.upper().startswith("G28 X"):
                stripped = "G0 X-100 F1500"

            # Map Tn -> extrusion type for D/E
            if current_tool == 0:
                stripped = re.sub(r" E(-?[0-9.]+\s*E)?", " D", stripped)
            elif current_tool == 1:
                stripped = re.sub(r" D(-?[0-9.]+\s*D)?", " E", stripped)

            # Clamp axis moves to measured physical limits (nozzle coordinates,
            # from axis_limits.log measurement on this machine):
            #   X: [-14, 212]  (carriage [0, 226], tool offset 14; X home switch
            #                   is FAULTY so we must not rely on it - G28 X is
            #                   replaced with a clamped move in before_print)
            #   Y: [-4, 151]   (carriage [0, 155], tool offset 4)
            #   Z: [0, 75]     (above 79 the head presses the lid arm; firmware
            #                   only reports ERROR_Z_TOP_SWITCH, does not stop)
            # COUPLED X+Z limit (measured z_contact_map.log 2026-07-31):
            #   The head hits the lid mounting bracket at the top-LEFT corner.
            #   Z+ top switch contact measured at: X=-14 -> 88mm, X=105 -> 90mm,
            #   X=212 -> 88mm. With the head at the X left limit, Z must be
            #   capped lower. Safe envelope: for X < 0, cap Z at 40mm; for
            #   X >= 0, cap Z at 75mm. This prevents the top-left bracket hit.
            if stripped.startswith(("G0", "G1")):
                m = re.search(r"\bX(-?[0-9.]+)", stripped)
                if m:
                    x_val = float(m.group(1))
                    if x_val > 212.0:
                        stripped = stripped.replace(m.group(0), "X212.0")
                    elif x_val < -14.0:
                        stripped = stripped.replace(m.group(0), "X-14.0")
                m = re.search(r"\bY(-?[0-9.]+)", stripped)
                if m:
                    y_val = float(m.group(1))
                    if y_val > 151.0:
                        stripped = stripped.replace(m.group(0), "Y151.0")
                    elif y_val < -4.0:
                        stripped = stripped.replace(m.group(0), "Y-4.0")
                m = re.search(r"\bZ(-?[0-9.]+)", stripped)
                if m:
                    z_val = float(m.group(1))
                    # Coupled limit: when X is at the left, Z is capped lower
                    # (lid mounting bracket collision at top-left corner)
                    if "X" in stripped:
                        xm = re.search(r"\bX(-?[0-9.]+)", stripped)
                        current_x = float(xm.group(1)) if xm else 0.0
                    else:
                        current_x = last_x
                    z_max = 40.0 if current_x < 0.0 else 75.0
                    if z_val > z_max:
                        stripped = stripped.replace(m.group(0), f"Z{z_max:.1f}")
                    elif z_val < 0.0:
                        stripped = stripped.replace(m.group(0), "Z0.0")
                # Track X position for coupled Z clamp on moves without X
                mx = re.search(r"\bX(-?[0-9.]+)", stripped)
                if mx:
                    last_x = float(mx.group(1))

            lines.append(stripped)

        lines.append("")

        # After-print macros
        lines.extend(mac.get_after_print())
        lines.append("M84")  # Disable motors

        return "\n".join(lines)
