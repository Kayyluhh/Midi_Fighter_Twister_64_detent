# MIDI Fighter Twister Firmware Changes

Release target: v1.2

This document summarizes custom changes added in this branch, where they are implemented, and what behavior they add.

## 1) Menu/UI Navigation Mapping (Preserved)

These mappings are still active as requested.

### Bottom-right encoder (Knob 15)
- Rotation: Channel 2, CC 40, relative navigation style.
- Push: Channel 2, CC 41.

Implemented in:
- `src/encoders.c`
  - `default_knob_cc_map` sets knob 15 to CC 40.
  - `default_knob_encoder_channel_map` sets knob 15 to channel 2 (firmware index `1`).
  - `default_knob_encoder_type_map` sets knob 15 to `SEND_REL_ENC`.
  - `default_knob_switch_cc_map` sets knob 15 push to CC 41.

### Right-side bottom two side buttons
- Side switch 5: Channel 2, CC 42 (`UI_OPT`).
- Side switch 6: Channel 2, CC 43 (`UI_EDIT`).

Implemented in:
- `src/side_switch.h`
  - Added `UI_OPT_CH2`, `UI_EDIT_CH2` actions.
- `src/side_switch.c`
  - `UI_NAV_CHANNEL_CH2`, `UI_NAV_CC_OPT`, `UI_NAV_CC_EDIT` constants.
  - Action handlers send 127 on press, 0 on release.
- `src/constants.h`
  - `DEF_SIDE_SW_5_FUNC = UI_OPT_CH2`
  - `DEF_SIDE_SW_6_FUNC = UI_EDIT_CH2`

## 2) Richer Switch Action: Side-Button Fine Adjust Hold

Added side-button hold mode that enables encoder fine movement while held.

Behavior:
- Press/hold configured side switch -> fine adjust active.
- Release -> fine adjust disabled.
- Affects both absolute and relative encoder handling paths.

Implemented in:
- `src/side_switch.h`
  - Added `FINE_ADJUST_HOLD` enum and `side_switch_fine_adjust_active()` prototype.
- `src/side_switch.c`
  - Added `fine_adjust_hold_active` runtime state.
  - Added `side_switch_fine_adjust_active()` getter.
  - Added `FINE_ADJUST_HOLD` action behavior.
- `src/constants.h`
  - `DEF_SIDE_SW_4_FUNC = FINE_ADJUST_HOLD`.
- `src/encoders.c`
  - Fine-adjust checks now include `side_switch_fine_adjust_active()`.

## 3) Quality of Life: Smaller Detent Window

Behavior:
- Reduced detent exit threshold for less sticky center feel.

Implemented in:
- `src/encoders.c`
  - `g_detent_size` changed from `8` to `5`.

## 3.1) New in v1.2: Configurable Detent Size

Behavior:
- Detent exit threshold is now a persisted global setting instead of a firmware-only constant.
- The GUI can pull, edit, and push `detent_size` as part of global configuration.
- Valid range is `1..31`.

Implemented in:
- `src/constants.h`
  - Added `EE_DETENT_SIZE` and `DEF_DETENT_SIZE`.
  - EEPROM layout bumped to `9`.
- `src/config.h`
  - Added `detent_size` to the global config table.
- `src/config.c`
  - SysEx push/pull now persist and report tag `36` for detent size.
  - `load_config()` applies the saved detent size at boot.
- `src/encoders.c`
  - Added `encoders_set_detent_size()` and `encoders_get_detent_size()`.
  - Removed the remaining hardcoded runtime detent threshold.

## 4) Soft Takeover / Pickup for Absolute Encoders

Behavior:
- Absolute encoders wait until physical movement reaches/crosses the current feedback target before sending.
- Prevents parameter jumps when software value and knob position differ.
- Relative encoder types are excluded from pickup gating.

Implemented in:
- `src/encoders.c`
  - Added state: `soft_takeover_enabled`, `soft_takeover_target[]`, `soft_takeover_armed[]`.
  - Added helper: `pickup_target_crossed()`.
  - Added API: `encoders_set_soft_takeover_enabled()`, `encoders_get_soft_takeover_enabled()`.
  - Absolute processing paths now arm/consume pickup logic.
  - MIDI feedback path arms pickup target for active bank absolute mappings.

## 5) Better Bank/Shift Workflows

### Bank wrap mode
Behavior:
- If enabled, bank up wraps last -> first and bank down wraps first -> last.
- If disabled, behavior is edge-clamped.

### Shift latch mode
Behavior:
- If enabled, pressing shift page button toggles latch (press once on, press again off).
- If disabled, legacy hold behavior remains (press to enter, release to exit).

Implemented in:
- `src/side_switch.c`
  - `GLOBAL_BANK_UP`/`GLOBAL_BANK_DOWN` use `global_bank_wrap_mode`.
  - `SHIFT_PAGE_1`/`SHIFT_PAGE_2` use `global_shift_page_latch`.

## 6) SysEx + EEPROM Config Extensions

Added 3 global settings persisted in EEPROM and reported via SysEx pull config.

### New global settings
- Soft takeover enable
- Bank wrap mode
- Shift page latch mode

Implemented in:
- `src/constants.h`
  - `EEPROM_LAYOUT` bumped to `8`.
  - Added EEPROM addresses:
    - `EE_SOFT_TAKEOVER = 0x000E`
    - `EE_BANK_WRAP_MODE = 0x000F`
    - `EE_SHIFT_PAGE_LATCH = 0x0010`
  - Added defaults:
    - `DEF_SOFT_TAKEOVER = true`
    - `DEF_BANK_WRAP_MODE = true`
    - `DEF_SHIFT_PAGE_LATCH = false`
- `src/config.h`
  - `GLOBAL_TABLE_SIZE` expanded from `12` to `15`.
  - Added global fields in table:
    - `soft_takeover`
    - `bank_wrap_mode`
    - `shift_page_latch`
  - Added globals:
    - `global_soft_takeover`
    - `global_bank_wrap_mode`
    - `global_shift_page_latch`
- `src/config.c`
  - SysEx push writes these values to EEPROM.
  - SysEx pull reports tags `33`, `34`, `35`.
  - `load_config()` reads values, applies `encoders_set_soft_takeover_enabled()`.
  - `config_factory_reset()` writes default values.

## 7) Compatibility Notes

- Existing UI/menu navigation CC mapping remains in place.
- Encoder mapping defaults are still table-driven in `factory_reset_encoder_config()`.
- EEPROM layout was bumped, so firmware may reset EEPROM defaults after flashing this build.

## 8) Suggested Quick Test Plan

1. Flash firmware and force factory reset.
2. Confirm UI nav controls:
- Bottom-right encoder rotation sends Ch2 CC40 relative.
- Bottom-right encoder press sends Ch2 CC41.
- Side 5/6 send Ch2 CC42/43.
3. Hold side switch 4 and confirm fine-adjust movement sensitivity.
4. Verify detent feel is less sticky around center.
5. Verify bank wrap up/down behavior at edges.
6. Verify shift latch toggling when enabled.
7. Verify soft takeover prevents jumps with incoming feedback mismatch.
