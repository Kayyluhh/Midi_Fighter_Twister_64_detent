#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Midi Fighter Twistluhh Utility"
ICON_PNG="assets/midi_fighter_twistluhh_icon.png"
ICONSET_DIR="assets/midi_fighter_twistluhh.iconset"
ICON_ICNS="assets/midi_fighter_twistluhh.icns"

if [[ -x "$SCRIPT_DIR/../../../.venv/bin/python" ]]; then
  PYTHON="$(cd "$SCRIPT_DIR/../../../.venv/bin" && pwd)/python"
elif [[ -x "$SCRIPT_DIR/../../.venv/bin/python" ]]; then
  PYTHON="$(cd "$SCRIPT_DIR/../../.venv/bin" && pwd)/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  echo "python3 not found"
  exit 1
fi

if [[ ! -f "presets.json" ]]; then
  echo "{}" > presets.json
fi

if [[ -f "$ICON_PNG" ]]; then
  rm -rf "$ICONSET_DIR"
  mkdir -p "$ICONSET_DIR"
  sips -z 16 16 "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
  sips -z 32 32 "$ICON_PNG" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
  sips -z 64 64 "$ICON_PNG" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
  sips -z 256 256 "$ICON_PNG" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
  sips -z 512 512 "$ICON_PNG" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
  sips -z 1024 1024 "$ICON_PNG" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
  iconutil -c icns "$ICONSET_DIR" -o "$ICON_ICNS"
  rm -rf "$ICONSET_DIR"
fi

"${PYTHON}" -m pip install -r requirements.txt
"${PYTHON}" -m pip install --upgrade pyinstaller
"${PYTHON}" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ICON_ICNS" \
  --hidden-import "mido.backends.rtmidi" \
  --hidden-import "rtmidi" \
  --add-data "presets.json:." \
  --add-data "templates:templates" \
  --add-data "host_presets:host_presets" \
  --add-data "examples:examples" \
  app.py

echo "Built app bundle at: dist/$APP_NAME.app"
