#!/usr/bin/env bash
# One-step launcher: installs dependencies (quietly), starts the Recovery Control Center and opens your browser.
cd "$(dirname "$0")" || exit 1
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
"$PY" -m pip install -q -r requirements.txt || { echo "pip install failed"; exit 1; }
exec "$PY" ui/server.py --open "$@"
