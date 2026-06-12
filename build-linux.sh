#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "tkinter not found. Install it first, e.g.:"
  echo "  Debian/Ubuntu: sudo apt install python3-tk"
  echo "  Fedora:          sudo dnf install python3-tkinter"
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Creating virtual environment..."
  python3 -m venv "$ROOT/.venv"
fi

"$ROOT/.venv/bin/python" -m pip install --upgrade pip -q
"$ROOT/.venv/bin/pip" install -r requirements.txt pyinstaller -q

echo "Building standalone executable..."
"$ROOT/.venv/bin/pyinstaller" --noconfirm --clean CopilotMonitor.spec

if [[ -x "$ROOT/dist/CopilotMonitor" ]]; then
  cp -f config.example.json dist/config.example.json
  echo
  echo "Build complete: dist/CopilotMonitor"
  echo "Copy config.example.json to config.json in dist/ and edit your credentials."
else
  echo "Build failed."
  exit 1
fi
