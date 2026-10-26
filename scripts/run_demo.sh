#!/usr/bin/env bash
# ==============================================================================
# O3AI End-to-End Demo Runner Script
# Runs the full Goal -> Result lifecycle automatically or interactively.
# Usage:
#   ./scripts/run_demo.sh           (Runs automated demo)
#   ./scripts/run_demo.sh -i        (Pauses at each step for video narration)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 &>/dev/null; then
    python3 "${SCRIPT_DIR}/run_demo.py" "$@"
elif command -v python &>/dev/null; then
    python "${SCRIPT_DIR}/run_demo.py" "$@"
else
    echo "[ERROR] Python 3.12+ is required to execute run_demo.py"
    exit 1
fi
