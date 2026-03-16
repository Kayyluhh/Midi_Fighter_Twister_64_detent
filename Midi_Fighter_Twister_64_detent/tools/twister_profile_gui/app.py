import json
import queue
import random
import time
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import BOTH, LEFT, X, Y, BooleanVar, Canvas, IntVar, StringVar, Text, Tk, Toplevel, filedialog, messagebox
from tkinter import colorchooser, simpledialog
from tkinter import ttk

import mido


MANUFACTURER_ID = [0x00, 0x01, 0x79]
SYSEX_COMMAND_PUSH_CONF = 0x01
SYSEX_COMMAND_PULL_CONF = 0x02
SYSEX_COMMAND_BULK_XFER = 0x04

NUM_BANKS = 4
ENCODERS_PER_BANK = 16
TOTAL_ENCODERS = NUM_BANKS * ENCODERS_PER_BANK

# Modifier bit masks observed from Tk events on desktop platforms.
MOD_SHIFT = 0x0001
MOD_TOGGLE = 0x0004 | 0x0008 | 0x0010

# Global tag map in firmware config.c / config.h.
GLOBAL_TAGS = {
    "midi_channel": 0,
    "side_is_banked": 1,
    "side_func_1": 2,
    "side_func_2": 3,
    "side_func_3": 4,
    "side_func_4": 5,
    "side_func_5": 6,
    "side_func_6": 7,
    "super_start": 8,
    "super_end": 9,
    "rgb_brightness": 31,
    "ind_brightness": 32,
    "soft_takeover": 33,
    "bank_wrap_mode": 34,
    "shift_page_latch": 35,
}

# Encoder tag map from config.c Bulk Transfer response (10..24).
ENCODER_TAGS = {
    "has_detent": 10,
    "movement": 11,
    "switch_action_type": 12,
    "switch_midi_channel": 13,
    "switch_midi_number": 14,
    "switch_midi_type": 15,
    "encoder_midi_channel": 16,
    "encoder_midi_number": 17,
    "encoder_midi_type": 18,
    "active_color": 19,
    "inactive_color": 20,
    "detent_color": 21,
    "indicator_display_type": 22,
    "is_super_knob": 23,
    "encoder_shift_midi_channel": 24,
}

SCOPE_FIELDS = {
    "All Fields": list(ENCODER_TAGS.keys()),
    "Colors Only": ["active_color", "inactive_color", "detent_color"],
    "MIDI Only": [
        "switch_midi_channel",
        "switch_midi_number",
        "switch_midi_type",
        "encoder_midi_channel",
        "encoder_midi_number",
        "encoder_midi_type",
        "encoder_shift_midi_channel",
    ],
    "Behavior Only": [
        "has_detent",
        "movement",
        "switch_action_type",
        "indicator_display_type",
        "is_super_knob",
    ],
}


def clamp7(value: int) -> int:
    return max(0, min(127, int(value)))


@dataclass
class EncoderConfig:
    has_detent: int = 0
    movement: int = 0
    switch_action_type: int = 0
    switch_midi_channel: int = 1
    switch_midi_number: int = 0
    switch_midi_type: int = 0
    encoder_midi_channel: int = 1
    encoder_midi_number: int = 0
    encoder_midi_type: int = 1
    active_color: int = 25
    inactive_color: int = 0
    detent_color: int = 63
    indicator_display_type: int = 2
    is_super_knob: int = 0
    encoder_shift_midi_channel: int = 5

    def apply_tag_value(self, tag: int, value: int) -> None:
        for field_name, field_tag in ENCODER_TAGS.items():
            if field_tag == tag:
                setattr(self, field_name, clamp7(value))
                return

    def to_tag_pairs(self) -> list[int]:
        pairs: list[int] = []
        for field_name, field_tag in ENCODER_TAGS.items():
            pairs.extend([field_tag, clamp7(getattr(self, field_name))])
        return pairs


@dataclass
class Profile:
    globals: dict[str, int] = field(default_factory=lambda: {
        "midi_channel": 4,
        "side_is_banked": 1,
        "side_func_1": 0,
        "side_func_2": 0,
        "side_func_3": 0,
        "side_func_4": 0,
        "side_func_5": 0,
        "side_func_6": 0,
        "super_start": 64,
        "super_end": 127,
        "rgb_brightness": 127,
        "ind_brightness": 127,
        "soft_takeover": 1,
        "bank_wrap_mode": 1,
        "shift_page_latch": 0,
    })
    encoders: list[EncoderConfig] = field(default_factory=lambda: [EncoderConfig() for _ in range(TOTAL_ENCODERS)])

    def to_json_dict(self) -> dict:
        return {
            "globals": self.globals,
            "encoders": [asdict(e) for e in self.encoders],
        }

    @staticmethod
    def from_json_dict(data: dict) -> "Profile":
        profile = Profile()
        profile.globals.update({k: clamp7(v) for k, v in data.get("globals", {}).items() if k in GLOBAL_TAGS})
        encoders_data = data.get("encoders", [])
        for i, row in enumerate(encoders_data[:TOTAL_ENCODERS]):
            current = profile.encoders[i]
            for key in ENCODER_TAGS:
                if key in row:
                    setattr(current, key, clamp7(row[key]))
        return profile


class TwisterMidiClient:
    def __init__(self, event_queue: queue.Queue):
        self.event_queue = event_queue
        self.in_port = None
        self.out_port = None
        self.connected = False

    @staticmethod
    def list_input_ports() -> list[str]:
        return list(mido.get_input_names())

    @staticmethod
    def list_output_ports() -> list[str]:
        return list(mido.get_output_names())

    def connect(self, input_name: str, output_name: str) -> None:
        self.disconnect()
        self.in_port = mido.open_input(input_name, callback=self._on_message)
        self.out_port = mido.open_output(output_name)
        self.connected = True

    def disconnect(self) -> None:
        if self.in_port is not None:
            self.in_port.close()
            self.in_port = None
        if self.out_port is not None:
            self.out_port.close()
            self.out_port = None
        self.connected = False

    def _on_message(self, msg: mido.Message) -> None:
        if msg.type != "sysex":
            return

        data = list(msg.data)
        if len(data) < 4:
            return

        if data[:3] != MANUFACTURER_ID:
            return

        command = data[3]
        payload = data[4:]
        self.event_queue.put({"type": "sysex", "command": command, "payload": payload})

    def _send_sysex(self, command: int, payload: list[int]) -> None:
        if not self.connected or self.out_port is None:
            raise RuntimeError("Not connected")
        sysex_data = MANUFACTURER_ID + [command] + [clamp7(x) for x in payload]
        self.out_port.send(mido.Message("sysex", data=sysex_data))
        self.event_queue.put({"type": "sysex_tx", "command": command, "payload": [clamp7(x) for x in payload]})

    def pull_global_config(self) -> None:
        self._send_sysex(SYSEX_COMMAND_PULL_CONF, [0x00])

    def push_global_config(self, globals_map: dict[str, int]) -> None:
        payload: list[int] = []
        for name, tag in GLOBAL_TAGS.items():
            payload.extend([tag, clamp7(globals_map.get(name, 0))])
        self._send_sysex(SYSEX_COMMAND_PUSH_CONF, payload)

    def pull_encoder(self, sysex_tag: int) -> None:
        # Firmware checks length > 2 before handling pull; add a dummy byte.
        self._send_sysex(SYSEX_COMMAND_BULK_XFER, [0x01, clamp7(sysex_tag), 0x00])

    def push_encoder(self, sysex_tag: int, encoder_config: EncoderConfig) -> None:
        pairs = encoder_config.to_tag_pairs()

        # Firmware supports payload size <= 24 bytes per part.
        chunks = [pairs[i:i + 24] for i in range(0, len(pairs), 24)]
        total = len(chunks)
        for part_idx, chunk in enumerate(chunks, start=1):
            payload = [
                0x00,  # push
                clamp7(sysex_tag),
                clamp7(part_idx),
                clamp7(total),
                clamp7(len(chunk)),
            ] + chunk
            self._send_sysex(SYSEX_COMMAND_BULK_XFER, payload)
            time.sleep(0.01)


def find_color_map_file() -> Path | None:
    cwd = Path.cwd()
    candidates = [
        cwd / "src" / "colorMap.c",
        cwd.parent / "src" / "colorMap.c",
        cwd.parent.parent / "src" / "colorMap.c",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def parse_color_map7_from_c() -> list[tuple[int, int, int]]:
    path = find_color_map_file()
    if path is None:
        return fallback_palette()

    text = path.read_text(encoding="utf-8", errors="ignore")
    marker = "const uint8_t colorMap7[128][3]"
    start = text.find(marker)
    if start < 0:
        return fallback_palette()

    brace_start = text.find("{", start)
    if brace_start < 0:
        return fallback_palette()

    values: list[tuple[int, int, int]] = []
    i = brace_start
    length = len(text)
    while i < length and len(values) < 128:
        if text[i] == "{":
            j = text.find("}", i + 1)
            if j < 0:
                break
            body = text[i + 1:j]
            nums = []
            current = ""
            for ch in body:
                if ch.isdigit():
                    current += ch
                elif current:
                    nums.append(int(current))
                    current = ""
            if current:
                nums.append(int(current))
            if len(nums) >= 3:
                # Firmware stores color in Blue, Green, Red byte order.
                b, g, r = nums[:3]
                values.append((r, g, b))
            i = j
        i += 1

    if len(values) != 128:
        return fallback_palette()
    return values


def fallback_palette() -> list[tuple[int, int, int]]:
    palette: list[tuple[int, int, int]] = [(0, 0, 0)]
    for idx in range(1, 127):
        hue = (idx / 127.0) * 360.0
        c = 1.0
        x = c * (1 - abs(((hue / 60.0) % 2) - 1))
        if hue < 60:
            r1, g1, b1 = c, x, 0
        elif hue < 120:
            r1, g1, b1 = x, c, 0
        elif hue < 180:
            r1, g1, b1 = 0, c, x
        elif hue < 240:
            r1, g1, b1 = 0, x, c
        elif hue < 300:
            r1, g1, b1 = x, 0, c
        else:
            r1, g1, b1 = c, 0, x
        palette.append((int(r1 * 255), int(g1 * 255), int(b1 * 255)))
    palette.append((225, 220, 188))
    return palette


def nearest_palette_index(rgb: tuple[int, int, int], palette: list[tuple[int, int, int]]) -> int:
    r, g, b = rgb
    best_idx = 0
    best_dist = 10**12
    for i, (pr, pg, pb) in enumerate(palette):
        dr = r - pr
        dg = g - pg
        db = b - pb
        dist = dr * dr + dg * dg + db * db
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


class TwisterGui(Tk):
    def __init__(self):
        super().__init__()
        self.title("Midi Fighter Twister Profile + RGB GUI")
        self.geometry("1350x840")
        self.minsize(1180, 760)

        self.events: queue.Queue = queue.Queue()
        self.client = TwisterMidiClient(self.events)
        self.profile = Profile()
        self.device_profile = Profile.from_json_dict(self.profile.to_json_dict())
        self.palette = parse_color_map7_from_c()

        self.input_port_var = StringVar(value="")
        self.output_port_var = StringVar(value="")
        self.status_var = StringVar(value="Disconnected")
        self.bank_var = IntVar(value=1)
        self.encoder_var = IntVar(value=1)
        self.apply_scope_var = StringVar(value="All Fields")
        self.dry_run_var = BooleanVar(value=False)
        self.confirm_threshold_var = IntVar(value=12)

        self.context_var = StringVar(value="")
        self.selection_var = StringVar(value="")

        self.fields: dict[str, IntVar] = {name: IntVar(value=getattr(self.profile.encoders[0], name)) for name in ENCODER_TAGS}
        self.global_fields: dict[str, IntVar] = {name: IntVar(value=self.profile.globals[name]) for name in GLOBAL_TAGS}

        active = self._selected_index()
        self.selected_encoders: set[int] = {active}
        self.last_selected_encoder = active
        self._suppress_var_selection = False
        self._history_lock = False
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.max_history = 80

        self.knob_canvas = None
        self.knob_items: dict[int, dict[str, int]] = {}
        self.knob_centers: dict[int, tuple[float, float, float]] = {}
        self.drag_rect_id = None
        self.drag_start: tuple[int, int] | None = None
        self.drag_mode = "replace"
        self.drag_base_selection: set[int] = set()
        self.dragging = False
        self.drag_clicked_index: int | None = None
        self.copied_encoder: EncoderConfig | None = None
        self.clipboard_slot_var = IntVar(value=1)
        self.clipboard_slots: dict[int, EncoderConfig | None] = {1: None, 2: None, 3: None, 4: None}
        self.preset_file = Path(__file__).with_name("presets.json")
        self.backup_dir = Path(__file__).with_name("backups")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.named_presets: dict[str, dict] = self._load_named_presets()
        self.preset_name_var = StringVar(value="")
        self.preset_select_var = StringVar(value="")
        self.bank_tabs = None
        self.mini_map = None
        self.selection_pulse_phase = 0
        self.selection_pulse_dir = 1
        self.midi_monitor_window: Toplevel | None = None
        self.midi_monitor_text: Text | None = None
        self.midi_log_enabled = BooleanVar(value=True)
        self.midi_log_lines: list[str] = []
        self.midi_log_max_lines = 800

        self._build_ui()
        self.refresh_ports()
        self._load_encoder_fields_from_model()
        self._refresh_color_previews()
        self._draw_knob_grid()
        self._draw_mini_map()
        self._update_context_labels()
        self._animate_selection_pulse()
        self.after(40, self._poll_events)

        self.bank_var.trace_add("write", self._on_var_changed)
        self.encoder_var.trace_add("write", self._on_var_changed)
        self.bind("<Command-z>", lambda _e: self.undo())
        self.bind("<Command-Z>", lambda _e: self.redo())
        self.bind("<Control-z>", lambda _e: self.undo())
        self.bind("<Control-y>", lambda _e: self.redo())

    def _animate_selection_pulse(self) -> None:
        self.selection_pulse_phase += self.selection_pulse_dir
        if self.selection_pulse_phase >= 8:
            self.selection_pulse_phase = 8
            self.selection_pulse_dir = -1
        elif self.selection_pulse_phase <= 0:
            self.selection_pulse_phase = 0
            self.selection_pulse_dir = 1
        self._update_knob_visuals()
        self.after(90, self._animate_selection_pulse)

    def _build_ui(self) -> None:
        root = ttk.Frame(self)
        root.pack(fill=BOTH, expand=True, padx=10, pady=10)

        top = ttk.LabelFrame(root, text="MIDI Connection")
        top.pack(fill=X, pady=(0, 10))

        ttk.Label(top, text="Input").grid(row=0, column=0, padx=6, pady=6)
        self.input_combo = ttk.Combobox(top, textvariable=self.input_port_var, width=40, state="readonly")
        self.input_combo.grid(row=0, column=1, padx=6, pady=6)

        ttk.Label(top, text="Output").grid(row=0, column=2, padx=6, pady=6)
        self.output_combo = ttk.Combobox(top, textvariable=self.output_port_var, width=40, state="readonly")
        self.output_combo.grid(row=0, column=3, padx=6, pady=6)

        ttk.Button(top, text="Refresh Ports", command=self.refresh_ports).grid(row=0, column=4, padx=6, pady=6)
        ttk.Button(top, text="Connect", command=self.connect).grid(row=0, column=5, padx=6, pady=6)
        ttk.Button(top, text="Disconnect", command=self.disconnect).grid(row=0, column=6, padx=6, pady=6)
        ttk.Button(top, text="MIDI Monitor", command=self.open_midi_monitor).grid(row=0, column=7, padx=6, pady=6)

        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=8, padx=8, pady=6, sticky="w")

        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill=BOTH, expand=True)

        left = ttk.Frame(body)
        body.add(left, weight=3)

        right = ttk.Frame(body)
        body.add(right, weight=2)

        profile_frame = ttk.LabelFrame(left, text="Profile")
        profile_frame.pack(fill=BOTH, expand=True)

        toolbar = ttk.Frame(profile_frame)
        toolbar.pack(fill=X, padx=8, pady=8)

        ttk.Button(toolbar, text="Load JSON", command=self.load_json).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Save JSON", command=self.save_json).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Restore Last Snapshot", command=self.restore_last_snapshot).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Export Diagnostics", command=self.export_diagnostics_report).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Import Bundle", command=self.import_everything_bundle).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Export Everything", command=self.export_everything_bundle).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Import Bank Snippet", command=self.import_bank_snippet).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Export Bank Snippet", command=self.export_bank_snippet).pack(side=LEFT, padx=4)
        ttk.Separator(toolbar, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Button(toolbar, text="Undo", command=self.undo).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Redo", command=self.redo).pack(side=LEFT, padx=4)
        ttk.Separator(toolbar, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Button(toolbar, text="Pull Global", command=self.pull_global).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Push Global", command=self.push_global).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Pull Bank", command=self.pull_bank).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Push Bank", command=self.push_bank).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Pull All Banks", command=self.pull_all_banks).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Push All Banks", command=self.push_all_banks).pack(side=LEFT, padx=4)

        selector = ttk.Frame(profile_frame)
        selector.pack(fill=X, padx=8)

        ttk.Label(selector, text="Bank").pack(side=LEFT)
        ttk.Spinbox(selector, from_=1, to=4, width=4, textvariable=self.bank_var, command=self._on_selection_changed).pack(side=LEFT, padx=4)
        ttk.Label(selector, text="Encoder").pack(side=LEFT, padx=(14, 0))
        ttk.Spinbox(selector, from_=1, to=16, width=4, textvariable=self.encoder_var, command=self._on_selection_changed).pack(side=LEFT, padx=4)
        ttk.Label(selector, text="Scope").pack(side=LEFT, padx=(14, 0))
        ttk.Combobox(selector, textvariable=self.apply_scope_var, values=list(SCOPE_FIELDS.keys()), width=14, state="readonly").pack(side=LEFT, padx=4)
        ttk.Button(selector, text="Load Active", command=self._load_encoder_fields_from_model).pack(side=LEFT, padx=8)
        ttk.Button(selector, text="Apply To Selected", command=self._apply_encoder_fields_to_model).pack(side=LEFT, padx=4)
        ttk.Button(selector, text="Preview Diff", command=self.preview_diff_selected).pack(side=LEFT, padx=4)
        ttk.Button(selector, text="Send Selected", command=self.push_selected_encoder).pack(side=LEFT, padx=4)

        safety = ttk.Frame(profile_frame)
        safety.pack(fill=X, padx=8, pady=(6, 2))
        ttk.Checkbutton(safety, text="Dry Run", variable=self.dry_run_var).pack(side=LEFT)
        ttk.Label(safety, text="Confirm if sending >=").pack(side=LEFT, padx=(10, 2))
        ttk.Spinbox(safety, from_=1, to=64, width=4, textvariable=self.confirm_threshold_var).pack(side=LEFT)
        ttk.Label(safety, text="encoders").pack(side=LEFT, padx=(4, 0))

        quick = ttk.Frame(profile_frame)
        quick.pack(fill=X, padx=8, pady=(6, 4))
        ttk.Label(quick, text="Quick Select").pack(side=LEFT)
        ttk.Button(quick, text="Row", command=self.select_row_from_active).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="Column", command=self.select_column_from_active).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="All 16", command=self.select_all_in_bank).pack(side=LEFT, padx=4)
        ttk.Separator(quick, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Button(quick, text="Copy Active", command=self.copy_active_encoder).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="Paste To Selected", command=self.paste_to_selected).pack(side=LEFT, padx=4)
        ttk.Separator(quick, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Label(quick, text="Slot").pack(side=LEFT)
        ttk.Spinbox(quick, from_=1, to=4, width=3, textvariable=self.clipboard_slot_var).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="Copy To Slot", command=self.copy_to_slot).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="Paste Slot To Selected", command=self.paste_from_slot).pack(side=LEFT, padx=4)

        presets = ttk.Frame(profile_frame)
        presets.pack(fill=X, padx=8, pady=(0, 6))
        ttk.Label(presets, text="Preset Name").pack(side=LEFT)
        ttk.Entry(presets, textvariable=self.preset_name_var, width=16).pack(side=LEFT, padx=4)
        ttk.Button(presets, text="Save Active Preset", command=self.save_named_preset).pack(side=LEFT, padx=4)
        ttk.Separator(presets, orient="vertical").pack(side=LEFT, fill=Y, padx=8)
        self.preset_combo = ttk.Combobox(presets, textvariable=self.preset_select_var, values=sorted(self.named_presets.keys()), width=16, state="readonly")
        self.preset_combo.pack(side=LEFT, padx=4)
        ttk.Button(presets, text="Apply Preset To Selected", command=self.apply_named_preset).pack(side=LEFT, padx=4)
        ttk.Button(presets, text="Delete Preset", command=self.delete_named_preset).pack(side=LEFT, padx=4)
        ttk.Separator(presets, orient="vertical").pack(side=LEFT, fill=Y, padx=8)
        ttk.Button(presets, text="Import Presets", command=self.import_named_presets).pack(side=LEFT, padx=4)
        ttk.Button(presets, text="Export Presets", command=self.export_named_presets).pack(side=LEFT, padx=4)

        tabs_wrap = ttk.Frame(profile_frame)
        tabs_wrap.pack(fill=X, padx=8, pady=(0, 6))
        self.bank_tabs = ttk.Notebook(tabs_wrap)
        self.bank_tabs.pack(fill=X)
        for i in range(1, 5):
            self.bank_tabs.add(ttk.Frame(self.bank_tabs), text=f"Bank {i}")
        self.bank_tabs.bind("<<NotebookTabChanged>>", self._on_bank_tab_changed)

        context_line = ttk.Frame(profile_frame)
        context_line.pack(fill=X, padx=8, pady=(6, 4))
        ttk.Label(context_line, textvariable=self.context_var).pack(side=LEFT)

        selection_line = ttk.Frame(profile_frame)
        selection_line.pack(fill=X, padx=8, pady=(0, 8))
        ttk.Label(selection_line, textvariable=self.selection_var).pack(side=LEFT)

        content = ttk.Panedwindow(profile_frame, orient="horizontal")
        content.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))

        knob_box = ttk.LabelFrame(content, text="Graphical Bank View")
        content.add(knob_box, weight=3)

        self.knob_canvas = Canvas(knob_box, width=650, height=620, bg="#121419", highlightthickness=1, highlightbackground="#40444f")
        self.knob_canvas.pack(fill=BOTH, expand=True, padx=8, pady=8)
        self.knob_canvas.bind("<ButtonPress-1>", self._on_knob_press)
        self.knob_canvas.bind("<B1-Motion>", self._on_knob_drag)
        self.knob_canvas.bind("<ButtonRelease-1>", self._on_knob_release)

        hint = (
            "Selection: Click = single | Shift+Click = range add | Cmd+Click = toggle | "
            "Drag = box select (with Shift/Cmd modifiers)."
        )
        ttk.Label(knob_box, text=hint).pack(anchor="w", padx=10, pady=(0, 8))

        editor_box = ttk.LabelFrame(content, text="Encoder Settings")
        content.add(editor_box, weight=2)

        fields_box = ttk.Frame(editor_box)
        fields_box.pack(side=LEFT, fill=BOTH, expand=True, padx=(8, 2), pady=8)

        row = 0
        for key in ENCODER_TAGS:
            ttk.Label(fields_box, text=key).grid(row=row, column=0, sticky="w", padx=6, pady=3)
            ttk.Spinbox(fields_box, from_=0, to=127, textvariable=self.fields[key], width=10).grid(row=row, column=1, sticky="w", padx=6, pady=3)
            row += 1

        color_box = ttk.LabelFrame(editor_box, text="RGB Palette Controls")
        color_box.pack(side=LEFT, fill=Y, padx=(8, 8), pady=8)

        self.preview_active = Canvas(color_box, width=120, height=44, bg="#000000", highlightthickness=1, highlightbackground="#333333")
        self.preview_inactive = Canvas(color_box, width=120, height=44, bg="#000000", highlightthickness=1, highlightbackground="#333333")
        self.preview_detent = Canvas(color_box, width=120, height=44, bg="#000000", highlightthickness=1, highlightbackground="#333333")

        ttk.Label(color_box, text="Active").pack(pady=(8, 2))
        self.preview_active.pack(padx=8)
        ttk.Button(color_box, text="Pick Active RGB", command=lambda: self.pick_rgb_for("active_color")).pack(pady=4)

        ttk.Label(color_box, text="Inactive").pack(pady=(8, 2))
        self.preview_inactive.pack(padx=8)
        ttk.Button(color_box, text="Pick Inactive RGB", command=lambda: self.pick_rgb_for("inactive_color")).pack(pady=4)

        ttk.Label(color_box, text="Detent").pack(pady=(8, 2))
        self.preview_detent.pack(padx=8)
        ttk.Button(color_box, text="Pick Detent RGB", command=lambda: self.pick_rgb_for("detent_color")).pack(pady=4)

        ttk.Button(color_box, text="Refresh Swatches", command=self._refresh_color_previews).pack(pady=4)
        ttk.Button(color_box, text="Gradient Fill Selected", command=self.gradient_fill_selected).pack(pady=2)
        ttk.Button(color_box, text="Randomize Colors", command=self.randomize_selected_colors).pack(pady=2)
        ttk.Button(color_box, text="Rotate Hue Index", command=self.rotate_selected_hue).pack(pady=4)

        global_frame = ttk.LabelFrame(right, text="Global Settings + Mini Map")
        global_frame.pack(fill=BOTH, expand=True)

        gr = 0
        for key in GLOBAL_TAGS:
            ttk.Label(global_frame, text=key).grid(row=gr, column=0, sticky="w", padx=8, pady=4)
            ttk.Spinbox(global_frame, from_=0, to=127, textvariable=self.global_fields[key], width=10).grid(row=gr, column=1, sticky="w", padx=8, pady=4)
            gr += 1

        ttk.Separator(global_frame, orient="horizontal").grid(row=gr, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        gr += 1
        ttk.Label(global_frame, text="64 Encoder Mini Map (click to jump)").grid(row=gr, column=0, columnspan=2, sticky="w", padx=8)
        gr += 1
        self.mini_map = Canvas(global_frame, width=300, height=170, bg="#0f1219", highlightthickness=1, highlightbackground="#40444f")
        self.mini_map.grid(row=gr, column=0, columnspan=2, padx=8, pady=(4, 8), sticky="ew")
        self.mini_map.bind("<Button-1>", self._on_mini_map_click)

        help_box = ttk.LabelFrame(right, text="Usage")
        help_box.pack(fill=BOTH, expand=False, pady=(8, 0))
        msg = (
            "1) Connect to Twister ports and pull data.\n"
            "2) Select knobs with click/drag/Shift/Cmd or quick actions.\n"
            "3) Use scope, presets, slots, and color tools for batch edits.\n"
            "4) Preview diff, then send selected/bank/all.\n"
            "5) Use Dry Run and confirmation threshold for safety."
        )
        ttk.Label(help_box, text=msg, justify="left").pack(anchor="w", padx=8, pady=8)

    def refresh_ports(self) -> None:
        inputs = self.client.list_input_ports()
        outputs = self.client.list_output_ports()
        self.input_combo["values"] = inputs
        self.output_combo["values"] = outputs

        if inputs and not self.input_port_var.get():
            self.input_port_var.set(inputs[0])
        if outputs and not self.output_port_var.get():
            self.output_port_var.set(outputs[0])

    def connect(self) -> None:
        try:
            self.client.connect(self.input_port_var.get(), self.output_port_var.get())
            self.status_var.set("Connected")
        except Exception as exc:
            messagebox.showerror("Connection Error", str(exc))

    def disconnect(self) -> None:
        self.client.disconnect()
        self.status_var.set("Disconnected")

    def _selected_index(self) -> int:
        bank = max(1, min(NUM_BANKS, int(self.bank_var.get())))
        encoder = max(1, min(ENCODERS_PER_BANK, int(self.encoder_var.get())))
        return (bank - 1) * ENCODERS_PER_BANK + (encoder - 1)

    def _set_active_index(self, idx: int) -> None:
        bank = idx // ENCODERS_PER_BANK + 1
        encoder = idx % ENCODERS_PER_BANK + 1
        self._suppress_var_selection = True
        self.bank_var.set(bank)
        self.encoder_var.set(encoder)
        if self.bank_tabs is not None:
            self.bank_tabs.select(bank - 1)
        self._suppress_var_selection = False

    def _capture_state(self) -> dict:
        return {
            "profile": self.profile.to_json_dict(),
            "selected": sorted(self.selected_encoders),
            "active": self._selected_index(),
        }

    def _push_history(self) -> None:
        if self._history_lock:
            return
        self.undo_stack.append(self._capture_state())
        if len(self.undo_stack) > self.max_history:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _restore_state(self, snap: dict) -> None:
        self._history_lock = True
        self.profile = Profile.from_json_dict(snap["profile"])
        self.selected_encoders = set(snap.get("selected", [])) or {snap.get("active", 0)}
        self._set_active_index(int(snap.get("active", 0)))
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()
        self._history_lock = False

    def undo(self) -> None:
        if not self.undo_stack:
            return
        self.redo_stack.append(self._capture_state())
        self._restore_state(self.undo_stack.pop())

    def redo(self) -> None:
        if not self.redo_stack:
            return
        self.undo_stack.append(self._capture_state())
        self._restore_state(self.redo_stack.pop())

    def _on_var_changed(self, *_args) -> None:
        if self._suppress_var_selection:
            return
        self._on_selection_changed()

    def _on_bank_tab_changed(self, _event) -> None:
        if self.bank_tabs is None or self._suppress_var_selection:
            return
        tab_idx = self.bank_tabs.index(self.bank_tabs.select())
        self._suppress_var_selection = True
        self.bank_var.set(tab_idx + 1)
        self._suppress_var_selection = False
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        idx = self._selected_index()
        if idx not in self.selected_encoders:
            self.selected_encoders = {idx}
        self.last_selected_encoder = idx
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()
        self._update_context_labels()

    def _scoped_fields(self) -> list[str]:
        return SCOPE_FIELDS.get(self.apply_scope_var.get(), SCOPE_FIELDS["All Fields"])

    def _apply_encoder_fields_to_model(self) -> None:
        self._push_history()
        fields = self._scoped_fields()
        targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
        for idx in targets:
            enc = self.profile.encoders[idx]
            for key in fields:
                setattr(enc, key, clamp7(self.fields[key].get()))
        self._refresh_color_previews()
        self._draw_knob_grid()
        self._draw_mini_map()

    def _load_encoder_fields_from_model(self) -> None:
        idx = self._selected_index()
        enc = self.profile.encoders[idx]
        for key in ENCODER_TAGS:
            self.fields[key].set(clamp7(getattr(enc, key)))
        self._refresh_color_previews()
        self._update_context_labels()

    def _refresh_color_previews(self) -> None:
        a = clamp7(self.fields["active_color"].get())
        i = clamp7(self.fields["inactive_color"].get())
        d = clamp7(self.fields["detent_color"].get())
        self.preview_active.configure(bg=self._palette_hex(a))
        self.preview_inactive.configure(bg=self._palette_hex(i))
        self.preview_detent.configure(bg=self._palette_hex(d))

    def _palette_hex(self, idx: int) -> str:
        r, g, b = self.palette[clamp7(idx)]
        return f"#{r:02x}{g:02x}{b:02x}"

    def _encoder_label(self, idx: int) -> str:
        bank = idx // ENCODERS_PER_BANK + 1
        enc = idx % ENCODERS_PER_BANK + 1
        return f"B{bank}E{enc}"

    def _update_context_labels(self) -> None:
        idx = self._selected_index()
        bank = idx // ENCODERS_PER_BANK + 1
        enc = idx % ENCODERS_PER_BANK + 1
        self.context_var.set(f"Active Bank: {bank}   Active Encoder: {enc}   Active Tag: {idx + 1}   Scope: {self.apply_scope_var.get()}")

        current_bank = bank - 1
        selected_in_bank = sorted(
            (sel % ENCODERS_PER_BANK) + 1
            for sel in self.selected_encoders
            if (sel // ENCODERS_PER_BANK) == current_bank
        )
        if selected_in_bank:
            joined = ", ".join(str(v) for v in selected_in_bank)
            self.selection_var.set(f"Selected in bank {bank}: {len(selected_in_bank)} knob(s) -> {joined}")
        else:
            self.selection_var.set(f"Selected in bank {bank}: none")

    def _current_bank_start(self) -> int:
        return (max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1) * ENCODERS_PER_BANK

    def select_all_in_bank(self) -> None:
        bank_start = self._current_bank_start()
        self.selected_encoders = {bank_start + i for i in range(ENCODERS_PER_BANK)}
        active = self._selected_index()
        if active not in self.selected_encoders:
            active = bank_start
            self._set_active_index(active)
        self.last_selected_encoder = active
        self._update_knob_visuals()
        self._draw_mini_map()
        self._update_context_labels()

    def select_row_from_active(self) -> None:
        idx = self._selected_index()
        bank_start = self._current_bank_start()
        local = idx - bank_start
        row = local // 4
        self.selected_encoders = {bank_start + row * 4 + col for col in range(4)}
        self.last_selected_encoder = idx
        self._update_knob_visuals()
        self._draw_mini_map()
        self._update_context_labels()

    def select_column_from_active(self) -> None:
        idx = self._selected_index()
        bank_start = self._current_bank_start()
        local = idx - bank_start
        col = local % 4
        self.selected_encoders = {bank_start + row * 4 + col for row in range(4)}
        self.last_selected_encoder = idx
        self._update_knob_visuals()
        self._draw_mini_map()
        self._update_context_labels()

    def copy_active_encoder(self) -> None:
        idx = self._selected_index()
        source = self.profile.encoders[idx]
        self.copied_encoder = EncoderConfig(**asdict(source))
        messagebox.showinfo("Copied", f"Copied settings from encoder #{idx + 1}.")

    def paste_to_selected(self) -> None:
        if self.copied_encoder is None:
            messagebox.showwarning("Nothing Copied", "Use Copy Active first.")
            return

        self._push_history()
        fields = self._scoped_fields()
        targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
        for idx in targets:
            dst = self.profile.encoders[idx]
            for field_name in fields:
                setattr(dst, field_name, getattr(self.copied_encoder, field_name))

        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def copy_to_slot(self) -> None:
        slot = max(1, min(4, int(self.clipboard_slot_var.get())))
        idx = self._selected_index()
        self.clipboard_slots[slot] = EncoderConfig(**asdict(self.profile.encoders[idx]))
        messagebox.showinfo("Copied", f"Copied encoder #{idx + 1} to slot {slot}.")

    def paste_from_slot(self) -> None:
        slot = max(1, min(4, int(self.clipboard_slot_var.get())))
        src = self.clipboard_slots.get(slot)
        if src is None:
            messagebox.showwarning("Empty Slot", f"Clipboard slot {slot} is empty.")
            return
        self._push_history()
        fields = self._scoped_fields()
        for idx in sorted(self.selected_encoders):
            dst = self.profile.encoders[idx]
            for field_name in fields:
                setattr(dst, field_name, getattr(src, field_name))
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def _load_named_presets(self) -> dict:
        if not self.preset_file.exists():
            return {}
        try:
            data = json.loads(self.preset_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _save_named_presets(self) -> None:
        self.preset_file.write_text(json.dumps(self.named_presets, indent=2), encoding="utf-8")
        self.preset_combo["values"] = sorted(self.named_presets.keys())

    def save_named_preset(self) -> None:
        name = self.preset_name_var.get().strip()
        if not name:
            messagebox.showwarning("Preset Name", "Enter a preset name first.")
            return
        self.named_presets[name] = asdict(self.profile.encoders[self._selected_index()])
        self._save_named_presets()
        self.preset_select_var.set(name)

    def apply_named_preset(self) -> None:
        name = self.preset_select_var.get().strip()
        if not name or name not in self.named_presets:
            messagebox.showwarning("Preset", "Select a valid preset.")
            return
        self._push_history()
        src = EncoderConfig(**self.named_presets[name])
        fields = self._scoped_fields()
        for idx in sorted(self.selected_encoders):
            dst = self.profile.encoders[idx]
            for field_name in fields:
                setattr(dst, field_name, getattr(src, field_name))
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def delete_named_preset(self) -> None:
        name = self.preset_select_var.get().strip()
        if name in self.named_presets:
            del self.named_presets[name]
            self._save_named_presets()
            self.preset_select_var.set("")

    def export_named_presets(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        data = {
            "mode": "named-presets",
            "version": 1,
            "presets": self.named_presets,
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        messagebox.showinfo("Exported", f"Presets exported to:\n{path}")

    def import_named_presets(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("mode") != "named-presets":
                raise ValueError("File is not a named-presets export")
            presets = raw.get("presets", {})
            if not isinstance(presets, dict):
                raise ValueError("Invalid presets payload")

            merged = 0
            for name, payload in presets.items():
                if not isinstance(name, str) or not isinstance(payload, dict):
                    continue
                clean: dict[str, int] = {}
                for field_name in ENCODER_TAGS:
                    if field_name in payload:
                        clean[field_name] = clamp7(payload[field_name])
                if clean:
                    self.named_presets[name] = clean
                    merged += 1

            self._save_named_presets()
            messagebox.showinfo("Imported", f"Imported {merged} preset(s).")
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))

    def _draw_knob_grid(self) -> None:
        if self.knob_canvas is None:
            return

        c = self.knob_canvas
        c.delete("all")
        self.knob_items.clear()
        self.knob_centers.clear()

        width = int(c.winfo_width() or 650)
        height = int(c.winfo_height() or 620)
        margin_x = 85
        margin_y = 78
        step_x = (width - margin_x * 2) / 3.0
        step_y = (height - margin_y * 2) / 3.0
        radius = min(step_x, step_y) * 0.38

        bank_start = self._current_bank_start()
        for row in range(4):
            for col in range(4):
                local_encoder = row * 4 + col
                idx = bank_start + local_encoder

                cx = margin_x + col * step_x
                cy = margin_y + row * step_y
                self.knob_centers[idx] = (cx, cy, radius)

                outer = c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="#6a7386", width=2, fill="#1b1f2a")
                inner = c.create_oval(cx - radius * 0.58, cy - radius * 0.58, cx + radius * 0.58, cy + radius * 0.58, outline="#0d1017", width=1, fill="#161922")
                led = c.create_arc(
                    cx - radius * 0.82,
                    cy - radius * 0.82,
                    cx + radius * 0.82,
                    cy + radius * 0.82,
                    start=212,
                    extent=116,
                    style="arc",
                    width=11,
                    outline="#3c4457",
                )
                txt = c.create_text(cx, cy + radius + 18, text=f"{local_encoder + 1}", fill="#c5cedd", font=("TkDefaultFont", 10, "bold"))
                tag = c.create_text(cx, cy - radius - 14, text=f"#{idx + 1}", fill="#8f99ad", font=("TkDefaultFont", 9))

                self.knob_items[idx] = {
                    "outer": outer,
                    "inner": inner,
                    "led": led,
                    "label": txt,
                    "tag": tag,
                }

        self.drag_rect_id = c.create_rectangle(0, 0, 0, 0, outline="#91b3ff", width=1, dash=(3, 2), state="hidden")
        self._update_knob_visuals()

    def _update_knob_visuals(self) -> None:
        if self.knob_canvas is None:
            return
        c = self.knob_canvas
        active = self._selected_index()

        for idx, ids in self.knob_items.items():
            cfg = self.profile.encoders[idx]
            led_color = self._palette_hex(cfg.active_color)
            selected = idx in self.selected_encoders

            if idx == active:
                pulse = self.selection_pulse_phase
                outline = "#ffffff" if pulse >= 4 else "#dce8ff"
                width = 3 + (pulse // 3)
            elif selected:
                outline = "#8eb2ff"
                width = 3
            else:
                outline = "#5e6677"
                width = 2

            c.itemconfigure(ids["outer"], outline=outline, width=width)
            c.itemconfigure(ids["led"], outline=led_color)
            c.itemconfigure(ids["inner"], fill="#141820" if not selected else "#1d2330")
            c.itemconfigure(ids["label"], fill="#ebf1ff" if selected else "#c5cedd")

    def _draw_mini_map(self) -> None:
        if self.mini_map is None:
            return

        c = self.mini_map
        c.delete("all")

        cell_w = 16
        cell_h = 28
        pad_x = 8
        pad_y = 8
        active = self._selected_index()

        for bank in range(NUM_BANKS):
            y = pad_y + bank * (cell_h + 8)
            c.create_text(4, y + cell_h / 2, text=str(bank + 1), fill="#aeb7c8", anchor="w")
            for enc in range(ENCODERS_PER_BANK):
                idx = bank * ENCODERS_PER_BANK + enc
                x = pad_x + 20 + enc * cell_w
                fill = self._palette_hex(self.profile.encoders[idx].active_color)
                outline = "#f4f7ff" if idx == active else ("#8eb2ff" if idx in self.selected_encoders else "#303748")
                width = 2 if idx == active else 1
                c.create_rectangle(x, y, x + cell_w - 2, y + cell_h, fill=fill, outline=outline, width=width)

    def _on_mini_map_click(self, event) -> None:
        cell_w = 16
        cell_h = 28
        pad_x = 8 + 20
        pad_y = 8

        x = event.x - pad_x
        if x < 0:
            return
        col = x // cell_w
        if col < 0 or col >= 16:
            return

        for bank in range(NUM_BANKS):
            y = pad_y + bank * (cell_h + 8)
            if y <= event.y <= y + cell_h:
                idx = bank * ENCODERS_PER_BANK + int(col)
                self.selected_encoders = {idx}
                self.last_selected_encoder = idx
                self._set_active_index(idx)
                self._load_encoder_fields_from_model()
                self._draw_knob_grid()
                self._draw_mini_map()
                return

    def _index_at_point(self, x: float, y: float) -> int | None:
        for idx, (cx, cy, radius) in self.knob_centers.items():
            dx = x - cx
            dy = y - cy
            if (dx * dx + dy * dy) <= (radius * radius):
                return idx
        return None

    def _is_shift(self, state: int) -> bool:
        return bool(state & MOD_SHIFT)

    def _is_toggle(self, state: int) -> bool:
        return bool(state & MOD_TOGGLE)

    def _on_knob_press(self, event) -> None:
        if self.knob_canvas is None:
            return
        self.drag_start = (event.x, event.y)
        self.dragging = False
        self.drag_clicked_index = self._index_at_point(event.x, event.y)
        self.drag_base_selection = set(self.selected_encoders)

        if self._is_shift(event.state):
            self.drag_mode = "add"
        elif self._is_toggle(event.state):
            self.drag_mode = "toggle"
        else:
            self.drag_mode = "replace"

    def _on_knob_drag(self, event) -> None:
        if self.knob_canvas is None or self.drag_start is None:
            return

        x0, y0 = self.drag_start
        if abs(event.x - x0) + abs(event.y - y0) < 8:
            return

        self.dragging = True
        self.knob_canvas.itemconfigure(self.drag_rect_id, state="normal")
        self.knob_canvas.coords(self.drag_rect_id, x0, y0, event.x, event.y)

        rect_x0 = min(x0, event.x)
        rect_y0 = min(y0, event.y)
        rect_x1 = max(x0, event.x)
        rect_y1 = max(y0, event.y)

        hit: set[int] = set()
        for idx, (cx, cy, _radius) in self.knob_centers.items():
            if rect_x0 <= cx <= rect_x1 and rect_y0 <= cy <= rect_y1:
                hit.add(idx)

        if self.drag_mode == "add":
            new_selection = set(self.drag_base_selection) | hit
        elif self.drag_mode == "toggle":
            new_selection = set(self.drag_base_selection)
            for idx in hit:
                if idx in new_selection:
                    new_selection.remove(idx)
                else:
                    new_selection.add(idx)
        else:
            new_selection = hit

        if new_selection:
            self.selected_encoders = new_selection
            active = sorted(new_selection)[0]
            self._set_active_index(active)
            self.last_selected_encoder = active
            self._load_encoder_fields_from_model()
            self._update_knob_visuals()
            self._draw_mini_map()

    def _on_knob_release(self, event) -> None:
        if self.knob_canvas is None:
            return

        if self.dragging:
            self.knob_canvas.itemconfigure(self.drag_rect_id, state="hidden")
            self.drag_start = None
            self.dragging = False
            self._update_context_labels()
            return

        idx = self.drag_clicked_index
        self.drag_start = None
        self.drag_clicked_index = None
        if idx is None:
            return

        shift = self._is_shift(event.state)
        toggle = self._is_toggle(event.state)

        if shift and self.last_selected_encoder // ENCODERS_PER_BANK == idx // ENCODERS_PER_BANK:
            start = min(self.last_selected_encoder, idx)
            end = max(self.last_selected_encoder, idx)
            selection = set(self.selected_encoders)
            for i in range(start, end + 1):
                selection.add(i)
            self.selected_encoders = selection
        elif toggle:
            selection = set(self.selected_encoders)
            if idx in selection and len(selection) > 1:
                selection.remove(idx)
            else:
                selection.add(idx)
            self.selected_encoders = selection
        else:
            self.selected_encoders = {idx}

        self._set_active_index(idx)
        self.last_selected_encoder = idx
        self._load_encoder_fields_from_model()
        self._update_knob_visuals()
        self._draw_mini_map()

    def pick_rgb_for(self, field_name: str) -> None:
        current_idx = clamp7(self.fields[field_name].get())
        selected, _ = colorchooser.askcolor(color=self._palette_hex(current_idx), title=f"Pick RGB for {field_name}")
        if not selected:
            return
        sr, sg, sb = map(int, selected)
        idx = nearest_palette_index((sr, sg, sb), self.palette)
        self.fields[field_name].set(idx)
        self._refresh_color_previews()

        mapped = self.palette[idx]
        messagebox.showinfo(
            "RGB Mapped",
            (
                f"Requested RGB: ({sr}, {sg}, {sb})\n"
                f"Mapped palette index: {idx}\n"
                f"Mapped RGB: ({mapped[0]}, {mapped[1]}, {mapped[2]})"
            ),
        )

    def gradient_fill_selected(self) -> None:
        targets = sorted(self.selected_encoders)
        if not targets:
            return
        c1, _ = colorchooser.askcolor(title="Gradient start")
        if not c1:
            return
        c2, _ = colorchooser.askcolor(title="Gradient end")
        if not c2:
            return
        self._push_history()
        sr, sg, sb = map(int, c1)
        er, eg, eb = map(int, c2)
        n = len(targets)
        for i, idx in enumerate(targets):
            t = 0 if n <= 1 else i / (n - 1)
            r = int(sr + (er - sr) * t)
            g = int(sg + (eg - sg) * t)
            b = int(sb + (eb - sb) * t)
            self.profile.encoders[idx].active_color = nearest_palette_index((r, g, b), self.palette)
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def randomize_selected_colors(self) -> None:
        targets = sorted(self.selected_encoders)
        if not targets:
            return
        low = simpledialog.askinteger("Randomize", "Min palette index", minvalue=0, maxvalue=127, initialvalue=1)
        if low is None:
            return
        high = simpledialog.askinteger("Randomize", "Max palette index", minvalue=0, maxvalue=127, initialvalue=126)
        if high is None:
            return
        if low > high:
            low, high = high, low
        self._push_history()
        for idx in targets:
            enc = self.profile.encoders[idx]
            enc.active_color = random.randint(low, high)
            enc.inactive_color = random.randint(low, high)
            enc.detent_color = random.randint(low, high)
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def rotate_selected_hue(self) -> None:
        targets = sorted(self.selected_encoders)
        if not targets:
            return
        step = simpledialog.askinteger("Rotate", "Index step (-127..127)", minvalue=-127, maxvalue=127, initialvalue=4)
        if step is None:
            return
        self._push_history()
        for idx in targets:
            enc = self.profile.encoders[idx]
            enc.active_color = (enc.active_color + step) % 128
            enc.inactive_color = (enc.inactive_color + step) % 128
            enc.detent_color = (enc.detent_color + step) % 128
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def pull_global(self) -> None:
        try:
            self.client.pull_global_config()
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def _target_indices_for_bank(self, bank: int) -> list[int]:
        start = bank * ENCODERS_PER_BANK
        return [start + i for i in range(ENCODERS_PER_BANK)]

    def _validate_profile_for_send(self, targets: list[int], include_globals: bool = True) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []

        if include_globals:
            midi_channel = clamp7(self.profile.globals.get("midi_channel", 0))
            if midi_channel > 15:
                errors.append(f"Global midi_channel is {midi_channel}; expected 0..15 (MIDI channels 1..16).")

            super_start = clamp7(self.profile.globals.get("super_start", 0))
            super_end = clamp7(self.profile.globals.get("super_end", 0))
            if super_start > super_end:
                errors.append(f"Global super range is invalid: super_start ({super_start}) > super_end ({super_end}).")

        for idx in targets:
            cfg = self.profile.encoders[idx]
            bank = idx // ENCODERS_PER_BANK + 1
            enc = idx % ENCODERS_PER_BANK + 1
            prefix = f"B{bank}E{enc}"

            if cfg.switch_midi_channel > 15:
                errors.append(f"{prefix}: switch_midi_channel {cfg.switch_midi_channel} out of range 0..15.")
            if cfg.encoder_midi_channel > 15:
                errors.append(f"{prefix}: encoder_midi_channel {cfg.encoder_midi_channel} out of range 0..15.")
            if cfg.encoder_shift_midi_channel > 15:
                errors.append(f"{prefix}: encoder_shift_midi_channel {cfg.encoder_shift_midi_channel} out of range 0..15.")

            if cfg.indicator_display_type > 3:
                errors.append(f"{prefix}: indicator_display_type {cfg.indicator_display_type} out of range 0..3.")
            if cfg.movement > 2:
                errors.append(f"{prefix}: movement {cfg.movement} out of range 0..2.")
            if cfg.encoder_midi_type > 5:
                errors.append(f"{prefix}: encoder_midi_type {cfg.encoder_midi_type} out of expected range 0..5.")

            if cfg.has_detent == 0 and cfg.detent_color != 0:
                warnings.append(f"{prefix}: detent_color is set ({cfg.detent_color}) while has_detent is 0.")

        return errors, warnings

    def _run_send_validation(self, targets: list[int], label: str, include_globals: bool = True) -> bool:
        errors, warnings = self._validate_profile_for_send(targets, include_globals=include_globals)
        if errors:
            messagebox.showerror("Validation Failed", f"{label}\n\n" + "\n".join(errors[:20]))
            return False
        if warnings:
            return messagebox.askyesno(
                "Validation Warning",
                f"{label}\n\n" + "\n".join(warnings[:20]) + "\n\nContinue anyway?",
            )
        return True

    def _capture_device_encoder_snapshot(self, targets: list[int]) -> dict[int, dict[str, int]]:
        snap: dict[int, dict[str, int]] = {}
        for idx in targets:
            cfg = self.device_profile.encoders[idx]
            snap[idx] = {field_name: clamp7(getattr(cfg, field_name)) for field_name in ENCODER_TAGS}
        return snap

    def _restore_profile_from_dict(self, data: dict) -> None:
        self.profile = Profile.from_json_dict(data)
        for key in GLOBAL_TAGS:
            self.global_fields[key].set(self.profile.globals.get(key, 0))
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def _preflight_drift_warning(self, targets: list[int], label: str) -> bool:
        if not self.client.connected or self.dry_run_var.get():
            return True

        baseline = self._capture_device_encoder_snapshot(targets)
        local_profile_before = self.profile.to_json_dict()

        try:
            self.client.pull_global_config()
            time.sleep(0.01)
            for idx in targets:
                self.client.pull_encoder(idx + 1)
                time.sleep(0.006)

            deadline = time.time() + 0.75
            while time.time() < deadline:
                self._drain_event_queue()
                time.sleep(0.01)

            changed_targets = 0
            changed_fields = 0
            for idx in targets:
                before = baseline[idx]
                now_cfg = self.device_profile.encoders[idx]
                this_changed = False
                for field_name in ENCODER_TAGS:
                    now_val = clamp7(getattr(now_cfg, field_name))
                    if now_val != before[field_name]:
                        changed_fields += 1
                        this_changed = True
                if this_changed:
                    changed_targets += 1
        except Exception as exc:
            messagebox.showwarning("Drift Check", f"{label}: could not run drift check ({exc}).")
            changed_targets = 0
            changed_fields = 0
        finally:
            self._restore_profile_from_dict(local_profile_before)

        if changed_targets == 0:
            return True

        return messagebox.askyesno(
            "Drift Detected",
            (
                f"{label}: device changed since last known baseline.\n\n"
                f"Changed encoders: {changed_targets}\n"
                f"Changed fields: {changed_fields}\n\n"
                "Continue and overwrite device with current editor values?"
            ),
        )

    def _sync_globals_from_ui(self) -> None:
        for key in GLOBAL_TAGS:
            self.profile.globals[key] = clamp7(self.global_fields[key].get())

    def _snapshot_payload(self, label: str, targets: list[int]) -> dict:
        return {
            "mode": "auto-backup-snapshot",
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "target_encoders": [int(i) for i in targets],
            "profile": self.profile.to_json_dict(),
            "named_presets": self.named_presets,
        }

    def _write_auto_backup_snapshot(self, label: str, targets: list[int]) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in label.lower()).strip("_")
        name = f"snapshot_{stamp}_{safe_label or 'send'}.json"
        path = self.backup_dir / name
        payload = self._snapshot_payload(label, targets)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def _latest_snapshot_path(self) -> Path | None:
        files = sorted(self.backup_dir.glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None

    def restore_last_snapshot(self) -> None:
        path = self._latest_snapshot_path()
        if path is None:
            messagebox.showwarning("No Snapshot", "No auto-backup snapshot exists yet.")
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            profile_data = raw.get("profile", {})
            self._push_history()
            self.profile = Profile.from_json_dict(profile_data)

            presets_data = raw.get("named_presets", {})
            if isinstance(presets_data, dict):
                clean_presets: dict[str, dict] = {}
                for name, payload in presets_data.items():
                    if isinstance(name, str) and isinstance(payload, dict):
                        clean = {}
                        for field_name in ENCODER_TAGS:
                            if field_name in payload:
                                clean[field_name] = clamp7(payload[field_name])
                        if clean:
                            clean_presets[name] = clean
                self.named_presets = clean_presets
                self._save_named_presets()

            for key in GLOBAL_TAGS:
                self.global_fields[key].set(self.profile.globals.get(key, 0))
            self._load_encoder_fields_from_model()
            self._draw_knob_grid()
            self._draw_mini_map()
            self.status_var.set(f"Restored snapshot: {path.name}")

            if self.client.connected and messagebox.askyesno(
                "Push Restored Profile",
                "Snapshot restored in editor. Push all 64 encoders to device now?",
            ):
                self.push_all_banks()
        except Exception as exc:
            messagebox.showerror("Restore Error", str(exc))

    def _confirm_bulk_send(self, targets: list[int], label: str) -> bool:
        if self.dry_run_var.get():
            self.show_diff_window(targets, f"Dry Run: {label}")
            return False
        threshold = max(1, int(self.confirm_threshold_var.get()))
        if len(targets) >= threshold:
            return messagebox.askyesno("Confirm", f"{label}: send {len(targets)} encoder(s)?")
        return True

    def push_global(self) -> None:
        try:
            self._sync_globals_from_ui()
            if not self._run_send_validation(list(range(TOTAL_ENCODERS)), "Push Global", include_globals=True):
                return
            snapshot_path = self._write_auto_backup_snapshot("push_global", list(range(TOTAL_ENCODERS)))
            self.client.push_global_config(self.profile.globals)
            self.status_var.set(f"Global push complete. Backup: {snapshot_path.name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def pull_bank(self) -> None:
        try:
            bank = max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1
            for idx in self._target_indices_for_bank(bank):
                self.client.pull_encoder(idx + 1)
                time.sleep(0.01)
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_bank(self) -> None:
        try:
            self._apply_encoder_fields_to_model()
            self._sync_globals_from_ui()
            bank = max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1
            targets = self._target_indices_for_bank(bank)
            if not self._run_send_validation(targets, f"Push Bank {bank + 1}", include_globals=True):
                return
            if not self._confirm_bulk_send(targets, f"Push Bank {bank + 1}"):
                return
            if not self._preflight_drift_warning(targets, f"Push Bank {bank + 1}"):
                return
            snapshot_path = self._write_auto_backup_snapshot(f"push_bank_{bank + 1}", targets)
            for idx in targets:
                self.client.push_encoder(idx + 1, self.profile.encoders[idx])
            self.status_var.set(f"Bank {bank + 1} push complete. Backup: {snapshot_path.name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def pull_all_banks(self) -> None:
        try:
            for idx in range(TOTAL_ENCODERS):
                self.client.pull_encoder(idx + 1)
                time.sleep(0.008)
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_all_banks(self) -> None:
        try:
            self._apply_encoder_fields_to_model()
            self._sync_globals_from_ui()
            targets = list(range(TOTAL_ENCODERS))
            if not self._run_send_validation(targets, "Push All Banks", include_globals=True):
                return
            if not self._confirm_bulk_send(targets, "Push All Banks"):
                return
            if not self._preflight_drift_warning(targets, "Push All Banks"):
                return
            snapshot_path = self._write_auto_backup_snapshot("push_all_banks", targets)
            for idx in targets:
                self.client.push_encoder(idx + 1, self.profile.encoders[idx])
            self.status_var.set(f"All-bank push complete. Backup: {snapshot_path.name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_selected_encoder(self) -> None:
        try:
            self._apply_encoder_fields_to_model()
            self._sync_globals_from_ui()
            targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
            if not self._run_send_validation(targets, "Send Selected", include_globals=True):
                return
            if not self._confirm_bulk_send(targets, "Send Selected"):
                return
            if not self._preflight_drift_warning(targets, "Send Selected"):
                return
            snapshot_path = self._write_auto_backup_snapshot("push_selected", targets)
            for idx in targets:
                self.client.push_encoder(idx + 1, self.profile.encoders[idx])
            self.status_var.set(f"Selected send complete. Backup: {snapshot_path.name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def _compute_diff_lines(self, targets: list[int]) -> list[str]:
        lines = []
        field_counts: dict[str, int] = {}
        encoder_blocks: list[str] = []
        for idx in targets:
            cur = self.profile.encoders[idx]
            dev = self.device_profile.encoders[idx]
            changed = []
            for field_name in ENCODER_TAGS:
                a = getattr(cur, field_name)
                b = getattr(dev, field_name)
                if a != b:
                    changed.append(f"{field_name}: {b} -> {a}")
                    field_counts[field_name] = field_counts.get(field_name, 0) + 1
            if changed:
                bank = idx // ENCODERS_PER_BANK + 1
                enc = idx % ENCODERS_PER_BANK + 1
                encoder_blocks.append(f"B{bank}E{enc} (# {idx + 1})")
                encoder_blocks.extend([f"  {c}" for c in changed])

        if field_counts:
            lines.append("Summary (changed fields)")
            for field_name, count in sorted(field_counts.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"  {field_name}: {count} encoder(s)")
            lines.append("")
            lines.append("Details by Encoder")
            lines.extend(encoder_blocks)
        return lines

    def preview_diff_selected(self) -> None:
        targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
        self.show_diff_window(targets, "Preview Diff: Selected")

    def show_diff_window(self, targets: list[int], title: str) -> None:
        lines = self._compute_diff_lines(targets)
        if not lines:
            lines = ["No differences detected against the last pulled device state."]
        win = Toplevel(self)
        win.title(title)
        txt = Text(win, wrap="word", width=90, height=28)
        txt.pack(fill=BOTH, expand=True, padx=8, pady=8)
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")

    def load_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            self._push_history()
            if raw.get("mode") == "everything-bundle":
                profile_data = raw.get("profile", {})
                presets_data = raw.get("named_presets", {})
                self.profile = Profile.from_json_dict(profile_data)
                if isinstance(presets_data, dict):
                    clean_presets: dict[str, dict] = {}
                    for name, payload in presets_data.items():
                        if isinstance(name, str) and isinstance(payload, dict):
                            clean = {}
                            for field_name in ENCODER_TAGS:
                                if field_name in payload:
                                    clean[field_name] = clamp7(payload[field_name])
                            if clean:
                                clean_presets[name] = clean
                    self.named_presets = clean_presets
                    self._save_named_presets()
            elif raw.get("mode") == "bank-snippet":
                bank = max(1, min(4, int(raw.get("bank", int(self.bank_var.get())))))
                start = (bank - 1) * ENCODERS_PER_BANK
                for i, row in enumerate(raw.get("encoders", [])[:ENCODERS_PER_BANK]):
                    dst = self.profile.encoders[start + i]
                    for field_name in ENCODER_TAGS:
                        if field_name in row:
                            setattr(dst, field_name, clamp7(row[field_name]))
                self._set_active_index(start)
            else:
                self.profile = Profile.from_json_dict(raw)
            for key in GLOBAL_TAGS:
                self.global_fields[key].set(self.profile.globals.get(key, 0))
            self._load_encoder_fields_from_model()
            self._draw_knob_grid()
            self._draw_mini_map()
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))

    def export_everything_bundle(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        self._apply_encoder_fields_to_model()
        for key in GLOBAL_TAGS:
            self.profile.globals[key] = clamp7(self.global_fields[key].get())
        data = {
            "mode": "everything-bundle",
            "version": 1,
            "profile": self.profile.to_json_dict(),
            "named_presets": self.named_presets,
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        messagebox.showinfo("Exported", f"Everything bundle exported to:\n{path}")

    def import_everything_bundle(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("mode") != "everything-bundle":
                raise ValueError("File is not an everything-bundle export")
            self._push_history()
            self.profile = Profile.from_json_dict(raw.get("profile", {}))
            presets_data = raw.get("named_presets", {})
            self.named_presets = presets_data if isinstance(presets_data, dict) else {}
            self._save_named_presets()
            for key in GLOBAL_TAGS:
                self.global_fields[key].set(self.profile.globals.get(key, 0))
            self._load_encoder_fields_from_model()
            self._draw_knob_grid()
            self._draw_mini_map()
            messagebox.showinfo("Imported", f"Everything bundle imported from:\n{path}")
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))

    def save_json(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        self._apply_encoder_fields_to_model()
        self._sync_globals_from_ui()
        Path(path).write_text(json.dumps(self.profile.to_json_dict(), indent=2), encoding="utf-8")
        messagebox.showinfo("Saved", f"Profile written to:\n{path}")

    def export_bank_snippet(self) -> None:
        bank = max(1, min(NUM_BANKS, int(self.bank_var.get())))
        start = (bank - 1) * ENCODERS_PER_BANK
        data = {
            "mode": "bank-snippet",
            "bank": bank,
            "encoders": [asdict(self.profile.encoders[start + i]) for i in range(ENCODERS_PER_BANK)],
        }
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        messagebox.showinfo("Saved", f"Bank {bank} snippet written to:\n{path}")

    def import_bank_snippet(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("mode") != "bank-snippet":
                raise ValueError("File is not a bank-snippet JSON")
            self._push_history()
            bank = max(1, min(4, int(raw.get("bank", int(self.bank_var.get())))))
            start = (bank - 1) * ENCODERS_PER_BANK
            for i, row in enumerate(raw.get("encoders", [])[:ENCODERS_PER_BANK]):
                dst = self.profile.encoders[start + i]
                for field_name in ENCODER_TAGS:
                    if field_name in row:
                        setattr(dst, field_name, clamp7(row[field_name]))
            self._set_active_index(start)
            self._load_encoder_fields_from_model()
            self._draw_knob_grid()
            self._draw_mini_map()
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))

    def _poll_events(self) -> None:
        self._drain_event_queue()
        self.after(40, self._poll_events)

    def _drain_event_queue(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "sysex_tx":
            self._append_midi_log("TX", event.get("command"), event.get("payload", []))
            return

        if event_type != "sysex":
            return

        self._append_midi_log("RX", event.get("command"), event.get("payload", []))

        command = event.get("command")
        payload = event.get("payload", [])

        if command == SYSEX_COMMAND_PULL_CONF:
            self._handle_global_sysex(payload)
        elif command == SYSEX_COMMAND_BULK_XFER:
            self._handle_bulk_sysex(payload)

    def _command_name(self, command: int) -> str:
        names = {
            SYSEX_COMMAND_PUSH_CONF: "PUSH_CONF",
            SYSEX_COMMAND_PULL_CONF: "PULL_CONF",
            SYSEX_COMMAND_BULK_XFER: "BULK_XFER",
        }
        return names.get(command, f"CMD_{command}")

    def _append_midi_log(self, direction: str, command: int | None, payload: list[int]) -> None:
        if not self.midi_log_enabled.get():
            return

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        cmd_val = clamp7(command if command is not None else 0)
        head = " ".join(f"{clamp7(v):02X}" for v in payload[:16])
        if len(payload) > 16:
            head += " ..."
        line = f"[{timestamp}] {direction} {self._command_name(cmd_val)} ({cmd_val:02X}) len={len(payload)} data={head}"

        self.midi_log_lines.append(line)
        if len(self.midi_log_lines) > self.midi_log_max_lines:
            self.midi_log_lines = self.midi_log_lines[-self.midi_log_max_lines:]

        if self.midi_monitor_text is not None and self.midi_monitor_window is not None and self.midi_monitor_window.winfo_exists():
            self.midi_monitor_text.configure(state="normal")
            self.midi_monitor_text.insert("end", line + "\n")
            self.midi_monitor_text.see("end")
            self.midi_monitor_text.configure(state="disabled")

    def clear_midi_log(self) -> None:
        self.midi_log_lines = []
        if self.midi_monitor_text is not None and self.midi_monitor_window is not None and self.midi_monitor_window.winfo_exists():
            self.midi_monitor_text.configure(state="normal")
            self.midi_monitor_text.delete("1.0", "end")
            self.midi_monitor_text.configure(state="disabled")

    def open_midi_monitor(self) -> None:
        if self.midi_monitor_window is not None and self.midi_monitor_window.winfo_exists():
            self.midi_monitor_window.lift()
            return

        win = Toplevel(self)
        win.title("MIDI Activity Monitor")
        win.geometry("980x420")
        self.midi_monitor_window = win

        bar = ttk.Frame(win)
        bar.pack(fill=X, padx=8, pady=6)
        ttk.Checkbutton(bar, text="Enable Logging", variable=self.midi_log_enabled).pack(side=LEFT)
        ttk.Button(bar, text="Clear", command=self.clear_midi_log).pack(side=LEFT, padx=8)
        ttk.Label(bar, text="Shows outgoing and incoming Twister SysEx traffic.").pack(side=LEFT, padx=8)

        txt = Text(win, wrap="none", width=140, height=24)
        txt.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))
        self.midi_monitor_text = txt

        txt.configure(state="normal")
        if self.midi_log_lines:
            txt.insert("1.0", "\n".join(self.midi_log_lines) + "\n")
        txt.configure(state="disabled")

        def _on_close() -> None:
            self.midi_monitor_window = None
            self.midi_monitor_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _diagnostics_payload(self) -> dict:
        return {
            "mode": "diagnostics-report",
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "app": {
                "title": "Midi Fighter Twister Profile + RGB GUI",
                "connected": bool(self.client.connected),
                "selected_bank": int(self.bank_var.get()),
                "selected_encoder": int(self.encoder_var.get()),
            },
            "midi": {
                "input_port": self.input_port_var.get(),
                "output_port": self.output_port_var.get(),
                "available_inputs": TwisterMidiClient.list_input_ports(),
                "available_outputs": TwisterMidiClient.list_output_ports(),
            },
            "safety": {
                "dry_run": bool(self.dry_run_var.get()),
                "confirm_threshold": int(self.confirm_threshold_var.get()),
            },
            "globals": self.profile.globals,
            "recent_midi_log": self.midi_log_lines[-200:],
        }

    def export_diagnostics_report(self) -> None:
        default_name = f"twister_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            payload = self._diagnostics_payload()
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            messagebox.showinfo("Diagnostics Exported", f"Diagnostics report written to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Diagnostics Error", str(exc))

    def _handle_global_sysex(self, payload: list[int]) -> None:
        if not payload:
            return

        # Pull config response from firmware starts with 0x1.
        if payload[0] != 0x01:
            return

        idx = 1
        while idx + 1 < len(payload):
            tag = payload[idx]
            value = payload[idx + 1]
            idx += 2

            for name, expected_tag in GLOBAL_TAGS.items():
                if tag == expected_tag:
                    val = clamp7(value)
                    self.profile.globals[name] = val
                    self.device_profile.globals[name] = val
                    self.global_fields[name].set(val)
                    break

    def _handle_bulk_sysex(self, payload: list[int]) -> None:
        # Expected firmware response: [0x0, tag, part, total, size, tag/value bytes...]
        if len(payload) < 5:
            return

        cmd = payload[0]
        sysex_tag = payload[1]
        size = payload[4]

        if cmd != 0x00 or sysex_tag < 1 or sysex_tag > TOTAL_ENCODERS:
            return

        data = payload[5:5 + size]
        idx = sysex_tag - 1
        cfg = self.profile.encoders[idx]
        dev_cfg = self.device_profile.encoders[idx]

        i = 0
        while i + 1 < len(data):
            tag = data[i]
            value = data[i + 1]
            cfg.apply_tag_value(tag, value)
            dev_cfg.apply_tag_value(tag, value)
            i += 2

        if idx == self._selected_index():
            self._load_encoder_fields_from_model()

        current_bank = self._current_bank_start() // ENCODERS_PER_BANK
        if idx // ENCODERS_PER_BANK == current_bank:
            self._update_knob_visuals()
        self._draw_mini_map()


if __name__ == "__main__":
    app = TwisterGui()
    app.mainloop()
