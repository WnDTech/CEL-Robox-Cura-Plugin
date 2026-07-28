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

        # Before-print macros
        lines.extend(mac.get_before_print())
        lines.append("")

        # Set temperatures from user's selected profile (M104/M140 from Cura
        # come after the macro, so inject them here to take effect immediately)
        lines.append(f"M104 S{self.nozzle_temp}")
        lines.append(f"M140 S{self.bed_temp}")
        lines.append("")

        # Track tool state for nozzle open/close
        # Default to tool 0 (D extruder) for single-material prints
        current_tool = 0 if not self.use_nozzle1 or self.use_nozzle0 else 1
        first_tool = True

        for raw_line in input_gcode.split("\n"):
            stripped = raw_line.strip()

            if not stripped or self.COMMENT.match(stripped):
                lines.append(stripped)
                continue

            # Detect tool change
            m = self.TOOL_PATTERN.match(stripped)
            if m:
                new_tool = int(m.group(1))
                if current_tool is not None and new_tool != current_tool:
                    lines.append("G0 B0")  # Close current nozzle
                    lines.append("G0 B1")  # Open new nozzle
                current_tool = new_tool

            # Map Tn -> extrusion type for D/E
            if current_tool == 0:
                stripped = re.sub(r" E(-?[0-9.]+\s*E)?", " D", stripped)
            elif current_tool == 1:
                stripped = re.sub(r" D(-?[0-9.]+\s*D)?", " E", stripped)

            lines.append(stripped)

        lines.append("")

        # After-print macros
        lines.extend(mac.get_after_print())
        lines.append("M84")  # Disable motors

        return "\n".join(lines)
