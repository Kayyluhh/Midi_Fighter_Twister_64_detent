#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_PATH="dist/MFT Profile GUI.app"
ZIP_PATH="dist/MFT_Profile_GUI_macOS.zip"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found at $APP_PATH. Building first..."
  ./build_macos_app.sh
fi

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

echo "Created release zip: $ZIP_PATH"
