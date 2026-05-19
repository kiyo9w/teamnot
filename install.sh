#!/usr/bin/env bash
# TeamNoT one-shot installer for Linux / macOS.
#
# Creates a .venv next to this script, installs TeamNoT in editable mode,
# and runs `teamnot doctor` as a final check. Idempotent.
#
# Usage:
#   ./install.sh                  # core only
#   ./install.sh --telegram       # core + Telegram gateway
#   ./install.sh --all --dev      # everything
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WITH_TELEGRAM=0
WITH_HTTP=0
ALL=0
DEV=0
PYTHON=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --telegram)   WITH_TELEGRAM=1; shift ;;
        --http)       WITH_HTTP=1; shift ;;
        --all)        ALL=1; shift ;;
        --dev)        DEV=1; shift ;;
        --python)     PYTHON="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,12p' "$0"; exit 0 ;;
        *)            echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$PYTHON" ]]; then
    if command -v python3.12 >/dev/null 2>&1; then PYTHON=python3.12
    elif command -v python3.11 >/dev/null 2>&1; then PYTHON=python3.11
    elif command -v python3    >/dev/null 2>&1; then PYTHON=python3
    elif command -v python     >/dev/null 2>&1; then PYTHON=python
    else
        echo "No Python 3 found. Install 3.11+ and rerun." >&2
        exit 1
    fi
fi

echo "[teamnot] Using Python: $PYTHON"
$PYTHON -c 'import sys; \
ver=sys.version_info; \
assert ver >= (3, 11), f"Need Python 3.11+ (have {ver.major}.{ver.minor})"; \
print(f"[teamnot] {ver.major}.{ver.minor}.{ver.micro} ok")'

VENV="$ROOT/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "[teamnot] Creating venv at $VENV"
    "$PYTHON" -m venv "$VENV"
else
    echo "[teamnot] Reusing venv at $VENV"
fi

VPY="$VENV/bin/python"
"$VPY" -m pip install --upgrade pip wheel setuptools >/dev/null

EXTRAS=()
if [[ $ALL -eq 1 || $WITH_TELEGRAM -eq 1 ]]; then EXTRAS+=("telegram"); fi
if [[ $ALL -eq 1 || $WITH_HTTP     -eq 1 ]]; then EXTRAS+=("http");     fi
if [[ $DEV -eq 1 ]]; then EXTRAS+=("dev"); fi

if [[ ${#EXTRAS[@]} -gt 0 ]]; then
    SPEC=".[$(IFS=,; echo "${EXTRAS[*]}")]"
else
    SPEC="."
fi

echo "[teamnot] Installing $SPEC"
"$VPY" -m pip install -e "$SPEC"

echo ""
echo "[teamnot] Install complete."
echo ""
echo "Activate the venv in your shell:"
echo "    source ./.venv/bin/activate"
echo ""
echo "Then verify:"
echo "    teamnot doctor"
echo "    teamnot --help"
echo ""

echo "[teamnot] Running doctor inside the venv..."
"$VPY" -m teamnot.cli doctor || \
    echo "[teamnot] Doctor reported missing optional pieces — see above."
