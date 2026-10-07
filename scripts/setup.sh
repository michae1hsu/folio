#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required.")'
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!(major > 22 || major === 22 && minor >= 12 || major === 20 && minor >= 19)) { console.error("Node.js 20.19+ or 22.12+ is required."); process.exit(1); }'
command -v npm >/dev/null

if [[ -d .venv/Scripts && ! -x .venv/bin/python ]]; then
    echo 'This checkout has a Windows .venv. Use a separate Linux checkout.' >&2
    exit 1
fi

if ! { command -v soffice >/dev/null || command -v libreoffice >/dev/null; } \
    || ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
    if ! command -v apt-get >/dev/null; then
        echo 'Install LibreOffice, FFmpeg/ffprobe, and CJK fonts, then rerun setup.' >&2
        exit 1
    fi
    apt=(apt-get)
    if [[ "$(id -u)" != 0 ]]; then
        sudo -n true
        apt=(sudo -n apt-get)
    fi
    "${apt[@]}" update
    "${apt[@]}" install -y --no-install-recommends \
        libreoffice-writer libreoffice-calc libreoffice-impress ffmpeg fonts-noto-cjk
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade 'pip>=26.2.0'
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/install_gitleaks.py
.venv/bin/python -m playwright install --with-deps chromium
npm ci
npm run build
# Verify the bundled vocabulary and populate the tokenizer cache offline.
.venv/bin/python -c 'from backend.tokenizer import load_encoding; load_encoding(); print("Folio dependencies and bundled tokenizer are ready.")'
if [[ ! -f .env ]]; then cp .env.example .env; fi
if [[ -e .git ]]; then git config core.hooksPath .githooks; fi
echo 'Configure .env and the separate Google JSON path, then run bash scripts/start.sh.'
