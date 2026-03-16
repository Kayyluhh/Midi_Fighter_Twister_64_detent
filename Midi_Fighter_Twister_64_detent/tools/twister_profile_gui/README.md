# Midi Fighter Twister Profile + RGB GUI

Desktop app for configuring Midi Fighter Twister profiles and LED colors via MIDI SysEx.

## Features

- Connect to any MIDI in/out ports exposed by the Twister.
- Pull and push global settings through Twister SysEx command 0x01/0x02.
- Pull and push per-encoder settings for a whole bank (16 encoders) via bulk transfer command 0x04.
- Pull and push all banks (all 64 encoders) in one action.
- Graphical 4x4 Twister-style bank view with per-knob color preview.
- Active knob selection pulse animation for clearer focus while editing.
- Bank tabs and a 64-encoder mini map for quick navigation.
- Multi-select editing support: click, drag-box, Shift-add range, and Cmd-toggle.
- One-click quick selection: active row, active column, or all 16 knobs in bank.
- Apply scope selector: All Fields, Colors Only, MIDI Only, Behavior Only.
- Named presets and 4 clipboard slots.
- Preset import/export for sharing preset libraries between machines.
- Undo/Redo with keyboard shortcuts (`Cmd+Z`, `Cmd+Shift+Z`).
- Diff preview against last pulled device state.
- Diff preview includes grouped field-change summary plus per-encoder details.
- Dry-run mode and configurable confirmation threshold for safer bulk sends.
- Color tools for selected knobs: gradient fill, randomize, and hue-index rotate.
- Save and load full 64-encoder profiles as JSON.
- Import and export bank-snippet JSON files.
- One-click everything bundle export/import (full profile + named presets).
- RGB picker maps full RGB color space to the nearest Twister 7-bit palette color index (0..127).

## Requirements

- Python 3.10+
- A CoreMIDI-compatible environment (macOS already includes this)
- Twister connected via USB

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Run

From this folder:

```bash
python3 app.py
```

## Usage

1. Select Twister input/output ports and click Connect.
2. Click Pull Global and Pull Bank (or Pull All Banks) to import current device settings.
3. Choose an apply scope (`All Fields`, `Colors Only`, `MIDI Only`, `Behavior Only`).
4. In Graphical Bank View, select one or more knobs (click, drag, Shift, Cmd).
5. Use quick tools: `Row`, `Column`, `All 16`, clipboard slots, presets, gradient/randomize/rotate.
6. Click Apply To Selected.
7. Use Preview Diff to inspect changes vs last pulled hardware state.
8. Send Selected, Push Bank, or Push All Banks (supports Dry Run and confirmation threshold).
9. Save full JSON or export a bank snippet JSON.

## Everything Bundle

- `Export Everything`: writes one JSON containing:
- Full profile (`globals` + 64 encoders)
- All named presets
- `Import Bundle`: restores profile + named presets from that bundle.

## Preset Sharing

- `Export Presets`: saves all named presets to a JSON file (`mode: named-presets`).
- `Import Presets`: merges presets from a named-presets JSON file into your local preset library.

## Import/Export Modes

- Full profile JSON: contains global settings and all 64 encoders.
- Bank snippet JSON: contains a single bank's 16 encoders and a `mode: bank-snippet` marker.

## Safety Features

- `Dry Run`: no MIDI send, shows change preview only.
- `Confirm >= N`: asks for confirmation when sending to `N` or more encoders.

## macOS App Bundle

Build a one-click `.app` bundle using:

```bash
./build_macos_app.sh
```

Result:

- `dist/MFT Profile GUI.app`

## Notes

- Twister firmware color values are palette indexes (0..127), not direct 24-bit LED values.
- The app resolves this by mapping chosen RGB to the nearest firmware palette color.
- `switch_midi_channel` and `encoder_midi_channel` are 1-based in SysEx transfer, matching firmware behavior.
