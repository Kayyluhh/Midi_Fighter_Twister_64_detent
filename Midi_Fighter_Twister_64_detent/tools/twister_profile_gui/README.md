# Midi Fighter Twister Profile + RGB GUI

Desktop app for configuring Midi Fighter Twister profiles and LED colors via MIDI SysEx.

## Features

- Connect to any MIDI in/out ports exposed by the Twister.
- Pull and push global settings through Twister SysEx command 0x01/0x02.
- Pull and push per-encoder settings for a whole bank (16 encoders) via bulk transfer command 0x04.
- Graphical 4x4 Twister-style bank view with per-knob color preview.
- Multi-select editing support: click, drag-box, Shift-add range, and Cmd-toggle.
- One-click quick selection: active row, active column, or all 16 knobs in bank.
- Copy Active / Paste To Selected for fast profile duplication.
- Save and load full 64-encoder profiles as JSON.
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
2. Click Pull Global and Pull Bank to import current device settings.
3. In Graphical Bank View, select one or more knobs (click, drag, Shift, Cmd).
4. Use Quick Select buttons (`Row`, `Column`, `All 16`) or `Copy Active` / `Paste To Selected`.
5. Edit values, or use RGB buttons to pick colors.
6. Click Apply To Selected and Send Selected (or Push Bank).
7. Save JSON to store reusable profiles.

## Notes

- Twister firmware color values are palette indexes (0..127), not direct 24-bit LED values.
- The app resolves this by mapping chosen RGB to the nearest firmware palette color.
- `switch_midi_channel` and `encoder_midi_channel` are 1-based in SysEx transfer, matching firmware behavior.
