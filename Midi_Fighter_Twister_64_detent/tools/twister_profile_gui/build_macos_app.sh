#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -x "../../../.venv/bin/python" ]]; then
  PYTHON="../../../.venv/bin/python"
elif [[ -x "../../.venv/bin/python" ]]; then
  PYTHON="../../.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  echo "python3 not found"
  exit 1
fi

if [[ ! -f "presets.json" ]]; then
  echo "{}" > presets.json
fi

"${PYTHON}" -m pip install -r requirements.txt
"${PYTHON}" -m pip install --upgrade pyinstaller
"${PYTHON}" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "MFT Profile GUI" \
  --add-data "presets.json:." \
  --add-data "templates:templates" \
  --add-data "host_presets:host_presets" \
  --add-data "examples:examples" \
  app.py

echo "Built app bundle at: dist/MFT Profile GUI.app"
