# Twister Profile GUI: 25-Feature Implementation Roadmap

This document is the canonical handoff and progress tracker for all 25 proposed features.
Any agent continuing work should start here and update status/logs before and after coding.

## Objectives

- Deliver all 25 roadmap features with stable behavior.
- Keep stage-risk low with explicit safety and recovery controls.
- Preserve Twister firmware compatibility while introducing optional UX enhancements.

## Current Snapshot (2026-03-15)

- App baseline is advanced and already includes many roadmap items.
- Firmware default visual behavior has been updated to BLENDED_BAR for all physical encoders in src/encoders.c.
- Remaining work is primarily around guardrails, diagnostics, packaging polish, and advanced workflows.

## Status Key

- done: implemented and validated
- in-progress: partially implemented, needs follow-up
- planned: not started

## Feature Matrix

1. Setup Wizard - planned
2. Device Snapshot + Auto-Backup - done
3. One-Click Restore Point - done
4. Live MIDI Activity Monitor - done
5. Conflict or Drift Warning - done
6. Smart Validation Rules - done
7. Performance Mode (smart batching/throttling) - planned
8. Push Preview Heatmap - planned
9. Favorites and Lock Fields - planned
10. Color Theme Packs - planned
11. Per-Bank Color Gradients - done
12. Rules-Based Auto-Coloring - planned
13. Advanced Multi-Edit Macros - planned
14. Relative or Absolute Conversion Assistant - planned
15. Profile Compare Tool - planned
16. Merge Profiles - planned
17. Profile Notes and Tags - planned
18. Templates Library - planned
19. Plugin Host Bridge Presets - planned
20. Keyboard-First Editing Mode - in-progress
21. In-App Firmware Compatibility Check - planned
22. Guided Recovery Mode - planned
23. Portable Show Pack Export - in-progress
24. Session Sandbox - planned
25. Diagnostics Report Generator - done

## Delivery Phases

### Phase 1: Safety and Recovery Foundation

- Feature 2: Device Snapshot + Auto-Backup
- Feature 3: One-Click Restore Point
- Feature 5: Conflict or Drift Warning
- Feature 6: Smart Validation Rules
- Feature 22: Guided Recovery Mode
- Feature 24: Session Sandbox

Acceptance criteria:
- Any bulk push can be reverted in under 10 seconds.
- Drift conflicts are detected before write operations.
- Invalid field combinations are blocked with actionable messages.

### Phase 2: Visibility and Reliability

- Feature 4: Live MIDI Activity Monitor
- Feature 7: Performance Mode
- Feature 8: Push Preview Heatmap
- Feature 21: Firmware Compatibility Check
- Feature 25: Diagnostics Report Generator

Acceptance criteria:
- Users can inspect traffic and diagnose failed sends quickly.
- Performance mode remains stable for all-bank pushes.
- Preflight compatibility catches mismatched expectations.

### Phase 3: Editing Power and Workflow Speed

- Feature 9: Favorites and Lock Fields
- Feature 10: Color Theme Packs
- Feature 12: Rules-Based Auto-Coloring
- Feature 13: Advanced Multi-Edit Macros
- Feature 14: Relative or Absolute Conversion Assistant
- Feature 20: Keyboard-First Editing Mode

Acceptance criteria:
- Common repetitive edits are reduced to one or two actions.
- Locked fields cannot be modified accidentally.
- Keyboard workflow supports full no-mouse operation for core actions.

### Phase 4: Profile Intelligence and Sharing

- Feature 15: Profile Compare Tool
- Feature 16: Merge Profiles
- Feature 17: Profile Notes and Tags
- Feature 18: Templates Library
- Feature 19: Plugin Host Bridge Presets
- Feature 23: Portable Show Pack Export

Acceptance criteria:
- Users can compare and merge profiles with conflict clarity.
- Shared assets are portable across machines and sessions.
- Template and host bridge flows reduce setup effort.

### Phase 5: Onboarding and Final UX

- Feature 1: Setup Wizard

Acceptance criteria:
- First-time users can connect and perform a safe pull without external docs.

## Implementation Notes by Feature

### 1) Setup Wizard
- Add first-run modal with port detection, round-trip SysEx probe, initial pull.
- Persist completion flag in local app settings.

### 2) Device Snapshot + Auto-Backup
- Before push operations, write timestamped backup JSON to backups/.
- Include globals, all 64 encoders, and app metadata.

### 3) One-Click Restore Point
- Add toolbar action Restore Last Snapshot.
- Restore from latest backup after confirmation, then optional immediate push.

### 4) Live MIDI Activity Monitor
- Add dockable panel with filtered event stream (in, out, warnings, errors).
- Include decode of Twister SysEx tags and values.

### 5) Conflict or Drift Warning
- Track baseline hash from last pull.
- On push, compare device pull hash against baseline and prompt on mismatch.

### 6) Smart Validation Rules
- Central validator for channels, ranges, mode combinations.
- Add inline and preflight messages with severity levels.

### 7) Performance Mode
- Configurable send pacing and chunk scheduling.
- Adaptive retry for short transient failures.

### 8) Push Preview Heatmap
- 4x4 bank and 64-mini-map overlays highlighting changed knobs and severity.

### 9) Favorites and Lock Fields
- User-defined field pinning and lock toggles with visual indicators.
- Store in local preferences.

### 10) Color Theme Packs
- Add curated palettes, include high-contrast and colorblind-safe options.

### 11) Per-Bank Color Gradients (already present)
- Keep and validate as part of regression tests.

### 12) Rules-Based Auto-Coloring
- Rules engine using predicates (channel/type/range) and color outputs.

### 13) Advanced Multi-Edit Macros
- Macro actions: increment channels, remap CC spans, invert ranges.

### 14) Relative or Absolute Conversion Assistant
- Guided conversion dialog with impact preview.

### 15) Profile Compare Tool
- Side-by-side diff explorer with field filters and bank scoping.

### 16) Merge Profiles
- Merge wizard with conflict policy options: keep local, keep incoming, manual.

### 17) Profile Notes and Tags
- Extend profile JSON with metadata block (notes, tags, firmware, date).

### 18) Templates Library
- Ship built-in template JSON files and import helper.

### 19) Plugin Host Bridge Presets
- Export helper mappings and setup notes for common hosts.

### 20) Keyboard-First Editing Mode
- Expand shortcuts for selection, scopes, sends, previews, and bank jumps.

### 21) Firmware Compatibility Check
- Detect firmware feature capability from known constants/protocol expectations.
- Warn on unsupported fields before push.

### 22) Guided Recovery Mode
- Recovery flow: reconnect, repull, validate state, restore from snapshot option.

### 23) Portable Show Pack Export
- Extend bundle with metadata and checksums.

### 24) Session Sandbox
- Temporary in-memory edit layer with explicit Commit Sandbox action.

### 25) Diagnostics Report Generator
- Export anonymized diagnostics package: app version, port list, config flags, error log.

## Handoff Workflow (Required)

Before coding:
1. Update the Progress Log with a new entry and planned scope.
2. Set targeted features to in-progress.

After coding:
1. Mark completed feature statuses.
2. Add changed files and validation commands to Progress Log.
3. Record known risks or follow-up tasks.

## Progress Log

### 2026-03-15 (Current Session)
- Added this roadmap and handoff tracker.
- Updated firmware default indicator map to BLENDED_BAR for all physical encoders in src/encoders.c.
- Added sample custom profile template JSON at tools/twister_profile_gui/examples/custom_profile_template.json.
- Updated README with pointers to roadmap and template files.
- Implemented Feature 2: automatic pre-send snapshots written to tools/twister_profile_gui/backups/.
- Implemented Feature 3: one-click restore from latest snapshot with optional immediate push.
- Implemented Feature 5: preflight drift detection re-pulls targets and warns before overwrite.
- Implemented Feature 6: smart send validation with blocking errors and confirmable warnings.
- Implemented Feature 4: live MIDI monitor window with SysEx TX/RX stream summary.
- Implemented Feature 25: diagnostics report export including MIDI state and recent monitor logs.
- Next immediate implementation target: Phase 2 feature 7.
- Validation pending for firmware build in this session.
