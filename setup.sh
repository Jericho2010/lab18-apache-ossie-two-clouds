#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[setup] Initializing Python 3.11 environment with uv..."
if ! command -v uv >/dev/null 2>&1; then
    echo "[setup] ERROR: uv is not installed." >&2
    exit 1
fi

uv venv --python python3.11 .venv --allow-existing
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[setup] Installing dependencies via uv..."
uv pip install -e ".[dev]"

echo "[setup] Setup complete."
