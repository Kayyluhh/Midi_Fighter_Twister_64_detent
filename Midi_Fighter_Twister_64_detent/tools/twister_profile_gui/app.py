import json
import queue
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import BOTH, LEFT, X, Y, Canvas, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import colorchooser
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
        self.palette = parse_color_map7_from_c()

        self.input_port_var = StringVar(value="")
        self.output_port_var = StringVar(value="")
        self.status_var = StringVar(value="Disconnected")
        self.bank_var = IntVar(value=1)
        self.encoder_var = IntVar(value=1)

        self.context_var = StringVar(value="")
        self.selection_var = StringVar(value="")

        self.fields: dict[str, IntVar] = {name: IntVar(value=getattr(self.profile.encoders[0], name)) for name in ENCODER_TAGS}
        self.global_fields: dict[str, IntVar] = {name: IntVar(value=self.profile.globals[name]) for name in GLOBAL_TAGS}

        active = self._selected_index()
        self.selected_encoders: set[int] = {active}
        self.last_selected_encoder = active
        self._suppress_var_selection = False

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

        self._build_ui()
        self.refresh_ports()
        self._load_encoder_fields_from_model()
        self._refresh_color_previews()
        self._draw_knob_grid()
        self._update_context_labels()
        self.after(40, self._poll_events)

        self.bank_var.trace_add("write", self._on_var_changed)
        self.encoder_var.trace_add("write", self._on_var_changed)

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

        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=7, padx=8, pady=6, sticky="w")

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
        ttk.Separator(toolbar, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Button(toolbar, text="Pull Global", command=self.pull_global).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Push Global", command=self.push_global).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Pull Bank", command=self.pull_bank).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Push Bank", command=self.push_bank).pack(side=LEFT, padx=4)

        selector = ttk.Frame(profile_frame)
        selector.pack(fill=X, padx=8)

        ttk.Label(selector, text="Bank").pack(side=LEFT)
        ttk.Spinbox(selector, from_=1, to=4, width=4, textvariable=self.bank_var, command=self._on_selection_changed).pack(side=LEFT, padx=4)
        ttk.Label(selector, text="Encoder").pack(side=LEFT, padx=(14, 0))
        ttk.Spinbox(selector, from_=1, to=16, width=4, textvariable=self.encoder_var, command=self._on_selection_changed).pack(side=LEFT, padx=4)
        ttk.Button(selector, text="Load Active", command=self._load_encoder_fields_from_model).pack(side=LEFT, padx=8)
        ttk.Button(selector, text="Apply To Selected", command=self._apply_encoder_fields_to_model).pack(side=LEFT, padx=4)
        ttk.Button(selector, text="Send Selected", command=self.push_selected_encoder).pack(side=LEFT, padx=4)

        quick = ttk.Frame(profile_frame)
        quick.pack(fill=X, padx=8, pady=(6, 4))
        ttk.Label(quick, text="Quick Select").pack(side=LEFT)
        ttk.Button(quick, text="Row", command=self.select_row_from_active).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="Column", command=self.select_column_from_active).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="All 16", command=self.select_all_in_bank).pack(side=LEFT, padx=4)
        ttk.Separator(quick, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Button(quick, text="Copy Active", command=self.copy_active_encoder).pack(side=LEFT, padx=4)
        ttk.Button(quick, text="Paste To Selected", command=self.paste_to_selected).pack(side=LEFT, padx=4)

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

        ttk.Button(color_box, text="Refresh Swatches", command=self._refresh_color_previews).pack(pady=8)

        global_frame = ttk.LabelFrame(right, text="Global Settings")
        global_frame.pack(fill=BOTH, expand=True)

        gr = 0
        for key in GLOBAL_TAGS:
            ttk.Label(global_frame, text=key).grid(row=gr, column=0, sticky="w", padx=8, pady=4)
            ttk.Spinbox(global_frame, from_=0, to=127, textvariable=self.global_fields[key], width=10).grid(row=gr, column=1, sticky="w", padx=8, pady=4)
            gr += 1

        help_box = ttk.LabelFrame(right, text="Usage")
        help_box.pack(fill=BOTH, expand=False, pady=(8, 0))
        msg = (
            "1) Connect to Twister ports.\n"
            "2) Pull Global and Pull Bank.\n"
            "3) Use the graphical knobs to select one or many.\n"
            "4) Use Row/Column/All or Copy/Paste for fast edits.\n"
            "5) Edit values, then Apply To Selected and Send Selected.\n"
            "6) Save JSON for reusable profiles."
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
        self._suppress_var_selection = False

    def _on_var_changed(self, *_args) -> None:
        if self._suppress_var_selection:
            return
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        idx = self._selected_index()
        if idx not in self.selected_encoders:
            self.selected_encoders = {idx}
        self.last_selected_encoder = idx
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._update_context_labels()

    def _apply_encoder_fields_to_model(self) -> None:
        targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
        for idx in targets:
            enc = self.profile.encoders[idx]
            for key in ENCODER_TAGS:
                setattr(enc, key, clamp7(self.fields[key].get()))
        self._refresh_color_previews()
        self._draw_knob_grid()

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
        self.context_var.set(f"Active Bank: {bank}   Active Encoder: {enc}   Active Tag: {idx + 1}")

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
        self._update_context_labels()

    def select_row_from_active(self) -> None:
        idx = self._selected_index()
        bank_start = self._current_bank_start()
        local = idx - bank_start
        row = local // 4
        self.selected_encoders = {bank_start + row * 4 + col for col in range(4)}
        self.last_selected_encoder = idx
        self._update_knob_visuals()
        self._update_context_labels()

    def select_column_from_active(self) -> None:
        idx = self._selected_index()
        bank_start = self._current_bank_start()
        local = idx - bank_start
        col = local % 4
        self.selected_encoders = {bank_start + row * 4 + col for row in range(4)}
        self.last_selected_encoder = idx
        self._update_knob_visuals()
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

        targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
        for idx in targets:
            self.profile.encoders[idx] = EncoderConfig(**asdict(self.copied_encoder))

        self._load_encoder_fields_from_model()
        self._draw_knob_grid()

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
                outline = "#f4f7ff"
                width = 4
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

    def pull_global(self) -> None:
        try:
            self.client.pull_global_config()
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_global(self) -> None:
        try:
            for key in GLOBAL_TAGS:
                self.profile.globals[key] = clamp7(self.global_fields[key].get())
            self.client.push_global_config(self.profile.globals)
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def pull_bank(self) -> None:
        try:
            bank = max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1
            for enc in range(ENCODERS_PER_BANK):
                sysex_tag = bank * ENCODERS_PER_BANK + enc + 1
                self.client.pull_encoder(sysex_tag)
                time.sleep(0.01)
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_bank(self) -> None:
        try:
            self._apply_encoder_fields_to_model()
            bank = max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1
            for enc in range(ENCODERS_PER_BANK):
                idx = bank * ENCODERS_PER_BANK + enc
                self.client.push_encoder(idx + 1, self.profile.encoders[idx])
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_selected_encoder(self) -> None:
        try:
            self._apply_encoder_fields_to_model()
            targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
            for idx in targets:
                self.client.push_encoder(idx + 1, self.profile.encoders[idx])
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def load_json(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            self.profile = Profile.from_json_dict(raw)
            for key in GLOBAL_TAGS:
                self.global_fields[key].set(self.profile.globals.get(key, 0))
            self._load_encoder_fields_from_model()
            self._draw_knob_grid()
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))

    def save_json(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        self._apply_encoder_fields_to_model()
        for key in GLOBAL_TAGS:
            self.profile.globals[key] = clamp7(self.global_fields[key].get())
        Path(path).write_text(json.dumps(self.profile.to_json_dict(), indent=2), encoding="utf-8")
        messagebox.showinfo("Saved", f"Profile written to:\n{path}")

    def _poll_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self.after(40, self._poll_events)

    def _handle_event(self, event: dict) -> None:
        if event.get("type") != "sysex":
            return

        command = event.get("command")
        payload = event.get("payload", [])

        if command == SYSEX_COMMAND_PULL_CONF:
            self._handle_global_sysex(payload)
        elif command == SYSEX_COMMAND_BULK_XFER:
            self._handle_bulk_sysex(payload)

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
                    self.profile.globals[name] = clamp7(value)
                    self.global_fields[name].set(self.profile.globals[name])
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

        i = 0
        while i + 1 < len(data):
            tag = data[i]
            value = data[i + 1]
            cfg.apply_tag_value(tag, value)
            i += 2

        if idx == self._selected_index():
            self._load_encoder_fields_from_model()

        current_bank = self._current_bank_start() // ENCODERS_PER_BANK
        if idx // ENCODERS_PER_BANK == current_bank:
            self._update_knob_visuals()


if __name__ == "__main__":
    app = TwisterGui()
    app.mainloop()
