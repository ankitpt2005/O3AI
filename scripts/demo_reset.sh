#!/usr/bin/env bash
# ==============================================================================
# O3AI Demo Reset Script
# Restores sample_app/signup.py to its initial broken state for video re-recording.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 &>/dev/null; then
    python3 "${SCRIPT_DIR}/demo_reset.py"
elif command -v python &>/dev/null; then
    python "${SCRIPT_DIR}/demo_reset.py"
else
    echo "[ERROR] Python 3.12+ is required to execute demo_reset.py"
    exit 1
fi
