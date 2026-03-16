#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found"
  exit 1
fi

python3 -m pip install --upgrade pyinstaller
python3 -m PyInstaller \
  --noconfirm \
  --windowed \
  --name "MFT Profile GUI" \
  --add-data "presets.json:." \
  app.py

echo "Built app bundle at: dist/MFT Profile GUI.app"
