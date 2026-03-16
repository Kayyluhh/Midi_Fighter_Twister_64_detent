# Midi Fighter Twister Profile + RGB GUI

Desktop app for configuring Midi Fighter Twister profiles and LED colors via MIDI SysEx.

## Features

- Connect to any MIDI in/out ports exposed by the Twister.
- Pull and push global settings through Twister SysEx command 0x01/0x02.
- Pull and push per-encoder settings for a whole bank (16 encoders) via bulk transfer command 0x04.
- Pull and push all banks (all 64 encoders) in one action.
- Live MIDI monitor for outgoing and incoming SysEx activity.
- Diagnostics report export for support and troubleshooting.
- Performance mode with configurable transfer delay and retry for bulk operations.
- Firmware compatibility check based on the profile's target firmware.
- Guided recovery mode for reconnect, repull, validation, and snapshot recovery.
- Session sandbox with explicit commit/discard controls before device writes.
- Local app settings persistence for safety and editing preferences.
- Keyboard-first editing mode with navigation, send/pull, preview, and safety workflow shortcuts.
- Push preview heatmap for selected or all encoders.
- Favorites and lock-fields controls for safer, faster batch editing.
- Color theme packs for quick visual styling.
- Rules-based auto-coloring (by MIDI channel, type, or CC range).
- Advanced multi-edit macros for MIDI channel and CC transforms.
- Relative/absolute conversion assistant for selected encoders.
- Profile compare tool for full-profile and bundle JSON files.
- Profile merge tool with scope and conflict resolution policy.
- Profile metadata editor with notes, tags, firmware, template source, and host bridge fields.
- Built-in bank template library plus custom template import support.
- Host bridge preset export with mapping summaries and setup notes for common DAWs.
- Automatic pre-send snapshots for push actions (saved under `backups/`).
- One-click restore of the latest snapshot, with optional immediate push to device.
- Portable show pack export/import with checksum verification.
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

## Planning + Handoff

- 25-feature implementation plan and live progress tracker:
- `IMPLEMENTATION_ROADMAP_25.md`

## Agent Template JSON

- Sample file for agent-generated custom profiles:
- `examples/custom_profile_template.json`
- To use it, duplicate encoder rows until there are exactly 64 entries, then load it with `Load JSON`.

## Built-In Templates

- `templates/blank_performance_bank.json`
- `templates/detented_mixer_bank.json`
- `templates/fx_relative_bank.json`
- Apply these from the in-app `Template` picker to stamp the current bank quickly.

## Host Bridge Presets

- `host_presets/ableton_live.json`
- `host_presets/bitwig_studio.json`
- `host_presets/traktor_pro.json`
- Use the in-app `Export Host Bridge` action to write a host-specific reference JSON with setup notes and a mapping summary.

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
9. Use `Restore Last Snapshot` for quick rollback if needed.
10. Run `Compat Check` before pushing if you want a full firmware-target report.
11. Use `Start Sandbox` to make temporary edits that cannot reach hardware until you explicitly commit them.
12. Open `Recovery Mode` for reconnect, full repull, validation, and optional snapshot restore guidance.
13. Open `MIDI Monitor` to inspect SysEx TX/RX stream.
14. Use `Heatmap Selected` or `Heatmap All` to preview where pushes will change values.
15. Mark fields as `Fav` and/or `Lock`, then use `Apply Favorites` for quick targeted edits.
16. Apply a `Theme Pack` and/or run an `Auto-Color Rule` on selected knobs or all 64.
17. Use `Macros` for fast channel increment, CC span remap, or CC invert transforms.
18. Use `Convert Rel/Abs` to switch selected encoders between absolute and relative mode.
19. Use `Compare Profile` to inspect differences against another profile file.
20. Use `Merge Profile` to combine incoming profile data with current edits.
21. Use `Profile Metadata` to store notes, tags, firmware target, and sharing context inside the profile.
22. Apply a built-in `Template` or import a custom template JSON.
23. Use `Export Host Bridge` to generate host-specific setup notes and encoder mapping summaries.
24. Use `Export Show Pack` to bundle profile + presets + metadata with integrity checks.
25. Use `Export Diagnostics` to save app/MIDI/safety state and recent logs.
26. Save full JSON or export a bank snippet JSON.
27. Press `Ctrl+/` to open the keyboard shortcut cheat sheet.

## Keyboard Shortcuts

- `Ctrl+Enter`: Send Selected
- `Ctrl+Shift+Enter`: Push Bank
- `Ctrl+Alt+Enter`: Push All Banks
- `Ctrl+G`: Push Global
- `Ctrl+L`: Pull Bank
- `Ctrl+Shift+L`: Pull All Banks
- `Ctrl+Shift+G`: Pull Global
- `Ctrl+P`: Preview Diff
- `Ctrl+H`: Heatmap Selected
- `Ctrl+Shift+H`: Heatmap All
- `Ctrl+Alt+H`: Clear Heatmap
- `Ctrl+A`: Select all 16 encoders in current bank
- `Ctrl+R`: Select active row
- `Alt+Arrow`: Move active encoder in the 4x4 bank grid
- `Ctrl+[` / `Ctrl+]`: Previous/next bank
- `Ctrl+1..4`: Jump directly to bank 1-4
- `Ctrl+Shift+F`: Apply favorites to selected encoders
- `Ctrl+Shift+C`: Run firmware compatibility report
- `Ctrl+Shift+R`: Open guided recovery mode
- `Ctrl+Shift+S`: Start session sandbox
- `Ctrl+Shift+K`: Commit sandbox
- `Ctrl+Shift+D`: Discard sandbox
- `Ctrl+/`: Open keyboard shortcut cheat sheet

## Portable Show Pack

- `Export Show Pack`: writes one JSON containing:
- Full profile (`globals` + 64 encoders)
- Profile metadata
- All named presets
- Integrity checksums for the profile, metadata, and preset payloads
- `Import Show Pack`: restores profile + metadata + named presets from that bundle and warns if checksums do not match.

## Preset Sharing

- `Export Presets`: saves all named presets to a JSON file (`mode: named-presets`).
- `Import Presets`: merges presets from a named-presets JSON file into your local preset library.

## Import/Export Modes

- Full profile JSON: contains global settings and all 64 encoders.
- Bank snippet JSON: contains a single bank's 16 encoders and a `mode: bank-snippet` marker.
- Portable show pack JSON: contains full profile data, metadata, presets, and verification checksums.

## Safety Features

- `Dry Run`: no MIDI send, shows change preview only.
- `Confirm >= N`: asks for confirmation when sending to `N` or more encoders.
- `Performance Mode`: allows tuned send/pull pacing and short retries to improve reliability.
- `Drift Check`: preflight pull detects device-side changes since last baseline and asks before overwrite.
- `Compat Check`: warns when the profile targets firmware that cannot represent some globals or encoder settings.
- `Recovery Mode`: reconnects, repulls the full device, summarizes validation/compatibility results, and offers snapshot recovery.
- `Session Sandbox`: blocks hardware writes until temporary edits are committed or discarded.
- `Validation Guardrails`: blocks invalid channel/range combinations and prompts on risky settings.
- App preferences for safety, themes, port selection, and favorite/lock fields are saved locally in `app_settings.json`.

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
