#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
if [[ ! -x .venv/bin/python || ! -f dist/index.html ]]; then
    echo 'Run bash scripts/setup.sh before starting Folio.' >&2
    exit 1
fi
exec .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
