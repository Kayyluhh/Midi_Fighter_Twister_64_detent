#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Midi Fighter Twistluhh Utility"
APP_PATH="dist/${APP_NAME}.app"
ZIP_PATH="dist/Midi_Fighter_Twistluhh_Utility_macOS.zip"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found at $APP_PATH. Building first..."
  ./build_macos_app.sh
fi

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

echo "Created release zip: $ZIP_PATH"
