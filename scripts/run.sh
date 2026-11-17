#!/usr/bin/env bash
# One path: install, regenerate the fictional CSVs, serve the page.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q -r requirements.txt
python scripts/generate_data.py
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
