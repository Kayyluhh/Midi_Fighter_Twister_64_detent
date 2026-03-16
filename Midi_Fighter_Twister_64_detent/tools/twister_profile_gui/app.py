import json
import hashlib
import io
import queue
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import BOTH, LEFT, X, Y, BooleanVar, Canvas, IntVar, Label, Menu, StringVar, Text, Tk, Toplevel, filedialog, messagebox
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
    "detent_size": 36,
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

THEME_PACKS: dict[str, dict[str, int]] = {
    "Classic Neon": {"active_color": 25, "inactive_color": 0, "detent_color": 63},
    "High Contrast": {"active_color": 127, "inactive_color": 1, "detent_color": 63},
    "Warm Sunset": {"active_color": 9, "inactive_color": 3, "detent_color": 15},
    "Ocean Cool": {"active_color": 90, "inactive_color": 80, "detent_color": 100},
    "Mono Stage": {"active_color": 120, "inactive_color": 40, "detent_color": 64},
    "Colorblind Safe": {"active_color": 101, "inactive_color": 1, "detent_color": 52},
}

FIRMWARE_CAPABILITIES: dict[str, dict[str, object]] = {
    "open-source-default": {
        "label": "Open Source Default",
        "unsupported_globals": [],
        "max_indicator_display_type": 3,
        "max_movement": 2,
        "max_encoder_midi_type": 5,
        "supports_shift_channel": True,
    },
    "legacy-v1": {
        "label": "Legacy V1",
        "unsupported_globals": ["bank_wrap_mode", "shift_page_latch", "detent_size"],
        "max_indicator_display_type": 2,
        "max_movement": 1,
        "max_encoder_midi_type": 2,
        "supports_shift_channel": False,
    },
}

APP_SETTINGS_VERSION = 1
APP_NAME = "Midi Fighter Twistluhh Utility"
APP_VERSION = "2026.03.16"
DEFAULT_PATCH_MANIFEST_URL = (
    "https://raw.githubusercontent.com/Kayyluhh/Midi_Fighter_Twister_64_detent/main/"
    "Midi_Fighter_Twister_64_detent/tools/twister_profile_gui/patch_manifest.json"
)


def clamp7(value: int) -> int:
    return max(0, min(127, int(value)))


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_tags(raw: object) -> list[str]:
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, list):
        values = [str(part).strip() for part in raw]
    else:
        values = []

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def checksum_json(data: object) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def default_profile_metadata() -> "ProfileMetadata":
    now = iso_now()
    return ProfileMetadata(created_at=now, updated_at=now)


@dataclass
class ProfileMetadata:
    name: str = "Twister Profile"
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    firmware: str = "open-source-default"
    created_at: str = ""
    updated_at: str = ""
    template_source: str = ""
    host_bridge: str = ""

    def mark_updated(self) -> None:
        now = iso_now()
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    @staticmethod
    def from_json_dict(data: dict | None) -> "ProfileMetadata":
        source = data if isinstance(data, dict) else {}
        metadata = default_profile_metadata()
        metadata.name = str(source.get("name") or metadata.name).strip() or metadata.name
        metadata.notes = str(source.get("notes") or "")
        metadata.tags = normalize_tags(source.get("tags", []))
        metadata.firmware = str(source.get("firmware") or metadata.firmware).strip() or metadata.firmware
        metadata.created_at = str(source.get("created_at") or metadata.created_at)
        metadata.updated_at = str(source.get("updated_at") or metadata.updated_at)
        metadata.template_source = str(source.get("template_source") or "").strip()
        metadata.host_bridge = str(source.get("host_bridge") or "").strip()
        return metadata


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
    metadata: ProfileMetadata = field(default_factory=default_profile_metadata)
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
        "detent_size": 5,
    })
    encoders: list[EncoderConfig] = field(default_factory=lambda: [EncoderConfig() for _ in range(TOTAL_ENCODERS)])

    def to_json_dict(self) -> dict:
        return {
            "metadata": asdict(self.metadata),
            "globals": self.globals,
            "encoders": [asdict(e) for e in self.encoders],
        }

    @staticmethod
    def from_json_dict(data: dict) -> "Profile":
        profile = Profile()
        profile.metadata = ProfileMetadata.from_json_dict(data.get("metadata"))
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
        try:
            return list(mido.get_input_names())
        except Exception:
            return []

    @staticmethod
    def list_output_ports() -> list[str]:
        try:
            return list(mido.get_output_names())
        except Exception:
            return []

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
        self.title(APP_NAME)
        self.geometry("1350x840")
        self.minsize(800, 600)

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
        self.performance_mode_var = BooleanVar(value=False)
        self.performance_delay_ms_var = IntVar(value=6)
        self.performance_retry_var = IntVar(value=1)
        self.theme_pack_var = StringVar(value="Classic Neon")
        self.auto_color_rule_var = StringVar(value="By MIDI Channel")

        self.context_var = StringVar(value="")
        self.selection_var = StringVar(value="")

        self.fields: dict[str, IntVar] = {name: IntVar(value=getattr(self.profile.encoders[0], name)) for name in ENCODER_TAGS}
        self.favorite_fields_var: dict[str, BooleanVar] = {name: BooleanVar(value=False) for name in ENCODER_TAGS}
        self.lock_fields_var: dict[str, BooleanVar] = {name: BooleanVar(value=False) for name in ENCODER_TAGS}
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
        self.settings_file = Path(__file__).with_name("app_settings.json")
        self.preset_file = Path(__file__).with_name("presets.json")
        self.backup_dir = Path(__file__).with_name("backups")
        self.patch_dir = Path(__file__).with_name("patches")
        self.template_dir = Path(__file__).with_name("templates")
        self.host_preset_dir = Path(__file__).with_name("host_presets")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.patch_dir.mkdir(parents=True, exist_ok=True)
        self.named_presets: dict[str, dict] = self._load_named_presets()
        self.preset_name_var = StringVar(value="")
        self.preset_select_var = StringVar(value="")
        self.template_var = StringVar(value="")
        self.host_bridge_var = StringVar(value="")
        self.metadata_summary_var = StringVar(value="")
        self.patch_manifest_url_var = StringVar(value=DEFAULT_PATCH_MANIFEST_URL)
        self.patch_status_var = StringVar(value="Patcher: no patches applied")
        self.template_library = self._load_template_library()
        self.host_bridge_presets = self._load_host_bridge_presets()
        self.template_combo = None
        self.host_bridge_combo = None
        self.bank_tabs = None
        self.mini_map = None
        self.selection_pulse_phase = 0
        self.selection_pulse_dir = 1
        self.heatmap_scores: dict[int, int] = {}
        self.heatmap_scope_var = StringVar(value="None")
        self.sandbox_status_var = StringVar(value="Sandbox: off")
        self.sandbox_active = False
        self.sandbox_base_profile: dict | None = None
        self.sandbox_base_presets: dict[str, dict] | None = None
        self.wizard_completed = False
        self._loading_app_settings = False
        self.midi_monitor_window: Toplevel | None = None
        self.midi_monitor_text: Text | None = None
        self.midi_log_enabled = BooleanVar(value=True)
        self.midi_log_lines: list[str] = []
        self.midi_log_max_lines = 800

        self._load_app_settings()

        self._build_ui()
        self._init_tooltips()
        self.refresh_ports()
        self._load_encoder_fields_from_model()
        self._refresh_color_previews()
        self._draw_knob_grid()
        self._draw_mini_map()
        self._update_context_labels()
        self._refresh_library_state()
        self._update_metadata_summary()
        self._update_patch_status_summary()
        self._update_sandbox_status()
        self._animate_selection_pulse()
        self.after(40, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close_app)
        self._register_keyboard_shortcuts()
        self.after(550, self._maybe_launch_setup_wizard)

        self.bank_var.trace_add("write", self._on_var_changed)
        self.encoder_var.trace_add("write", self._on_var_changed)
        for var in (
            self.input_port_var,
            self.output_port_var,
            self.apply_scope_var,
            self.dry_run_var,
            self.confirm_threshold_var,
            self.performance_mode_var,
            self.performance_delay_ms_var,
            self.performance_retry_var,
            self.theme_pack_var,
            self.auto_color_rule_var,
            self.clipboard_slot_var,
            self.midi_log_enabled,
        ):
            var.trace_add("write", self._on_preferences_changed)
        for key in ENCODER_TAGS:
            self.favorite_fields_var[key].trace_add("write", self._on_preferences_changed)
            self.lock_fields_var[key].trace_add("write", self._on_preferences_changed)
        self.bind("<Command-z>", lambda _e: self.undo())
        self.bind("<Command-Z>", lambda _e: self.redo())
        self.bind("<Control-z>", lambda _e: self.undo())
        self.bind("<Control-y>", lambda _e: self.redo())

    def _encoder_field_tooltip_text(self, key: str) -> str:
        descriptions = {
            "has_detent": "Detent enable flag. 0 = no center detent, 1 = detent enabled.",
            "movement": "Encoder movement algorithm value (firmware enum, usually 0..2).",
            "switch_action_type": "Push-switch behavior mode (press/release style value).",
            "switch_midi_channel": "MIDI channel for switch events. Firmware uses 0..15 for channels 1..16.",
            "switch_midi_number": "Switch MIDI number (CC/Note/Program index, 0..127).",
            "switch_midi_type": "Switch message type enum used by firmware.",
            "encoder_midi_channel": "MIDI channel for encoder turns. Firmware uses 0..15 for channels 1..16.",
            "encoder_midi_number": "Encoder MIDI number (CC/Note/Program index, 0..127).",
            "encoder_midi_type": "Encoder output mode enum (absolute/relative variants).",
            "active_color": "Twister palette slot (0..127) when encoder is active.",
            "inactive_color": "Twister palette slot (0..127) when encoder is inactive.",
            "detent_color": "Twister palette slot (0..127) used at the detent marker.",
            "indicator_display_type": "LED ring indicator style enum (firmware display mode).",
            "is_super_knob": "Super Knob participation flag. 0 = normal, 1 = super-knob linked.",
            "encoder_shift_midi_channel": "Shift-layer MIDI channel (0..15 for channels 1..16).",
        }
        return descriptions.get(key, f"Edit {key} for the selected encoder(s).")

    def _global_field_tooltip_text(self, key: str) -> str:
        descriptions = {
            "midi_channel": "Global default MIDI channel (0..15 => channels 1..16).",
            "side_is_banked": "Side-button bank mode flag (side controls follow bank changes).",
            "side_func_1": "Firmware function enum for side button 1.",
            "side_func_2": "Firmware function enum for side button 2.",
            "side_func_3": "Firmware function enum for side button 3.",
            "side_func_4": "Firmware function enum for side button 4.",
            "side_func_5": "Firmware function enum for side button 5.",
            "side_func_6": "Firmware function enum for side button 6.",
            "super_start": "Super Knob minimum value (0..127).",
            "super_end": "Super Knob maximum value (0..127).",
            "rgb_brightness": "Global RGB LED brightness (0..127).",
            "ind_brightness": "Global ring/indicator brightness (0..127).",
            "soft_takeover": "Soft takeover enable flag to reduce value jumps.",
            "bank_wrap_mode": "When enabled, bank navigation wraps from end to start.",
            "shift_page_latch": "Latch shift page (toggle) instead of hold-to-shift behavior.",
            "detent_size": "Center detent width in firmware steps (recommended 1..31).",
        }
        return descriptions.get(key, f"Edit global parameter {key}.")

    def _button_tooltip_texts(self) -> dict[str, str]:
        return {
            "Refresh Ports": "Rescan available MIDI input/output ports from the OS.",
            "Connect": "Open selected MIDI input + output ports and start listening.",
            "Disconnect": "Close current MIDI connection.",
            "MIDI Monitor": "Open live TX/RX SysEx monitor window.",
            "Setup Wizard": "Guided first-run connection and pull flow.",
            "Load JSON": "Load a full profile JSON into the editor.",
            "Save JSON": "Save current profile as full JSON.",
            "Profile Metadata": "Edit profile notes, tags, and firmware target.",
            "Compare Profile": "Compare current profile against another file.",
            "Merge Profile": "Merge another profile into current edits.",
            "Restore Last Snapshot": "Restore latest auto-backup snapshot.",
            "Export Diagnostics": "Export troubleshooting report with app state.",
            "GitHub Patcher": "Check GitHub patch manifest and apply verified patch bundles.",
            "Import Show Pack": "Import profile + presets show pack bundle.",
            "Export Show Pack": "Export profile + presets show pack bundle.",
            "Import Bank Snippet": "Import one-bank encoder snippet JSON.",
            "Export Bank Snippet": "Export current bank snippet JSON.",
            "Undo": "Undo last edit action.",
            "Redo": "Redo previously undone action.",
            "Pull Global": "Pull global settings from hardware (SysEx pull config).",
            "Push Global": "Push global settings to hardware (SysEx push config).",
            "Pull Bank": "Pull current bank (16 encoders) from hardware.",
            "Push Bank": "Push current bank (16 encoders) to hardware.",
            "Pull All Banks": "Pull all 64 encoders from hardware.",
            "Push All Banks": "Push all 64 encoders to hardware.",
            "Load Active": "Load active encoder values into editor fields.",
            "Apply To Selected": "Apply current editor values to selected encoders.",
            "Apply Favorites": "Apply only favorited, unlocked fields to selection.",
            "Preview Diff": "Show differences versus last pulled device state.",
            "Heatmap Selected": "Visualize amount of change on selected encoders.",
            "Heatmap All": "Visualize amount of change across all encoders.",
            "Clear Heatmap": "Clear current heatmap overlay.",
            "Send Selected": "Push only selected encoders to hardware. Shortcut: Ctrl+Enter.",
            "Compat Check": "Check profile values against firmware capabilities.",
            "Recovery Mode": "Reconnect, repull, and run recovery validation.",
            "Start Sandbox": "Start temporary edits that cannot reach hardware.",
            "Commit Sandbox": "Keep sandbox edits and allow hardware writes.",
            "Discard Sandbox": "Discard sandbox changes and restore base state.",
            "Row": "Select active row in the 4x4 grid.",
            "Column": "Select active column in the 4x4 grid.",
            "All 16": "Select all encoders in current bank.",
            "Copy Active": "Copy active encoder settings into temporary clipboard.",
            "Paste To Selected": "Paste copied encoder settings to all selected encoders.",
            "Copy To Slot": "Save active encoder into numbered clipboard slot (1..4).",
            "Paste Slot To Selected": "Paste selected slot values to all selected encoders.",
            "+ MIDI Ch": "Increment MIDI channels on selected encoders.",
            "Remap CC Span": "Remap selected encoder CC numbers to a new range.",
            "Invert CC (127-x)": "Invert selected encoder CC numbers.",
            "Convert Rel/Abs": "Convert selected encoders between relative/absolute modes.",
            "Save Active Preset": "Save active encoder as a named preset.",
            "Apply Preset To Selected": "Apply selected preset to selected encoders.",
            "Delete Preset": "Delete selected named preset.",
            "Import Presets": "Import named presets from JSON file.",
            "Export Presets": "Export named presets to JSON file.",
            "Apply Template": "Apply selected template to current bank/profile.",
            "Import Template File": "Import and apply a template JSON file.",
            "Refresh Templates": "Rescan built-in and imported template files.",
            "Export Host Bridge": "Export host mapping and setup notes JSON.",
            "Pick Active RGB": "Choose active color and map to nearest palette index.",
            "Pick Inactive RGB": "Choose inactive color and map to nearest palette index.",
            "Pick Detent RGB": "Choose detent color and map to nearest palette index.",
            "Refresh Swatches": "Refresh color swatch previews from current values.",
            "Gradient Fill Selected": "Apply gradient colors across selection.",
            "Randomize Colors": "Randomize selected encoder colors.",
            "Rotate Hue Index": "Rotate selected colors through palette indices.",
            "Apply Theme To Selected": "Apply chosen theme pack to selection.",
            "Apply Theme To All 64": "Apply chosen theme pack to all encoders.",
            "Rule To Selected": "Apply auto-color rule to selection.",
            "Rule To All 64": "Apply auto-color rule to all encoders.",
            "Check Manifest": "Fetch patch metadata JSON from GitHub.",
            "Apply Patch": "Download patch archive, verify checksum, and apply with backups.",
            "Close": "Close this window.",
            "Clear": "Clear MIDI monitor log output history.",
        }

    def _init_tooltips(self) -> None:
        self._tooltip_after_id = None
        self._tooltip_window = None
        self._tooltip_current_widget = None
        self._tooltip_text_by_widget: dict[object, str] = {}
        self._button_tooltip_map = self._button_tooltip_texts()
        self._label_tooltip_map: dict[str, str] = {}
        self._var_tooltip_map: dict[str, str] = {}

        for key, var in self.fields.items():
            self._label_tooltip_map[key] = self._encoder_field_tooltip_text(key)
            self._var_tooltip_map[var._name] = self._encoder_field_tooltip_text(key)
            self._var_tooltip_map[self.favorite_fields_var[key]._name] = f"Favorite field toggle for {key}."
            self._var_tooltip_map[self.lock_fields_var[key]._name] = f"Lock {key} to prevent edit operations from changing it."

        for key, var in self.global_fields.items():
            self._label_tooltip_map[key] = self._global_field_tooltip_text(key)
            self._var_tooltip_map[var._name] = self._global_field_tooltip_text(key)

        self._var_tooltip_map[self.input_port_var._name] = "Selected MIDI input port from the Twister."
        self._var_tooltip_map[self.output_port_var._name] = "Selected MIDI output port to the Twister."
        self._var_tooltip_map[self.bank_var._name] = "Current bank number (1..4)."
        self._var_tooltip_map[self.encoder_var._name] = "Current encoder number in bank (1..16)."
        self._var_tooltip_map[self.apply_scope_var._name] = "Choose which field groups apply during multi-edit actions."
        self._var_tooltip_map[self.dry_run_var._name] = "Preview changes without sending MIDI writes."
        self._var_tooltip_map[self.confirm_threshold_var._name] = "Prompt confirmation when sending this many encoders or more."
        self._var_tooltip_map[self.performance_mode_var._name] = "Enable paced transfer mode for more reliable bulk operations."
        self._var_tooltip_map[self.performance_delay_ms_var._name] = "Delay between transfer operations in milliseconds."
        self._var_tooltip_map[self.performance_retry_var._name] = "Retry count for transient transfer errors."
        self._var_tooltip_map[self.clipboard_slot_var._name] = "Clipboard slot number (1..4)."
        self._var_tooltip_map[self.theme_pack_var._name] = "Selected color theme pack for apply actions."
        self._var_tooltip_map[self.auto_color_rule_var._name] = "Rule used for automatic color assignment."
        self._var_tooltip_map[self.patch_manifest_url_var._name] = "GitHub raw URL for patch manifest JSON."
        self._var_tooltip_map[self.preset_name_var._name] = "Name used when saving a new preset."
        self._var_tooltip_map[self.preset_select_var._name] = "Select existing preset to apply or delete."
        self._var_tooltip_map[self.template_var._name] = "Select template to stamp bank/profile values."
        self._var_tooltip_map[self.host_bridge_var._name] = "Select host preset profile for export."
        self._var_tooltip_map[self.midi_log_enabled._name] = "Enable or pause live MIDI monitor logging."

        self._attach_tooltips_recursive(self)

    def _attach_tooltips_recursive(self, widget) -> None:
        text = self._tooltip_text_for_widget(widget)
        if text:
            self._tooltip_text_by_widget[widget] = text
            widget.bind("<Enter>", self._on_tooltip_enter, add="+")
            widget.bind("<Leave>", self._on_tooltip_leave, add="+")
            widget.bind("<Motion>", self._on_tooltip_motion, add="+")
        for child in widget.winfo_children():
            self._attach_tooltips_recursive(child)

    def _tooltip_text_for_widget(self, widget) -> str | None:
        if isinstance(widget, ttk.Button):
            text = widget.cget("text")
            return self._button_tooltip_map.get(text, f"Action: {text}")

        if isinstance(widget, ttk.Checkbutton):
            text = widget.cget("text")
            if text:
                return self._button_tooltip_map.get(text, text)
            var_name = str(widget.cget("variable") or "")
            return self._var_tooltip_map.get(var_name)

        if isinstance(widget, ttk.Label):
            text = widget.cget("text")
            return self._label_tooltip_map.get(text)

        if isinstance(widget, (ttk.Spinbox, ttk.Combobox, ttk.Entry)):
            var_name = str(widget.cget("textvariable") or "")
            return self._var_tooltip_map.get(var_name)

        return None

    def _on_tooltip_enter(self, event) -> None:
        widget = event.widget
        text = self._tooltip_text_by_widget.get(widget)
        if not text:
            return
        self._tooltip_current_widget = widget
        self._cancel_tooltip_timer()
        self._tooltip_after_id = self.after(380, lambda: self._show_tooltip(widget, text))

    def _on_tooltip_leave(self, _event) -> None:
        self._cancel_tooltip_timer()
        self._hide_tooltip()
        self._tooltip_current_widget = None

    def _on_tooltip_motion(self, event) -> None:
        if self._tooltip_window is None:
            return
        self._tooltip_window.geometry(f"+{event.x_root + 14}+{event.y_root + 14}")

    def _cancel_tooltip_timer(self) -> None:
        if self._tooltip_after_id is not None:
            self.after_cancel(self._tooltip_after_id)
            self._tooltip_after_id = None

    def _show_tooltip(self, widget, text: str) -> None:
        if self._tooltip_current_widget is not widget:
            return
        self._hide_tooltip()
        tip = Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        Label(
            tip,
            text=text,
            justify="left",
            padx=8,
            pady=4,
            bg="#fffbe6",
            fg="#1f2937",
            relief="solid",
            borderwidth=1,
        ).pack(fill=BOTH, expand=True)
        x = self.winfo_pointerx() + 14
        y = self.winfo_pointery() + 14
        tip.geometry(f"+{x}+{y}")
        self._tooltip_window = tip

    def _hide_tooltip(self) -> None:
        if self._tooltip_window is not None:
            self._tooltip_window.destroy()
            self._tooltip_window = None

    def _register_keyboard_shortcuts(self) -> None:
        # Bind on all widgets so keyboard-first editing still works when controls have focus.
        shortcuts = [
            ("<Control-Return>", lambda _e: self._shortcut_action(self.push_selected_encoder)),
            ("<Control-Shift-Return>", lambda _e: self._shortcut_action(self.push_bank)),
            ("<Control-Alt-Return>", lambda _e: self._shortcut_action(self.push_all_banks)),
            ("<Control-g>", lambda _e: self._shortcut_action(self.push_global)),
            ("<Control-l>", lambda _e: self._shortcut_action(self.pull_bank)),
            ("<Control-Shift-L>", lambda _e: self._shortcut_action(self.pull_all_banks)),
            ("<Control-Shift-G>", lambda _e: self._shortcut_action(self.pull_global)),
            ("<Control-p>", lambda _e: self._shortcut_action(self.preview_diff_selected)),
            ("<Control-h>", lambda _e: self._shortcut_action(self.preview_heatmap_selected)),
            ("<Control-Shift-H>", lambda _e: self._shortcut_action(self.preview_heatmap_all)),
            ("<Control-Alt-h>", lambda _e: self._shortcut_action(self.clear_heatmap)),
            ("<Control-r>", lambda _e: self._shortcut_action(self.select_row_from_active)),
            ("<Control-a>", lambda _e: self._shortcut_action(self.select_all_in_bank)),
            ("<Control-Shift-F>", lambda _e: self._shortcut_action(self.apply_favorites_to_selected)),
            ("<Control-Shift-C>", lambda _e: self._shortcut_action(self.show_firmware_compatibility_report)),
            ("<Control-Shift-R>", lambda _e: self._shortcut_action(self.guided_recovery_mode)),
            ("<Control-Shift-S>", lambda _e: self._shortcut_action(self.start_sandbox)),
            ("<Control-Shift-K>", lambda _e: self._shortcut_action(self.commit_sandbox)),
            ("<Control-Shift-D>", lambda _e: self._shortcut_action(self.discard_sandbox)),
            ("<Control-bracketleft>", lambda _e: self._shortcut_action(self._jump_bank_relative, -1)),
            ("<Control-bracketright>", lambda _e: self._shortcut_action(self._jump_bank_relative, 1)),
            ("<Control-Key-1>", lambda _e: self._shortcut_action(self._set_bank_shortcut, 1)),
            ("<Control-Key-2>", lambda _e: self._shortcut_action(self._set_bank_shortcut, 2)),
            ("<Control-Key-3>", lambda _e: self._shortcut_action(self._set_bank_shortcut, 3)),
            ("<Control-Key-4>", lambda _e: self._shortcut_action(self._set_bank_shortcut, 4)),
            ("<Alt-Left>", lambda _e: self._shortcut_action(self._move_active_encoder, -1)),
            ("<Alt-Right>", lambda _e: self._shortcut_action(self._move_active_encoder, 1)),
            ("<Alt-Up>", lambda _e: self._shortcut_action(self._move_active_encoder, -4)),
            ("<Alt-Down>", lambda _e: self._shortcut_action(self._move_active_encoder, 4)),
            ("<Control-slash>", lambda _e: self._shortcut_action(self.show_keyboard_shortcuts)),
        ]
        for pattern, handler in shortcuts:
            self.bind_all(pattern, handler)

    def _shortcut_guard(self) -> bool:
        focus_widget = self.focus_get()
        if focus_widget is None:
            return True
        if isinstance(focus_widget, Text):
            return False
        if isinstance(focus_widget, (ttk.Entry, ttk.Combobox, ttk.Spinbox)):
            return False
        return True

    def _shortcut_action(self, callback, *args):
        if not self._shortcut_guard():
            return None
        callback(*args)
        return "break"

    def _move_active_encoder(self, delta: int) -> None:
        active = self._selected_index()
        bank_start = (active // ENCODERS_PER_BANK) * ENCODERS_PER_BANK
        local = (active % ENCODERS_PER_BANK + delta) % ENCODERS_PER_BANK
        next_idx = bank_start + local
        self.selected_encoders = {next_idx}
        self.last_selected_encoder = next_idx
        self._set_active_index(next_idx)
        self._load_encoder_fields_from_model()
        self._update_knob_visuals()
        self._draw_mini_map()
        self._update_context_labels()

    def _jump_bank_relative(self, step: int) -> None:
        bank = max(1, min(NUM_BANKS, int(self.bank_var.get())))
        bank = (bank - 1 + step) % NUM_BANKS + 1
        self._set_bank_shortcut(bank)

    def _set_bank_shortcut(self, bank: int) -> None:
        active_local = self._selected_index() % ENCODERS_PER_BANK
        target_idx = (max(1, min(NUM_BANKS, bank)) - 1) * ENCODERS_PER_BANK + active_local
        self.selected_encoders = {target_idx}
        self.last_selected_encoder = target_idx
        self._set_active_index(target_idx)
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()
        self._update_context_labels()

    def show_keyboard_shortcuts(self) -> None:
        lines = [
            "Keyboard-First Editing",
            "",
            "Ctrl+Enter: Send Selected",
            "Ctrl+Shift+Enter: Push Bank",
            "Ctrl+Alt+Enter: Push All Banks",
            "Ctrl+G: Push Global",
            "Ctrl+L: Pull Bank",
            "Ctrl+Shift+L: Pull All Banks",
            "Ctrl+Shift+G: Pull Global",
            "",
            "Ctrl+P: Preview Diff",
            "Ctrl+H: Heatmap Selected",
            "Ctrl+Shift+H: Heatmap All",
            "Ctrl+Alt+H: Clear Heatmap",
            "",
            "Ctrl+A: Select All In Bank",
            "Ctrl+R: Select Row",
            "Alt+Arrow Keys: Move active encoder in 4x4 grid",
            "Ctrl+[ / Ctrl+]: Previous/Next bank",
            "Ctrl+1..4: Jump to bank",
            "",
            "Ctrl+Shift+F: Apply Favorites",
            "Ctrl+Shift+C: Compatibility Check",
            "Ctrl+Shift+R: Recovery Mode",
            "Ctrl+Shift+S: Start Sandbox",
            "Ctrl+Shift+K: Commit Sandbox",
            "Ctrl+Shift+D: Discard Sandbox",
            "Ctrl+/: Show this shortcut list",
        ]
        self._show_report_window("Keyboard Shortcuts", lines, width=84, height=28)

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

    def _build_menubar(self) -> None:
        menubar = Menu(self)
        self.config(menu=menubar)

        # ── File ──────────────────────────────────────────────────────────────
        m_file = Menu(menubar, tearoff=False)
        menubar.add_cascade(label="File", menu=m_file)
        m_file.add_command(label="Load JSON", accelerator="Cmd+O", command=self.load_json)
        m_file.add_command(label="Save JSON", accelerator="Cmd+S", command=self.save_json)
        m_file.add_separator()
        m_file.add_command(label="Profile Metadata...", command=self.open_profile_metadata_editor)
        m_file.add_separator()
        m_file.add_command(label="Compare Profile...", command=self.compare_profile_file)
        m_file.add_command(label="Merge Profile...", command=self.merge_profile_file)
        m_file.add_separator()
        m_file.add_command(label="Import Show Pack", command=self.import_everything_bundle)
        m_file.add_command(label="Export Show Pack", command=self.export_everything_bundle)
        m_file.add_command(label="Import Bank Snippet", command=self.import_bank_snippet)
        m_file.add_command(label="Export Bank Snippet", command=self.export_bank_snippet)
        m_file.add_separator()
        m_file.add_command(label="Restore Last Snapshot", command=self.restore_last_snapshot)
        m_file.add_command(label="Export Diagnostics", command=self.export_diagnostics_report)

        # ── Edit ──────────────────────────────────────────────────────────────
        m_edit = Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Edit", menu=m_edit)
        m_edit.add_command(label="Undo", accelerator="Cmd+Z", command=self.undo)
        m_edit.add_command(label="Redo", accelerator="Cmd+Shift+Z", command=self.redo)
        m_edit.add_separator()
        m_edit.add_command(label="Copy Active Encoder", command=self.copy_active_encoder)
        m_edit.add_command(label="Paste To Selected", command=self.paste_to_selected)
        m_edit.add_separator()
        m_edit.add_command(label="Copy To Slot", command=self.copy_to_slot)
        m_edit.add_command(label="Paste Slot To Selected", command=self.paste_from_slot)

        # ── Device ────────────────────────────────────────────────────────────
        m_device = Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Device", menu=m_device)
        m_device.add_command(label="Refresh Ports", command=self.refresh_ports)
        m_device.add_command(label="Connect", command=self.connect)
        m_device.add_command(label="Disconnect", command=self.disconnect)
        m_device.add_separator()
        m_device.add_command(label="MIDI Monitor...", command=self.open_midi_monitor)
        m_device.add_command(label="Setup Wizard...", command=lambda: self.open_setup_wizard(force=True))
        m_device.add_separator()
        m_device.add_command(label="Pull Global", command=self.pull_global)
        m_device.add_command(label="Push Global", command=self.push_global)
        m_device.add_separator()
        m_device.add_command(label="Pull Bank", command=self.pull_bank)
        m_device.add_command(label="Push Bank", command=self.push_bank)
        m_device.add_separator()
        m_device.add_command(label="Pull All Banks", command=self.pull_all_banks)
        m_device.add_command(label="Push All Banks", command=self.push_all_banks)
        m_device.add_separator()
        m_device.add_command(label="Send Selected Encoder", command=self.push_selected_encoder)

        # ── Select ────────────────────────────────────────────────────────────
        m_select = Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Select", menu=m_select)
        m_select.add_command(label="Select Row", command=self.select_row_from_active)
        m_select.add_command(label="Select Column", command=self.select_column_from_active)
        m_select.add_command(label="Select All 16", command=self.select_all_in_bank)
        m_select.add_separator()
        m_select.add_command(label="Preview Diff", command=self.preview_diff_selected)
        m_select.add_command(label="Heatmap Selected", command=self.preview_heatmap_selected)
        m_select.add_command(label="Heatmap All", command=self.preview_heatmap_all)
        m_select.add_command(label="Clear Heatmap", command=self.clear_heatmap)

        # ── Macros ────────────────────────────────────────────────────────────
        m_macros = Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Macros", menu=m_macros)
        m_macros.add_command(label="+ MIDI Ch", command=self.macro_increment_midi_channel)
        m_macros.add_command(label="Remap CC Span", command=self.macro_remap_cc_span)
        m_macros.add_command(label="Invert CC (127-x)", command=self.macro_invert_cc_numbers)
        m_macros.add_command(label="Convert Rel/Abs", command=self.convert_relative_absolute_assistant)

        # ── Presets ───────────────────────────────────────────────────────────
        m_presets = Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Presets", menu=m_presets)
        m_presets.add_command(label="Save Active Preset", command=self.save_named_preset)
        m_presets.add_command(label="Apply Preset To Selected", command=self.apply_named_preset)
        m_presets.add_command(label="Delete Preset", command=self.delete_named_preset)
        m_presets.add_separator()
        m_presets.add_command(label="Import Presets", command=self.import_named_presets)
        m_presets.add_command(label="Export Presets", command=self.export_named_presets)
        m_presets.add_separator()
        m_presets.add_command(label="Apply Template", command=self.apply_selected_template)
        m_presets.add_command(label="Import Template File", command=self.import_template_file)
        m_presets.add_command(label="Refresh Templates", command=self.refresh_template_library)
        m_presets.add_separator()
        m_presets.add_command(label="Export Host Bridge", command=self.export_host_bridge_preset)

        # ── Tools ─────────────────────────────────────────────────────────────
        m_tools = Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Tools", menu=m_tools)
        m_tools.add_command(label="Compat Check", command=self.show_firmware_compatibility_report)
        m_tools.add_command(label="Recovery Mode", command=self.guided_recovery_mode)
        m_tools.add_separator()
        m_tools.add_command(label="Start Sandbox", command=self.start_sandbox)
        m_tools.add_command(label="Commit Sandbox", command=self.commit_sandbox)
        m_tools.add_command(label="Discard Sandbox", command=self.discard_sandbox)
        m_tools.add_separator()
        m_tools.add_command(label="GitHub Patcher", command=self.open_github_patcher)

    def _build_ui(self) -> None:
        self._build_menubar()
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
        toolbar.pack(fill=X, padx=8, pady=(8, 2))
        ttk.Button(toolbar, text="Load JSON", command=self.load_json).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Save JSON", command=self.save_json).pack(side=LEFT, padx=4)
        ttk.Separator(toolbar, orient="vertical").pack(side=LEFT, fill=Y, padx=8)
        ttk.Button(toolbar, text="Undo", command=self.undo).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Redo", command=self.redo).pack(side=LEFT, padx=4)
        ttk.Separator(toolbar, orient="vertical").pack(side=LEFT, fill=Y, padx=8)
        ttk.Button(toolbar, text="Pull Bank", command=self.pull_bank).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Push Bank", command=self.push_bank).pack(side=LEFT, padx=4)
        ttk.Separator(toolbar, orient="vertical").pack(side=LEFT, fill=Y, padx=8)
        ttk.Button(toolbar, text="Pull All Banks", command=self.pull_all_banks).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="Push All Banks", command=self.push_all_banks).pack(side=LEFT, padx=4)

        selector = ttk.Frame(profile_frame)
        selector.pack(fill=X, padx=8, pady=(6, 0))
        ttk.Label(selector, text="Bank").pack(side=LEFT)
        ttk.Spinbox(selector, from_=1, to=4, width=4, textvariable=self.bank_var, command=self._on_selection_changed).pack(side=LEFT, padx=4)
        ttk.Label(selector, text="Encoder").pack(side=LEFT, padx=(14, 0))
        ttk.Spinbox(selector, from_=1, to=16, width=4, textvariable=self.encoder_var, command=self._on_selection_changed).pack(side=LEFT, padx=4)
        ttk.Label(selector, text="Scope").pack(side=LEFT, padx=(14, 0))
        ttk.Combobox(selector, textvariable=self.apply_scope_var, values=list(SCOPE_FIELDS.keys()), width=14, state="readonly").pack(side=LEFT, padx=4)
        ttk.Button(selector, text="Load Active", command=self._load_encoder_fields_from_model).pack(side=LEFT, padx=8)
        ttk.Button(selector, text="Apply To Selected", command=self._apply_encoder_fields_to_model).pack(side=LEFT, padx=4)
        ttk.Button(selector, text="Apply Favorites", command=self.apply_favorites_to_selected).pack(side=LEFT, padx=4)
        ttk.Separator(selector, orient="vertical").pack(side=LEFT, fill=Y, padx=8)
        ttk.Button(selector, text="Send Selected", command=self.push_selected_encoder).pack(side=LEFT, padx=4)

        safety = ttk.Frame(profile_frame)
        safety.pack(fill=X, padx=8, pady=(6, 2))
        ttk.Checkbutton(safety, text="Dry Run", variable=self.dry_run_var).pack(side=LEFT)
        ttk.Label(safety, text="Confirm if sending >=").pack(side=LEFT, padx=(10, 2))
        ttk.Spinbox(safety, from_=1, to=64, width=4, textvariable=self.confirm_threshold_var).pack(side=LEFT)
        ttk.Label(safety, text="encoders").pack(side=LEFT, padx=(4, 0))
        ttk.Separator(safety, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Checkbutton(safety, text="Performance Mode", variable=self.performance_mode_var).pack(side=LEFT)
        ttk.Label(safety, text="Delay(ms)").pack(side=LEFT, padx=(8, 2))
        ttk.Spinbox(safety, from_=0, to=30, width=4, textvariable=self.performance_delay_ms_var).pack(side=LEFT)
        ttk.Label(safety, text="Retry").pack(side=LEFT, padx=(8, 2))
        ttk.Spinbox(safety, from_=0, to=3, width=3, textvariable=self.performance_retry_var).pack(side=LEFT)
        ttk.Separator(safety, orient="vertical").pack(side=LEFT, fill=Y, padx=10)
        ttk.Label(safety, textvariable=self.sandbox_status_var).pack(side=LEFT, padx=(4, 0))

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

        sharing = ttk.Frame(profile_frame)
        sharing.pack(fill=X, padx=8, pady=(0, 6))
        ttk.Label(sharing, text="Template").pack(side=LEFT)
        self.template_combo = ttk.Combobox(
            sharing,
            textvariable=self.template_var,
            values=sorted(self.template_library.keys()),
            width=24,
            state="readonly",
        )
        self.template_combo.pack(side=LEFT, padx=4)
        ttk.Button(sharing, text="Apply Template", command=self.apply_selected_template).pack(side=LEFT, padx=4)
        ttk.Separator(sharing, orient="vertical").pack(side=LEFT, fill=Y, padx=8)
        ttk.Label(sharing, text="Host Bridge").pack(side=LEFT)
        self.host_bridge_combo = ttk.Combobox(
            sharing,
            textvariable=self.host_bridge_var,
            values=sorted(self.host_bridge_presets.keys()),
            width=18,
            state="readonly",
        )
        self.host_bridge_combo.pack(side=LEFT, padx=4)

        metadata_line = ttk.Frame(profile_frame)
        metadata_line.pack(fill=X, padx=8, pady=(0, 4))
        ttk.Label(metadata_line, textvariable=self.metadata_summary_var).pack(side=LEFT)

        patch_line = ttk.Frame(profile_frame)
        patch_line.pack(fill=X, padx=8, pady=(0, 6))
        ttk.Label(patch_line, textvariable=self.patch_status_var).pack(side=LEFT)

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
        content.add(knob_box, weight=2)

        self.knob_canvas = Canvas(knob_box, width=560, height=620, bg="#121419", highlightthickness=1, highlightbackground="#40444f")
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
        content.add(editor_box, weight=3)

        fields_box = ttk.Frame(editor_box)
        fields_box.pack(side=LEFT, fill=BOTH, expand=True, padx=(8, 2), pady=8)

        row = 0
        ttk.Label(fields_box, text="Fav").grid(row=row, column=2, sticky="w", padx=4, pady=3)
        ttk.Label(fields_box, text="Lock").grid(row=row, column=3, sticky="w", padx=4, pady=3)
        row += 1
        for key in ENCODER_TAGS:
            ttk.Label(fields_box, text=key).grid(row=row, column=0, sticky="w", padx=6, pady=3)
            ttk.Spinbox(fields_box, from_=0, to=127, textvariable=self.fields[key], width=10).grid(row=row, column=1, sticky="w", padx=6, pady=3)
            ttk.Checkbutton(fields_box, variable=self.favorite_fields_var[key], takefocus=False).grid(row=row, column=2, sticky="w", padx=4, pady=3)
            ttk.Checkbutton(fields_box, variable=self.lock_fields_var[key], takefocus=False).grid(row=row, column=3, sticky="w", padx=4, pady=3)
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
        ttk.Separator(color_box, orient="horizontal").pack(fill=X, pady=(6, 4))
        ttk.Label(color_box, text="Theme Pack").pack(pady=(2, 2))
        ttk.Combobox(
            color_box,
            textvariable=self.theme_pack_var,
            values=list(THEME_PACKS.keys()),
            width=18,
            state="readonly",
        ).pack(pady=2)
        ttk.Button(color_box, text="Apply Theme To Selected", command=self.apply_theme_to_selected).pack(pady=2)
        ttk.Button(color_box, text="Apply Theme To All 64", command=self.apply_theme_to_all).pack(pady=(2, 6))
        ttk.Separator(color_box, orient="horizontal").pack(fill=X, pady=(6, 4))
        ttk.Label(color_box, text="Auto-Color Rule").pack(pady=(2, 2))
        ttk.Combobox(
            color_box,
            textvariable=self.auto_color_rule_var,
            values=["By MIDI Channel", "By MIDI Type", "By CC Range"],
            width=18,
            state="readonly",
        ).pack(pady=2)
        ttk.Button(color_box, text="Rule To Selected", command=self.apply_auto_color_rule_selected).pack(pady=2)
        ttk.Button(color_box, text="Rule To All 64", command=self.apply_auto_color_rule_all).pack(pady=(2, 6))

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

        def _preferred(ports: list[str], current: str) -> str:
            if current and current in ports:
                return current
            twister = next((p for p in ports if "midi fighter twister" in p.lower()), None)
            return twister if twister else (ports[0] if ports else current)

        if inputs:
            self.input_port_var.set(_preferred(inputs, self.input_port_var.get()))
        if outputs:
            self.output_port_var.set(_preferred(outputs, self.output_port_var.get()))

    def connect(self) -> None:
        try:
            self.client.connect(self.input_port_var.get(), self.output_port_var.get())
            self.status_var.set("Connected")
        except Exception as exc:
            messagebox.showerror("Connection Error", str(exc))

    def disconnect(self) -> None:
        self.client.disconnect()
        self.status_var.set("Disconnected")

    def _maybe_launch_setup_wizard(self) -> None:
        if self.wizard_completed:
            return
        self.open_setup_wizard(force=False)

    def _run_sysex_probe(self, timeout_s: float = 1.8) -> bool:
        try:
            self.client.pull_global_config()
        except Exception:
            return False

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            saw_probe = False
            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                if event.get("type") == "sysex" and event.get("command") == SYSEX_COMMAND_PULL_CONF:
                    saw_probe = True
                self._handle_event(event)
            if saw_probe:
                return True
            time.sleep(0.01)
        return False

    def _wizard_port_dialog(self) -> bool:
        """Show a modal MIDI port-selection step for the Setup Wizard.

        Returns True if the user confirmed their selection, False if cancelled.
        """
        confirmed: list[bool] = [False]

        dlg = Toplevel(self)
        dlg.title("Setup Wizard — Select MIDI Ports")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_set()

        in_var = StringVar(value=self.input_port_var.get())
        out_var = StringVar(value=self.output_port_var.get())

        pad = {"padx": 10, "pady": 6}

        ttk.Label(dlg, text="Select the MIDI ports for your Midi Fighter Twister:").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(12, 4)
        )

        ttk.Label(dlg, text="MIDI Input:").grid(row=1, column=0, sticky="e", **pad)
        in_combo = ttk.Combobox(dlg, textvariable=in_var, width=44, state="readonly")
        in_combo.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(dlg, text="MIDI Output:").grid(row=2, column=0, sticky="e", **pad)
        out_combo = ttk.Combobox(dlg, textvariable=out_var, width=44, state="readonly")
        out_combo.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)

        def _refresh() -> None:
            inputs = self.client.list_input_ports()
            outputs = self.client.list_output_ports()
            in_combo["values"] = inputs
            out_combo["values"] = outputs

            def _preferred(ports: list[str], current: str) -> str:
                if current and current in ports:
                    return current
                twister = next((p for p in ports if "midi fighter twister" in p.lower()), None)
                return twister if twister else (ports[0] if ports else current)

            if inputs:
                in_var.set(_preferred(inputs, in_var.get()))
            if outputs:
                out_var.set(_preferred(outputs, out_var.get()))

        _refresh()

        ttk.Button(dlg, text="Refresh Ports", command=_refresh).grid(
            row=3, column=0, columnspan=3, pady=(2, 8)
        )

        ttk.Separator(dlg, orient="horizontal").grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=10
        )

        btn_frame = ttk.Frame(dlg)
        btn_frame.grid(row=5, column=0, columnspan=3, pady=10)

        def _ok() -> None:
            if not in_var.get() or not out_var.get():
                messagebox.showwarning(
                    "Setup Wizard", "Please select both an input and output port.", parent=dlg
                )
                return
            self.input_port_var.set(in_var.get())
            self.output_port_var.set(out_var.get())
            # keep main combos in sync
            self.input_combo.set(in_var.get())
            self.output_combo.set(out_var.get())
            confirmed[0] = True
            dlg.destroy()

        def _cancel() -> None:
            dlg.destroy()

        ttk.Button(btn_frame, text="Continue", command=_ok, width=14).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=_cancel, width=14).pack(side="left", padx=6)

        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        self.wait_window(dlg)
        return confirmed[0]

    def open_setup_wizard(self, force: bool = False) -> None:
        if self.wizard_completed and not force:
            return

        self.refresh_ports()
        inputs = list(self.input_combo["values"])
        outputs = list(self.output_combo["values"])
        if not inputs or not outputs:
            messagebox.showwarning(
                "Setup Wizard",
                "No MIDI input/output ports found. Connect the Twister and click Refresh Ports, then run Setup Wizard again.",
            )
            return

        if not self.input_port_var.get():
            self.input_port_var.set(inputs[0])
        if not self.output_port_var.get():
            self.output_port_var.set(outputs[0])

        intro = (
            "Setup Wizard will help you connect and pull a safe baseline from your Twister.\n\n"
            "Steps:\n"
            "1) Confirm MIDI ports\n"
            "2) Connect\n"
            "3) Run SysEx probe\n"
            "4) Optionally pull globals + all 64 encoders\n\n"
            "Continue?"
        )
        if not messagebox.askyesno("Setup Wizard", intro):
            return

        if not self._wizard_port_dialog():
            return

        try:
            if not self.client.connected:
                self.client.connect(self.input_port_var.get(), self.output_port_var.get())
                self.status_var.set("Connected")
        except Exception as exc:
            messagebox.showerror("Setup Wizard", f"Could not connect:\n{exc}")
            return

        probe_ok = self._run_sysex_probe()
        probe_line = "SysEx probe: OK" if probe_ok else "SysEx probe: no response detected"
        if not probe_ok and not messagebox.askyesno(
            "Setup Wizard",
            "No SysEx response was detected during probe. Continue anyway and try full pull?",
        ):
            return

        pulled_full = False
        if messagebox.askyesno("Setup Wizard", "Pull globals + all 64 encoders now?"):
            if not self._prepare_for_device_pull("Setup Wizard Pull"):
                return
            try:
                self._pull_full_device_state()
                pulled_full = True
            except Exception as exc:
                messagebox.showerror("Setup Wizard", f"Pull failed:\n{exc}")
                return

        self.wizard_completed = True
        self._save_app_settings()

        lines = [
            "Setup Wizard Complete",
            "",
            f"Connection: {self.input_port_var.get()} -> {self.output_port_var.get()}",
            probe_line,
            f"Full pull: {'completed' if pulled_full else 'skipped'}",
            "",
            "You can rerun Setup Wizard anytime from the MIDI Connection toolbar.",
        ]
        self._show_report_window("Setup Wizard", lines, width=82, height=18)

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
        scoped = SCOPE_FIELDS.get(self.apply_scope_var.get(), SCOPE_FIELDS["All Fields"])
        return [name for name in scoped if not self.lock_fields_var[name].get()]

    def _favorite_unlocked_fields(self) -> list[str]:
        return [
            name
            for name in ENCODER_TAGS
            if self.favorite_fields_var[name].get() and not self.lock_fields_var[name].get()
        ]

    def _apply_encoder_fields_to_model(self) -> None:
        self._push_history()
        fields = self._scoped_fields()
        if not fields:
            messagebox.showwarning("No Editable Fields", "All scoped fields are locked.")
            return
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
        fav_count = sum(1 for name in ENCODER_TAGS if self.favorite_fields_var[name].get())
        locked_count = sum(1 for name in ENCODER_TAGS if self.lock_fields_var[name].get())
        sandbox_label = "active" if self.sandbox_active else "off"
        self.context_var.set(
            f"Active Bank: {bank}   Active Encoder: {enc}   Active Tag: {idx + 1}   "
            f"Scope: {self.apply_scope_var.get()}   Favorites: {fav_count}   Locked: {locked_count}   "
            f"Firmware: {self.profile.metadata.firmware}   Sandbox: {sandbox_label}"
        )

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

    def apply_favorites_to_selected(self) -> None:
        self._push_history()
        fields = self._favorite_unlocked_fields()
        if not fields:
            messagebox.showwarning("No Favorites", "No favorite fields are available to apply (or they are locked).")
            return
        targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
        for idx in targets:
            enc = self.profile.encoders[idx]
            for key in fields:
                setattr(enc, key, clamp7(self.fields[key].get()))
        self._refresh_color_previews()
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

    def _sanitize_named_presets(self, presets_data: object) -> dict[str, dict]:
        clean_presets: dict[str, dict] = {}
        if not isinstance(presets_data, dict):
            return clean_presets
        for name, payload in presets_data.items():
            if not isinstance(name, str) or not isinstance(payload, dict):
                continue
            clean = {}
            for field_name in ENCODER_TAGS:
                if field_name in payload:
                    clean[field_name] = clamp7(payload[field_name])
            if clean:
                clean_presets[name] = clean
        return clean_presets

    def _mark_profile_updated(self) -> None:
        self.profile.metadata.mark_updated()
        self._update_metadata_summary()
        self._update_context_labels()

    def _update_metadata_summary(self) -> None:
        metadata = self.profile.metadata
        tags = ", ".join(metadata.tags[:4]) if metadata.tags else "none"
        source = metadata.template_source or "manual"
        self.metadata_summary_var.set(
            f"Profile: {metadata.name}   Firmware: {metadata.firmware}   Tags: {tags}   Template: {source}"
        )

    def _update_sandbox_status(self) -> None:
        if self.sandbox_active:
            self.sandbox_status_var.set("Sandbox: active, device writes blocked")
        else:
            self.sandbox_status_var.set("Sandbox: off")

    def _clone_named_presets(self) -> dict[str, dict]:
        return json.loads(json.dumps(self.named_presets))

    def _summarize_encoder_targets(self, indices: list[int], limit: int = 6) -> str:
        labels = [self._encoder_label(idx) for idx in indices[:limit]]
        if len(indices) > limit:
            labels.append(f"+{len(indices) - limit} more")
        return ", ".join(labels)

    def _compatibility_findings(self, targets: list[int], include_globals: bool = True, profile: Profile | None = None) -> list[str]:
        current_profile = profile or self.profile
        target_id = str(current_profile.metadata.firmware or "open-source-default").strip() or "open-source-default"
        caps = FIRMWARE_CAPABILITIES.get(target_id)
        if caps is None:
            return [f"Unknown firmware target '{target_id}'. Compatibility rules are unavailable for this profile."]

        findings: list[str] = []
        label = str(caps.get("label") or target_id)
        default_profile = Profile()

        if include_globals:
            unsupported_globals: list[str] = []
            for key in caps.get("unsupported_globals", []):
                value = clamp7(current_profile.globals.get(key, default_profile.globals.get(key, 0)))
                default_value = clamp7(default_profile.globals.get(key, 0))
                if value != default_value:
                    unsupported_globals.append(f"{key}={value}")
            if unsupported_globals:
                findings.append(f"{label} does not support globals: {', '.join(unsupported_globals)}.")

        indicator_limit = int(caps.get("max_indicator_display_type", 3))
        movement_limit = int(caps.get("max_movement", 2))
        midi_type_limit = int(caps.get("max_encoder_midi_type", 5))
        supports_shift_channel = bool(caps.get("supports_shift_channel", True))

        indicator_issues: list[int] = []
        movement_issues: list[int] = []
        midi_type_issues: list[int] = []
        shift_channel_issues: list[int] = []

        for idx in targets:
            cfg = current_profile.encoders[idx]
            default_cfg = default_profile.encoders[idx]
            if cfg.indicator_display_type > indicator_limit:
                indicator_issues.append(idx)
            if cfg.movement > movement_limit:
                movement_issues.append(idx)
            if cfg.encoder_midi_type > midi_type_limit:
                midi_type_issues.append(idx)
            if not supports_shift_channel and cfg.encoder_shift_midi_channel != default_cfg.encoder_shift_midi_channel:
                shift_channel_issues.append(idx)

        if indicator_issues:
            findings.append(
                f"{len(indicator_issues)} encoder(s) use indicator_display_type above {indicator_limit}: {self._summarize_encoder_targets(indicator_issues)}."
            )
        if movement_issues:
            findings.append(
                f"{len(movement_issues)} encoder(s) use movement above {movement_limit}: {self._summarize_encoder_targets(movement_issues)}."
            )
        if midi_type_issues:
            findings.append(
                f"{len(midi_type_issues)} encoder(s) use encoder_midi_type above {midi_type_limit}: {self._summarize_encoder_targets(midi_type_issues)}."
            )
        if shift_channel_issues:
            findings.append(
                f"{len(shift_channel_issues)} encoder(s) set encoder_shift_midi_channel on firmware without shift-channel support: {self._summarize_encoder_targets(shift_channel_issues)}."
            )

        return findings

    def _show_report_window(self, title: str, lines: list[str], width: int = 96, height: int = 28) -> None:
        win = Toplevel(self)
        win.title(title)
        txt = Text(win, wrap="word", width=width, height=height)
        txt.pack(fill=BOTH, expand=True, padx=8, pady=8)
        txt.insert("1.0", "\n".join(lines))
        txt.configure(state="disabled")

    def show_firmware_compatibility_report(self) -> None:
        self._sync_globals_from_ui()
        findings = self._compatibility_findings(list(range(TOTAL_ENCODERS)), include_globals=True)
        target_id = str(self.profile.metadata.firmware or "open-source-default").strip() or "open-source-default"
        label = str(FIRMWARE_CAPABILITIES.get(target_id, {}).get("label") or target_id)
        lines = [
            "Firmware Compatibility Check",
            "",
            f"Target: {label} ({target_id})",
            f"Globals checked: {len(GLOBAL_TAGS)}",
            f"Encoders checked: {TOTAL_ENCODERS}",
            "",
        ]
        if findings:
            lines.append("Warnings")
            lines.extend([f"- {line}" for line in findings])
        else:
            lines.append("No compatibility warnings detected for the current profile.")
        self._show_report_window("Firmware Compatibility", lines, width=100, height=24)

    def start_sandbox(self) -> None:
        if self.sandbox_active:
            messagebox.showinfo("Sandbox", "Sandbox is already active.")
            return
        self.sandbox_base_profile = self.profile.to_json_dict()
        self.sandbox_base_presets = self._clone_named_presets()
        self.sandbox_active = True
        self._update_sandbox_status()
        self._update_context_labels()
        self.status_var.set("Sandbox started. Device writes are blocked until you commit or discard.")

    def commit_sandbox(self, silent: bool = False) -> bool:
        if not self.sandbox_active:
            if not silent:
                messagebox.showinfo("Sandbox", "Sandbox is not active.")
            return False
        self._mark_profile_updated()
        self.sandbox_active = False
        self.sandbox_base_profile = None
        self.sandbox_base_presets = None
        self._save_named_presets(persist=True)
        self._update_sandbox_status()
        self._update_context_labels()
        self.status_var.set("Sandbox committed.")
        if not silent:
            messagebox.showinfo("Sandbox", "Sandbox changes committed.")
        return True

    def discard_sandbox(self, silent: bool = False, force: bool = False) -> bool:
        if not self.sandbox_active:
            if not silent:
                messagebox.showinfo("Sandbox", "Sandbox is not active.")
            return False
        if not force and not messagebox.askyesno(
            "Discard Sandbox",
            "Discard all temporary sandbox edits and restore the last committed editor state?",
        ):
            return False

        if self.sandbox_base_profile is not None:
            self._restore_profile_from_dict(self.sandbox_base_profile)
        if self.sandbox_base_presets is not None:
            self.named_presets = self._sanitize_named_presets(self.sandbox_base_presets)
            self._save_named_presets(persist=True)

        self.sandbox_active = False
        self.sandbox_base_profile = None
        self.sandbox_base_presets = None
        self._update_sandbox_status()
        self._update_context_labels()
        self.status_var.set("Sandbox discarded.")
        if not silent:
            messagebox.showinfo("Sandbox", "Sandbox changes discarded.")
        return True

    def _guard_sandbox_before_device_write(self, label: str) -> bool:
        if not self.sandbox_active:
            return True
        messagebox.showwarning(
            "Sandbox Active",
            f"{label}: sandbox edits are temporary. Use Commit Sandbox or Discard Sandbox before sending to the device.",
        )
        return False

    def _prepare_for_device_pull(self, label: str) -> bool:
        if not self.sandbox_active:
            return True
        if not messagebox.askyesno(
            "Sandbox Active",
            f"{label}: pulling from the device will discard current sandbox edits. Discard sandbox and continue?",
        ):
            return False
        return self.discard_sandbox(silent=True, force=True)

    def _flush_midi_updates(self, timeout_s: float = 1.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self._drain_event_queue()
            time.sleep(0.01)
        self._drain_event_queue()

    def _pull_full_device_state(self) -> None:
        self.client.pull_global_config()
        delay_s = self._transfer_delay_seconds(0.008)
        if delay_s > 0:
            time.sleep(delay_s)
        for idx in range(TOTAL_ENCODERS):
            self.client.pull_encoder(idx + 1)
            if delay_s > 0:
                time.sleep(delay_s)
        self._flush_midi_updates(1.0)
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()
        self._update_context_labels()
        self._update_metadata_summary()

    def guided_recovery_mode(self) -> None:
        if not self.input_port_var.get() or not self.output_port_var.get():
            self.refresh_ports()
        if not self.input_port_var.get() or not self.output_port_var.get():
            messagebox.showwarning("Recovery Mode", "Select MIDI input and output ports first.")
            return

        prompt = (
            "Reconnect and repull globals + all 64 encoders from the selected ports?"
            if not self.client.connected
            else "Repull globals + all 64 encoders from the connected device and run recovery checks?"
        )
        if not messagebox.askyesno("Recovery Mode", prompt):
            return
        if not self._prepare_for_device_pull("Recovery Mode"):
            return

        try:
            if not self.client.connected:
                self.client.connect(self.input_port_var.get(), self.output_port_var.get())
                self.status_var.set("Connected")
            self._pull_full_device_state()
        except Exception as exc:
            messagebox.showerror("Recovery Mode", str(exc))
            return

        validation_errors, validation_warnings = self._validate_profile_for_send(list(range(TOTAL_ENCODERS)), include_globals=True)
        compatibility_warnings = self._compatibility_findings(list(range(TOTAL_ENCODERS)), include_globals=True)
        snapshot_path = self._latest_snapshot_path()

        lines = [
            "Guided Recovery Summary",
            "",
            f"Connection: {self.input_port_var.get()} -> {self.output_port_var.get()}",
            "Repull: globals + 64 encoders completed.",
            f"Latest snapshot: {snapshot_path.name if snapshot_path is not None else 'none'}",
            "",
        ]

        if validation_errors:
            lines.append("Validation Errors")
            lines.extend([f"- {line}" for line in validation_errors])
            lines.append("")
        if validation_warnings:
            lines.append("Validation Warnings")
            lines.extend([f"- {line}" for line in validation_warnings])
            lines.append("")
        if compatibility_warnings:
            lines.append("Compatibility Warnings")
            lines.extend([f"- {line}" for line in compatibility_warnings])
            lines.append("")
        if not validation_errors and not validation_warnings and not compatibility_warnings:
            lines.append("No validation or compatibility issues detected after the repull.")

        self._show_report_window("Guided Recovery", lines, width=104, height=26)
        self.status_var.set("Recovery pull complete. Review the recovery report.")

        if snapshot_path is not None and messagebox.askyesno(
            "Recovery Mode",
            f"Latest snapshot found:\n{snapshot_path.name}\n\nRestore it into the editor now?",
        ):
            self.restore_last_snapshot()

    def _apply_profile_object(self, profile: Profile) -> None:
        self.profile = Profile.from_json_dict(profile.to_json_dict())
        for key in GLOBAL_TAGS:
            self.global_fields[key].set(self.profile.globals.get(key, 0))
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()
        self._update_metadata_summary()

    def _load_template_library(self) -> dict[str, Path]:
        library: dict[str, Path] = {}
        if not self.template_dir.exists():
            return library
        for path in sorted(self.template_dir.glob("*.json")):
            label = path.stem.replace("_", " ").title()
            library[label] = path
        return library

    def _load_host_bridge_presets(self) -> dict[str, dict]:
        library: dict[str, dict] = {}
        if not self.host_preset_dir.exists():
            return library
        for path in sorted(self.host_preset_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("name") or path.stem.replace("_", " ").title()).strip()
            library[label] = raw
        return library

    def _refresh_library_state(self) -> None:
        if self.template_combo is not None:
            self.template_combo["values"] = sorted(self.template_library.keys())
        if self.host_bridge_combo is not None:
            self.host_bridge_combo["values"] = sorted(self.host_bridge_presets.keys())
        if not self.template_var.get() and self.template_library:
            self.template_var.set(next(iter(sorted(self.template_library.keys()))))
        if not self.host_bridge_var.get() and self.host_bridge_presets:
            self.host_bridge_var.set(next(iter(sorted(self.host_bridge_presets.keys()))))

    def refresh_template_library(self) -> None:
        self.template_library = self._load_template_library()
        self.host_bridge_presets = self._load_host_bridge_presets()
        self._refresh_library_state()

    def open_profile_metadata_editor(self) -> None:
        win = Toplevel(self)
        win.title("Profile Metadata")
        win.geometry("620x420")

        metadata = self.profile.metadata
        name_var = StringVar(value=metadata.name)
        tags_var = StringVar(value=", ".join(metadata.tags))
        firmware_var = StringVar(value=metadata.firmware)

        form = ttk.Frame(win)
        form.pack(fill=BOTH, expand=True, padx=10, pady=10)

        ttk.Label(form, text="Name").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=name_var, width=48).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(form, text="Tags").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=tags_var, width=48).grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(form, text="Firmware").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(form, textvariable=firmware_var, values=sorted(FIRMWARE_CAPABILITIES.keys()), width=45).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(form, text="Notes").grid(row=3, column=0, sticky="nw", padx=4, pady=4)

        notes_text = Text(form, wrap="word", width=56, height=14)
        notes_text.grid(row=3, column=1, sticky="nsew", padx=4, pady=4)
        notes_text.insert("1.0", metadata.notes)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(3, weight=1)

        footer = ttk.Frame(win)
        footer.pack(fill=X, padx=10, pady=(0, 10))

        def _save() -> None:
            self._push_history()
            self.profile.metadata.name = name_var.get().strip() or "Twister Profile"
            self.profile.metadata.tags = normalize_tags(tags_var.get())
            self.profile.metadata.firmware = firmware_var.get().strip() or "open-source-default"
            self.profile.metadata.notes = notes_text.get("1.0", "end").strip()
            self._mark_profile_updated()
            win.destroy()

        ttk.Button(footer, text="Save", command=_save).pack(side=LEFT)
        ttk.Button(footer, text="Cancel", command=win.destroy).pack(side=LEFT, padx=8)

    def _apply_template_payload(self, raw: dict, source_label: str) -> None:
        mode = str(raw.get("mode") or "")
        self._push_history()

        if mode in {"portable-show-pack", "everything-bundle"}:
            self._apply_profile_object(Profile.from_json_dict(raw.get("profile", {})))
            presets = self._sanitize_named_presets(raw.get("named_presets", {}))
            if presets:
                self.named_presets.update(presets)
                self._save_named_presets()
        elif mode == "bank-snippet":
            bank = max(1, min(NUM_BANKS, int(self.bank_var.get())))
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
        else:
            self._apply_profile_object(Profile.from_json_dict(raw))

        self.profile.metadata.template_source = source_label
        template_tags = normalize_tags(self.profile.metadata.tags + ["template"])
        self.profile.metadata.tags = template_tags
        self._mark_profile_updated()
        self.status_var.set(f"Applied template: {source_label}")

    def apply_selected_template(self) -> None:
        label = self.template_var.get().strip()
        path = self.template_library.get(label)
        if not label or path is None:
            messagebox.showwarning("Template", "Select a template first.")
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._apply_template_payload(raw, label)
        except Exception as exc:
            messagebox.showerror("Template Error", str(exc))

    def import_template_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            self._apply_template_payload(raw, Path(path).stem)
        except Exception as exc:
            messagebox.showerror("Template Error", str(exc))

    def _load_app_settings(self) -> None:
        self._loading_app_settings = True
        try:
            if not self.settings_file.exists():
                return
            raw = json.loads(self.settings_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return

            input_port = raw.get("input_port")
            output_port = raw.get("output_port")
            apply_scope = raw.get("apply_scope")
            theme_pack = raw.get("theme_pack")
            auto_color_rule = raw.get("auto_color_rule")
            clipboard_slot = raw.get("clipboard_slot")
            patch_manifest_url = raw.get("patch_manifest_url")

            if isinstance(input_port, str):
                self.input_port_var.set(input_port)
            if isinstance(output_port, str):
                self.output_port_var.set(output_port)
            if apply_scope in SCOPE_FIELDS:
                self.apply_scope_var.set(apply_scope)
            if theme_pack in THEME_PACKS:
                self.theme_pack_var.set(theme_pack)
            if auto_color_rule in {"By MIDI Channel", "By MIDI Type", "By CC Range"}:
                self.auto_color_rule_var.set(auto_color_rule)
            if isinstance(clipboard_slot, int):
                self.clipboard_slot_var.set(max(1, min(4, clipboard_slot)))
            if isinstance(patch_manifest_url, str) and patch_manifest_url.strip():
                self.patch_manifest_url_var.set(patch_manifest_url.strip())

            self.dry_run_var.set(bool(raw.get("dry_run", self.dry_run_var.get())))
            self.confirm_threshold_var.set(max(1, int(raw.get("confirm_threshold", self.confirm_threshold_var.get()))))
            self.performance_mode_var.set(bool(raw.get("performance_mode", self.performance_mode_var.get())))
            self.performance_delay_ms_var.set(max(0, int(raw.get("performance_delay_ms", self.performance_delay_ms_var.get()))))
            self.performance_retry_var.set(max(0, min(3, int(raw.get("performance_retry", self.performance_retry_var.get())))))
            self.midi_log_enabled.set(bool(raw.get("midi_log_enabled", self.midi_log_enabled.get())))
            self.wizard_completed = bool(raw.get("wizard_completed", False))

            favorite_fields = raw.get("favorite_fields") if isinstance(raw.get("favorite_fields"), dict) else {}
            lock_fields = raw.get("lock_fields") if isinstance(raw.get("lock_fields"), dict) else {}
            for key in ENCODER_TAGS:
                self.favorite_fields_var[key].set(bool(favorite_fields.get(key, False)))
                self.lock_fields_var[key].set(bool(lock_fields.get(key, False)))
        except Exception:
            pass
        finally:
            self._loading_app_settings = False

    def _app_settings_payload(self) -> dict:
        return {
            "version": APP_SETTINGS_VERSION,
            "input_port": self.input_port_var.get(),
            "output_port": self.output_port_var.get(),
            "apply_scope": self.apply_scope_var.get(),
            "dry_run": bool(self.dry_run_var.get()),
            "confirm_threshold": int(self.confirm_threshold_var.get()),
            "performance_mode": bool(self.performance_mode_var.get()),
            "performance_delay_ms": int(self.performance_delay_ms_var.get()),
            "performance_retry": int(self.performance_retry_var.get()),
            "theme_pack": self.theme_pack_var.get(),
            "auto_color_rule": self.auto_color_rule_var.get(),
            "clipboard_slot": int(self.clipboard_slot_var.get()),
            "patch_manifest_url": self.patch_manifest_url_var.get(),
            "midi_log_enabled": bool(self.midi_log_enabled.get()),
            "wizard_completed": bool(self.wizard_completed),
            "favorite_fields": {key: bool(self.favorite_fields_var[key].get()) for key in ENCODER_TAGS},
            "lock_fields": {key: bool(self.lock_fields_var[key].get()) for key in ENCODER_TAGS},
        }

    def _save_app_settings(self) -> None:
        if self._loading_app_settings:
            return
        try:
            self.settings_file.write_text(json.dumps(self._app_settings_payload(), indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_preferences_changed(self, *_args) -> None:
        if self._loading_app_settings:
            return
        self._update_context_labels()
        self._save_app_settings()

    def _on_close_app(self) -> None:
        if self.sandbox_active and not messagebox.askyesno(
            "Sandbox Active",
            "Discard current sandbox edits and quit?",
        ):
            return
        if self.sandbox_active:
            self.discard_sandbox(silent=True, force=True)
        self._save_app_settings()
        self.destroy()

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

    def _save_named_presets(self, persist: bool | None = None) -> None:
        if persist is None:
            persist = not self.sandbox_active
        if persist:
            self.preset_file.write_text(json.dumps(self.named_presets, indent=2), encoding="utf-8")
        if self.preset_combo is not None:
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
            presets = self._sanitize_named_presets(raw.get("presets", {}))
            if not presets:
                raise ValueError("Invalid presets payload")

            merged = 0
            for name, payload in presets.items():
                self.named_presets[name] = payload
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
        radius = min(step_x, step_y) * 0.34

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
                txt = c.create_text(cx, cy - radius - 14, text=f"{local_encoder + 1}", fill="#dde6f8", font=("TkDefaultFont", 11, "bold"))

                self.knob_items[idx] = {
                    "outer": outer,
                    "inner": inner,
                    "led": led,
                    "label": txt,
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
            heat = self.heatmap_scores.get(idx, 0)

            if idx == active:
                pulse = self.selection_pulse_phase
                outline = "#ffffff" if pulse >= 4 else "#dce8ff"
                width = 3 + (pulse // 3)
            elif selected:
                outline = "#8eb2ff"
                width = 3
            elif heat > 0:
                outline = "#ff5e2f" if heat >= 5 else ("#ff8f3a" if heat >= 3 else "#ffbf55")
                width = 2 + min(2, heat // 3)
            else:
                outline = "#5e6677"
                width = 2

            c.itemconfigure(ids["outer"], outline=outline, width=width)
            c.itemconfigure(ids["led"], outline=led_color)
            c.itemconfigure(ids["inner"], fill="#141820" if not selected else "#1d2330")
            c.itemconfigure(ids["label"], fill="#f2f6ff" if selected else "#dde6f8")

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
                heat = self.heatmap_scores.get(idx, 0)
                if idx == active:
                    outline = "#f4f7ff"
                    width = 2
                elif idx in self.selected_encoders:
                    outline = "#8eb2ff"
                    width = 1
                elif heat > 0:
                    outline = "#ff6c3a" if heat >= 5 else ("#ff9a45" if heat >= 3 else "#ffcc66")
                    width = 1
                else:
                    outline = "#303748"
                    width = 1
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

    def _theme_values(self) -> dict[str, int]:
        return THEME_PACKS.get(self.theme_pack_var.get(), THEME_PACKS["Classic Neon"])

    def apply_theme_to_selected(self) -> None:
        targets = sorted(self.selected_encoders)
        if not targets:
            return
        self._push_history()
        theme = self._theme_values()
        for idx in targets:
            enc = self.profile.encoders[idx]
            enc.active_color = clamp7(theme["active_color"])
            enc.inactive_color = clamp7(theme["inactive_color"])
            enc.detent_color = clamp7(theme["detent_color"])
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def apply_theme_to_all(self) -> None:
        self._push_history()
        theme = self._theme_values()
        for idx in range(TOTAL_ENCODERS):
            enc = self.profile.encoders[idx]
            enc.active_color = clamp7(theme["active_color"])
            enc.inactive_color = clamp7(theme["inactive_color"])
            enc.detent_color = clamp7(theme["detent_color"])
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def _auto_color_values_for_encoder(self, idx: int) -> tuple[int, int, int]:
        enc = self.profile.encoders[idx]
        rule = self.auto_color_rule_var.get()

        if rule == "By MIDI Type":
            midi_type = enc.encoder_midi_type % 6
            active_map = [25, 81, 100, 9, 52, 115]
            inactive_map = [0, 40, 20, 2, 10, 30]
            detent_map = [63, 52, 63, 15, 63, 63]
            return active_map[midi_type], inactive_map[midi_type], detent_map[midi_type]

        if rule == "By CC Range":
            cc = enc.encoder_midi_number
            if cc < 32:
                return 25, 0, 63
            if cc < 64:
                return 81, 20, 63
            if cc < 96:
                return 100, 30, 63
            return 9, 2, 15

        ch = enc.encoder_midi_channel % 16
        active = (ch * 8 + 9) % 128
        inactive = (ch * 4) % 128
        detent = 63 if enc.has_detent else inactive
        return active, inactive, detent

    def _apply_auto_color_rule_to_targets(self, targets: list[int]) -> None:
        if not targets:
            return
        self._push_history()
        for idx in targets:
            a, i, d = self._auto_color_values_for_encoder(idx)
            enc = self.profile.encoders[idx]
            enc.active_color = clamp7(a)
            enc.inactive_color = clamp7(i)
            enc.detent_color = clamp7(d)
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def apply_auto_color_rule_selected(self) -> None:
        self._apply_auto_color_rule_to_targets(sorted(self.selected_encoders))

    def apply_auto_color_rule_all(self) -> None:
        self._apply_auto_color_rule_to_targets(list(range(TOTAL_ENCODERS)))

    def _selected_targets(self) -> list[int]:
        return sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]

    def macro_increment_midi_channel(self) -> None:
        targets = self._selected_targets()
        if not targets:
            return
        step = simpledialog.askinteger("Macro", "Channel step (-15..15)", minvalue=-15, maxvalue=15, initialvalue=1)
        if step is None:
            return
        self._push_history()
        for idx in targets:
            enc = self.profile.encoders[idx]
            enc.encoder_midi_channel = clamp7((enc.encoder_midi_channel + step) % 16)
            enc.switch_midi_channel = clamp7((enc.switch_midi_channel + step) % 16)
            enc.encoder_shift_midi_channel = clamp7((enc.encoder_shift_midi_channel + step) % 16)
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def macro_remap_cc_span(self) -> None:
        targets = self._selected_targets()
        if not targets:
            return
        start = simpledialog.askinteger("Macro", "New CC span start (0..127)", minvalue=0, maxvalue=127, initialvalue=0)
        if start is None:
            return
        end = simpledialog.askinteger("Macro", "New CC span end (0..127)", minvalue=0, maxvalue=127, initialvalue=127)
        if end is None:
            return
        if len(targets) == 1:
            mapped = [start]
        else:
            mapped = [int(start + (end - start) * (i / (len(targets) - 1))) for i in range(len(targets))]

        self._push_history()
        for pos, idx in enumerate(targets):
            cc = clamp7(mapped[pos])
            enc = self.profile.encoders[idx]
            enc.encoder_midi_number = cc
            enc.switch_midi_number = cc
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def macro_invert_cc_numbers(self) -> None:
        targets = self._selected_targets()
        if not targets:
            return
        self._push_history()
        for idx in targets:
            enc = self.profile.encoders[idx]
            enc.encoder_midi_number = clamp7(127 - enc.encoder_midi_number)
            enc.switch_midi_number = clamp7(127 - enc.switch_midi_number)
        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def convert_relative_absolute_assistant(self) -> None:
        targets = self._selected_targets()
        if not targets:
            return

        to_absolute = messagebox.askyesnocancel(
            "Conversion Assistant",
            (
                "Convert selected encoders:\n\n"
                "Yes = Absolute (SEND_CC)\n"
                "No = Relative (SEND_REL_ENC)\n"
                "Cancel = abort"
            ),
        )
        if to_absolute is None:
            return

        target_type = 1 if to_absolute else 2
        target_label = "Absolute (SEND_CC)" if to_absolute else "Relative (SEND_REL_ENC)"
        changed = 0
        for idx in targets:
            if self.profile.encoders[idx].encoder_midi_type != target_type:
                changed += 1

        if changed == 0:
            messagebox.showinfo("Conversion Assistant", f"No changes needed. Selected encoders are already {target_label}.")
            return

        if not messagebox.askyesno(
            "Confirm Conversion",
            f"{changed} of {len(targets)} selected encoder(s) will be converted to {target_label}. Continue?",
        ):
            return

        self._push_history()
        for idx in targets:
            self.profile.encoders[idx].encoder_midi_type = target_type

        self._load_encoder_fields_from_model()
        self._draw_knob_grid()
        self._draw_mini_map()

    def pull_global(self) -> None:
        try:
            if not self._prepare_for_device_pull("Pull Global"):
                return
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

            detent_size = clamp7(self.profile.globals.get("detent_size", 0))
            if detent_size < 1 or detent_size > 31:
                errors.append(f"Global detent_size is {detent_size}; expected 1..31.")

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
        warnings.extend(self._compatibility_findings(targets, include_globals=include_globals))
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
        self._update_metadata_summary()
        self._update_context_labels()

    def _preflight_drift_warning(self, targets: list[int], label: str) -> bool:
        if not self.client.connected or self.dry_run_var.get():
            return True

        baseline = self._capture_device_encoder_snapshot(targets)
        local_profile_before = self.profile.to_json_dict()

        try:
            self.client.pull_global_config()
            time.sleep(self._transfer_delay_seconds(0.01))
            for idx in targets:
                self.client.pull_encoder(idx + 1)
                time.sleep(self._transfer_delay_seconds(0.006))

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

    def _transfer_delay_seconds(self, normal_delay: float) -> float:
        if self.performance_mode_var.get():
            return max(0.0, int(self.performance_delay_ms_var.get()) / 1000.0)
        return normal_delay

    def _transfer_retries(self) -> int:
        if not self.performance_mode_var.get():
            return 0
        return max(0, min(3, int(self.performance_retry_var.get())))

    def _push_encoder_with_retry(self, idx: int) -> None:
        retries = self._transfer_retries()
        wait_s = self._transfer_delay_seconds(0.006)
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                self.client.push_encoder(idx + 1, self.profile.encoders[idx])
                return
            except Exception as exc:
                last_exc = exc
                if attempt >= retries:
                    break
                if wait_s > 0:
                    time.sleep(wait_s)
        if last_exc is not None:
            raise last_exc

    def push_global(self) -> None:
        try:
            if not self._guard_sandbox_before_device_write("Push Global"):
                return
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
            if not self._prepare_for_device_pull("Pull Bank"):
                return
            bank = max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1
            delay_s = self._transfer_delay_seconds(0.01)
            for idx in self._target_indices_for_bank(bank):
                self.client.pull_encoder(idx + 1)
                if delay_s > 0:
                    time.sleep(delay_s)
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_bank(self) -> None:
        try:
            if not self._guard_sandbox_before_device_write("Push Bank"):
                return
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
            delay_s = self._transfer_delay_seconds(0.006)
            for idx in targets:
                self._push_encoder_with_retry(idx)
                if delay_s > 0:
                    time.sleep(delay_s)
            self.status_var.set(f"Bank {bank + 1} push complete. Backup: {snapshot_path.name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def pull_all_banks(self) -> None:
        try:
            if not self._prepare_for_device_pull("Pull All Banks"):
                return
            delay_s = self._transfer_delay_seconds(0.008)
            for idx in range(TOTAL_ENCODERS):
                self.client.pull_encoder(idx + 1)
                if delay_s > 0:
                    time.sleep(delay_s)
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_all_banks(self) -> None:
        try:
            if not self._guard_sandbox_before_device_write("Push All Banks"):
                return
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
            delay_s = self._transfer_delay_seconds(0.004)
            for idx in targets:
                self._push_encoder_with_retry(idx)
                if delay_s > 0:
                    time.sleep(delay_s)
            self.status_var.set(f"All-bank push complete. Backup: {snapshot_path.name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def push_selected_encoder(self) -> None:
        try:
            if not self._guard_sandbox_before_device_write("Send Selected"):
                return
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
            delay_s = self._transfer_delay_seconds(0.006)
            for idx in targets:
                self._push_encoder_with_retry(idx)
                if delay_s > 0:
                    time.sleep(delay_s)
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

    def _compute_profile_compare_lines(self, other: Profile, other_label: str) -> list[str]:
        lines: list[str] = []

        metadata_changes = []
        metadata_fields = ["name", "firmware", "template_source", "host_bridge", "notes"]
        for field_name in metadata_fields:
            cur = getattr(self.profile.metadata, field_name)
            alt = getattr(other.metadata, field_name)
            if cur != alt:
                metadata_changes.append(f"metadata.{field_name}: current={cur!r}   {other_label}={alt!r}")
        if self.profile.metadata.tags != other.metadata.tags:
            metadata_changes.append(
                f"metadata.tags: current={self.profile.metadata.tags!r}   {other_label}={other.metadata.tags!r}"
            )

        global_changes = []
        for key in GLOBAL_TAGS:
            cur = clamp7(self.profile.globals.get(key, 0))
            alt = clamp7(other.globals.get(key, 0))
            if cur != alt:
                global_changes.append(f"global.{key}: current={cur}   {other_label}={alt}")

        field_counts: dict[str, int] = {}
        encoder_details: list[str] = []
        for idx in range(TOTAL_ENCODERS):
            cur = self.profile.encoders[idx]
            alt = other.encoders[idx]
            changes = []
            for field_name in ENCODER_TAGS:
                a = getattr(cur, field_name)
                b = getattr(alt, field_name)
                if a != b:
                    changes.append(f"{field_name}: current={a}   {other_label}={b}")
                    field_counts[field_name] = field_counts.get(field_name, 0) + 1
            if changes:
                bank = idx // ENCODERS_PER_BANK + 1
                enc = idx % ENCODERS_PER_BANK + 1
                encoder_details.append(f"B{bank}E{enc} (# {idx + 1})")
                encoder_details.extend([f"  {item}" for item in changes])

        lines.append("Compare Summary")
        lines.append(f"Globals changed: {len(global_changes)}")
        lines.append(f"Encoders changed: {sum(1 for idx in range(TOTAL_ENCODERS) if any(getattr(self.profile.encoders[idx], f) != getattr(other.encoders[idx], f) for f in ENCODER_TAGS))}")
        lines.append("")

        if field_counts:
            lines.append("Field Change Counts")
            for field_name, count in sorted(field_counts.items(), key=lambda item: (-item[1], item[0])):
                lines.append(f"  {field_name}: {count} encoder(s)")
            lines.append("")

        if metadata_changes:
            lines.append("Metadata Differences")
            lines.extend([f"  {line}" for line in metadata_changes])
            lines.append("")

        if global_changes:
            lines.append("Global Differences")
            lines.extend([f"  {line}" for line in global_changes])
            lines.append("")

        if encoder_details:
            lines.append("Encoder Differences")
            lines.extend(encoder_details)

        if len(lines) <= 4:
            lines.append("No differences found.")
        return lines

    def compare_profile_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("mode") in {"everything-bundle", "portable-show-pack"}:
                other = Profile.from_json_dict(raw.get("profile", {}))
            else:
                other = Profile.from_json_dict(raw)

            lines = self._compute_profile_compare_lines(other, Path(path).name)
            win = Toplevel(self)
            win.title("Profile Compare")
            txt = Text(win, wrap="word", width=100, height=32)
            txt.pack(fill=BOTH, expand=True, padx=8, pady=8)
            txt.insert("1.0", "\n".join(lines))
            txt.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("Compare Error", str(exc))

    def _merge_profile_data(self, incoming: Profile, include_globals: bool, targets: list[int], incoming_wins: bool) -> int:
        changed = 0
        default_profile = Profile()

        if include_globals:
            metadata_fields = ["name", "notes", "firmware", "template_source", "host_bridge"]
            for field_name in metadata_fields:
                cur = getattr(self.profile.metadata, field_name)
                inc = getattr(incoming.metadata, field_name)
                default_val = getattr(default_profile.metadata, field_name)
                if cur == inc:
                    continue
                if incoming_wins or cur == default_val:
                    setattr(self.profile.metadata, field_name, inc)
                    changed += 1
            if self.profile.metadata.tags != incoming.metadata.tags and (incoming_wins or not self.profile.metadata.tags):
                self.profile.metadata.tags = list(incoming.metadata.tags)
                changed += 1

            for key in GLOBAL_TAGS:
                cur = clamp7(self.profile.globals.get(key, 0))
                inc = clamp7(incoming.globals.get(key, cur))
                if cur == inc:
                    continue
                if incoming_wins or cur == clamp7(default_profile.globals.get(key, 0)):
                    self.profile.globals[key] = inc
                    changed += 1

        for idx in targets:
            cur_cfg = self.profile.encoders[idx]
            in_cfg = incoming.encoders[idx]
            def_cfg = default_profile.encoders[idx]
            for field_name in ENCODER_TAGS:
                cur = clamp7(getattr(cur_cfg, field_name))
                inc = clamp7(getattr(in_cfg, field_name))
                if cur == inc:
                    continue
                default_val = clamp7(getattr(def_cfg, field_name))
                if incoming_wins or cur == default_val:
                    setattr(cur_cfg, field_name, inc)
                    changed += 1
        return changed

    def merge_profile_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("mode") in {"everything-bundle", "portable-show-pack"}:
                incoming = Profile.from_json_dict(raw.get("profile", {}))
            else:
                incoming = Profile.from_json_dict(raw)

            scope_all = messagebox.askyesnocancel(
                "Merge Scope",
                (
                    "Merge scope:\n\n"
                    "Yes = merge globals + all encoders\n"
                    "No = merge selected bank only\n"
                    "Cancel = abort"
                ),
            )
            if scope_all is None:
                return

            incoming_wins = messagebox.askyesnocancel(
                "Conflict Policy",
                (
                    "Conflict policy:\n\n"
                    "Yes = incoming wins on conflicts\n"
                    "No = keep current values unless current equals defaults\n"
                    "Cancel = abort"
                ),
            )
            if incoming_wins is None:
                return

            targets = list(range(TOTAL_ENCODERS)) if scope_all else self._target_indices_for_bank(max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1)
            self._push_history()
            changed = self._merge_profile_data(incoming, include_globals=bool(scope_all), targets=targets, incoming_wins=bool(incoming_wins))

            for key in GLOBAL_TAGS:
                self.global_fields[key].set(self.profile.globals.get(key, 0))
            self._load_encoder_fields_from_model()
            self._draw_knob_grid()
            self._draw_mini_map()
            self._update_metadata_summary()
            messagebox.showinfo("Merge Complete", f"Applied {changed} merged value change(s).")
        except Exception as exc:
            messagebox.showerror("Merge Error", str(exc))

    def _portable_show_pack_payload(self) -> dict:
        self._apply_encoder_fields_to_model()
        self._sync_globals_from_ui()
        self._mark_profile_updated()
        profile_data = self.profile.to_json_dict()
        presets_data = self.named_presets
        metadata_data = asdict(self.profile.metadata)
        return {
            "mode": "portable-show-pack",
            "legacy_mode": "everything-bundle",
            "version": 2,
            "exported_at": iso_now(),
            "profile": profile_data,
            "named_presets": presets_data,
            "checksums": {
                "profile": checksum_json(profile_data),
                "named_presets": checksum_json(presets_data),
                "metadata": checksum_json(metadata_data),
            },
        }

    def _verify_portable_show_pack(self, raw: dict) -> list[str]:
        checksums = raw.get("checksums") if isinstance(raw.get("checksums"), dict) else {}
        if not checksums:
            return []
        mismatches: list[str] = []
        if checksums.get("profile") and checksums["profile"] != checksum_json(raw.get("profile", {})):
            mismatches.append("profile checksum mismatch")
        if checksums.get("named_presets") and checksums["named_presets"] != checksum_json(raw.get("named_presets", {})):
            mismatches.append("named_presets checksum mismatch")
        metadata_data = raw.get("profile", {}).get("metadata", {}) if isinstance(raw.get("profile"), dict) else {}
        if checksums.get("metadata") and checksums["metadata"] != checksum_json(metadata_data):
            mismatches.append("metadata checksum mismatch")
        return mismatches

    def _host_bridge_mapping_summary(self, targets: list[int]) -> list[dict]:
        summary: list[dict] = []
        for idx in targets:
            enc = self.profile.encoders[idx]
            summary.append({
                "encoder": self._encoder_label(idx),
                "encoder_midi_channel": clamp7(enc.encoder_midi_channel),
                "encoder_midi_number": clamp7(enc.encoder_midi_number),
                "encoder_midi_type": clamp7(enc.encoder_midi_type),
                "switch_midi_channel": clamp7(enc.switch_midi_channel),
                "switch_midi_number": clamp7(enc.switch_midi_number),
                "switch_midi_type": clamp7(enc.switch_midi_type),
            })
        return summary

    def export_host_bridge_preset(self) -> None:
        label = self.host_bridge_var.get().strip()
        preset = self.host_bridge_presets.get(label)
        if not label or not isinstance(preset, dict):
            messagebox.showwarning("Host Bridge", "Select a host bridge preset first.")
            return

        scope = str(preset.get("scope") or "selected-bank")
        if scope == "all-banks":
            targets = list(range(TOTAL_ENCODERS))
        else:
            bank = max(1, min(NUM_BANKS, int(self.bank_var.get()))) - 1
            targets = self._target_indices_for_bank(bank)

        self._apply_encoder_fields_to_model()
        self._sync_globals_from_ui()
        self.profile.metadata.host_bridge = label
        self._mark_profile_updated()

        default_name = f"twister_{label.lower().replace(' ', '_')}_bridge.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return

        payload = {
            "mode": "plugin-host-bridge-preset",
            "version": 1,
            "host": label,
            "exported_at": iso_now(),
            "scope": scope,
            "notes": preset.get("notes", []),
            "setup_steps": preset.get("setup_steps", []),
            "profile_metadata": asdict(self.profile.metadata),
            "mapping_summary": self._host_bridge_mapping_summary(targets),
        }
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        messagebox.showinfo("Host Bridge Exported", f"Host bridge preset written to:\n{path}")

    def _compute_heatmap_scores(self, targets: list[int]) -> dict[int, int]:
        scores: dict[int, int] = {}
        for idx in targets:
            cur = self.profile.encoders[idx]
            dev = self.device_profile.encoders[idx]
            score = 0
            for field_name in ENCODER_TAGS:
                a = getattr(cur, field_name)
                b = getattr(dev, field_name)
                if a != b:
                    score += 1
            if score > 0:
                scores[idx] = score
        return scores

    def preview_heatmap_selected(self) -> None:
        targets = sorted(self.selected_encoders) if self.selected_encoders else [self._selected_index()]
        self.heatmap_scores = self._compute_heatmap_scores(targets)
        self.heatmap_scope_var.set("Selected")
        self._update_knob_visuals()
        self._draw_mini_map()

    def preview_heatmap_all(self) -> None:
        self.heatmap_scores = self._compute_heatmap_scores(list(range(TOTAL_ENCODERS)))
        self.heatmap_scope_var.set("All")
        self._update_knob_visuals()
        self._draw_mini_map()

    def clear_heatmap(self) -> None:
        self.heatmap_scores = {}
        self.heatmap_scope_var.set("None")
        self._update_knob_visuals()
        self._draw_mini_map()

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
            if raw.get("mode") in {"everything-bundle", "portable-show-pack"}:
                profile_data = raw.get("profile", {})
                self.profile = Profile.from_json_dict(profile_data)
                self.named_presets = self._sanitize_named_presets(raw.get("named_presets", {}))
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
            self._update_metadata_summary()
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))

    def export_everything_bundle(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        data = self._portable_show_pack_payload()
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        messagebox.showinfo("Exported", f"Portable show pack exported to:\n{path}")

    def import_everything_bundle(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All Files", "*")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("mode") not in {"everything-bundle", "portable-show-pack"}:
                raise ValueError("File is not a portable show pack")
            mismatches = self._verify_portable_show_pack(raw)
            if mismatches and not messagebox.askyesno(
                "Checksum Warning",
                "Show pack integrity checks failed:\n\n" + "\n".join(mismatches) + "\n\nImport anyway?",
            ):
                return
            self._push_history()
            self.profile = Profile.from_json_dict(raw.get("profile", {}))
            self.named_presets = self._sanitize_named_presets(raw.get("named_presets", {}))
            self._save_named_presets()
            for key in GLOBAL_TAGS:
                self.global_fields[key].set(self.profile.globals.get(key, 0))
            self._load_encoder_fields_from_model()
            self._draw_knob_grid()
            self._draw_mini_map()
            self._update_metadata_summary()
            messagebox.showinfo("Imported", f"Portable show pack imported from:\n{path}")
        except Exception as exc:
            messagebox.showerror("Import Error", str(exc))

    def save_json(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        self._apply_encoder_fields_to_model()
        self._sync_globals_from_ui()
        self._mark_profile_updated()
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
                "title": APP_NAME,
                "version": APP_VERSION,
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
                "sandbox_active": bool(self.sandbox_active),
            },
            "compatibility": {
                "target": self.profile.metadata.firmware,
                "findings": self._compatibility_findings(list(range(TOTAL_ENCODERS)), include_globals=True),
            },
            "globals": self.profile.globals,
            "recent_midi_log": self.midi_log_lines[-200:],
        }

    def _update_patch_status_summary(self, manifest: dict | None = None) -> None:
        latest_version = "none"
        try:
            history_path = self.patch_dir / "patch_history.jsonl"
            if history_path.exists():
                lines = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if lines:
                    record = json.loads(lines[-1])
                    latest_version = str(record.get("version") or "applied")
        except Exception:
            latest_version = "unknown"

        manifest_version = "unknown"
        if isinstance(manifest, dict):
            manifest_version = str(manifest.get("version") or "unknown")

        self.patch_status_var.set(f"Patcher: latest applied={latest_version} | manifest={manifest_version}")

    def _fetch_patch_manifest(self, url: str) -> dict:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                raw = response.read().decode("utf-8")
            manifest = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Failed to fetch manifest: {exc}") from exc

        if not isinstance(manifest, dict):
            raise RuntimeError("Manifest must be a JSON object.")
        download_url = str(manifest.get("download_url") or "").strip()
        if not download_url:
            raise RuntimeError("Manifest is missing required field: download_url")
        return manifest

    def _download_patch_archive(self, url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Failed to download patch archive: {exc}") from exc

    def _apply_patch_archive(self, payload: bytes, manifest: dict, manifest_url: str) -> tuple[list[str], str]:
        expected_sha = str(manifest.get("sha256") or "").strip().lower()
        actual_sha = hashlib.sha256(payload).hexdigest()
        if expected_sha and expected_sha != actual_sha:
            raise RuntimeError(f"Patch checksum mismatch. expected={expected_sha} actual={actual_sha}")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = Path(__file__).resolve().parent
        backup_root = self.patch_dir / f"backup_{stamp}"
        backup_root.mkdir(parents=True, exist_ok=True)
        archive_path = self.patch_dir / f"patch_{stamp}.zip"
        archive_path.write_bytes(payload)

        applied: list[str] = []
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                member = info.filename.replace("\\", "/")
                parts = [part for part in member.split("/") if part and part != "."]
                if not parts or any(part == ".." for part in parts):
                    raise RuntimeError(f"Unsafe patch path: {info.filename}")

                rel_path = Path(*parts)
                target = (base_dir / rel_path).resolve()
                if not target.is_relative_to(base_dir):
                    raise RuntimeError(f"Patch target escapes app directory: {info.filename}")

                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup_target = backup_root / rel_path
                    backup_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup_target)

                target.write_bytes(zf.read(info))
                applied.append(str(rel_path))

        history_path = self.patch_dir / "patch_history.jsonl"
        history_record = {
            "applied_at": datetime.now().isoformat(timespec="seconds"),
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "manifest_url": manifest_url,
            "download_url": str(manifest.get("download_url") or ""),
            "version": str(manifest.get("version") or ""),
            "sha256": actual_sha,
            "files": applied,
            "backup_dir": str(backup_root),
            "archive_path": str(archive_path),
        }
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(history_record) + "\n")

        return applied, str(backup_root)

    def _restart_application(self) -> None:
        try:
            if getattr(sys, "frozen", False):
                # PyInstaller app: relaunch bundled executable.
                command = [sys.executable]
                run_cwd = str(Path(sys.executable).resolve().parent)
            else:
                # Script mode: relaunch current interpreter + argv.
                command = [sys.executable] + sys.argv
                run_cwd = str(Path(__file__).resolve().parent)

            subprocess.Popen(command, cwd=run_cwd, close_fds=True)
        except Exception as exc:
            messagebox.showerror("Restart Failed", f"Patch applied, but restart failed:\n{exc}")
            return

        self.destroy()

    def open_github_patcher(self) -> None:
        win = Toplevel(self)
        win.title("GitHub Patcher")
        win.geometry("860x440")

        bar = ttk.Frame(win)
        bar.pack(fill=X, padx=10, pady=(10, 6))
        ttk.Label(bar, text="Manifest URL").pack(side=LEFT)
        manifest_entry = ttk.Entry(bar, textvariable=self.patch_manifest_url_var, width=98)
        manifest_entry.pack(side=LEFT, padx=8, fill=X, expand=True)

        out = Text(win, wrap="word", height=18)
        out.pack(fill=BOTH, expand=True, padx=10, pady=(0, 8))

        footer = ttk.Frame(win)
        footer.pack(fill=X, padx=10, pady=(0, 10))

        state: dict[str, dict | None] = {"manifest": None}

        def render(lines: list[str]) -> None:
            out.configure(state="normal")
            out.delete("1.0", "end")
            out.insert("1.0", "\n".join(lines) + "\n")
            out.configure(state="disabled")

        def check_manifest() -> None:
            url = self.patch_manifest_url_var.get().strip()
            if not url:
                messagebox.showwarning("GitHub Patcher", "Enter a manifest URL first.")
                return
            try:
                manifest = self._fetch_patch_manifest(url)
            except Exception as exc:
                messagebox.showerror("GitHub Patcher", str(exc))
                return

            state["manifest"] = manifest
            self._save_app_settings()
            self._update_patch_status_summary(manifest=manifest)
            lines = [
                "Manifest Loaded",
                "",
                f"URL: {url}",
                f"Version: {manifest.get('version', 'n/a')}",
                f"Download: {manifest.get('download_url', 'n/a')}",
                f"SHA256: {manifest.get('sha256', 'not provided')}",
                "",
                "Notes:",
                str(manifest.get("notes", "(no notes)")),
            ]
            render(lines)

        def apply_patch() -> None:
            manifest = state["manifest"]
            if manifest is None:
                check_manifest()
                manifest = state["manifest"]
            if manifest is None:
                return
            if self.sandbox_active:
                messagebox.showwarning("GitHub Patcher", "Commit or discard sandbox edits before patching files.")
                return
            if not messagebox.askyesno(
                "Apply Patch",
                "Download and apply this patch now? Existing files will be backed up before changes are written.",
            ):
                return

            try:
                payload = self._download_patch_archive(str(manifest.get("download_url")))
                applied, backup_dir = self._apply_patch_archive(payload, manifest, self.patch_manifest_url_var.get().strip())
            except Exception as exc:
                messagebox.showerror("GitHub Patcher", str(exc))
                return

            lines = [
                "Patch Applied",
                "",
                f"Version: {manifest.get('version', 'n/a')}",
                f"Files updated: {len(applied)}",
                f"Backup dir: {backup_dir}",
                "",
            ]
            lines.extend([f"- {item}" for item in applied[:120]])
            render(lines)
            self.status_var.set(f"Patch applied: {manifest.get('version', 'n/a')}")
            self._update_patch_status_summary(manifest=manifest)
            if messagebox.askyesno(
                "GitHub Patcher",
                "Patch applied successfully. Restart now to load all updates?",
            ):
                self._restart_application()

        ttk.Button(footer, text="Check Manifest", command=check_manifest).pack(side=LEFT)
        ttk.Button(footer, text="Apply Patch", command=apply_patch).pack(side=LEFT, padx=8)
        ttk.Button(footer, text="Close", command=win.destroy).pack(side=LEFT)

        render([
            "GitHub Patcher",
            "",
            "1) Enter a raw GitHub manifest URL.",
            "2) Click Check Manifest.",
            "3) Click Apply Patch to download, verify, backup, and patch local files.",
        ])

        self._attach_tooltips_recursive(win)

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
