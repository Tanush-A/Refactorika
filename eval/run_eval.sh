#!/usr/bin/env bash
#
# One-command Refactorika evaluation runner for collaborators.
#
#   bash eval/run_eval.sh            # full run: setup -> fetch -> eval
#   bash eval/run_eval.sh --no-fetch # skip benchmark fetch (use existing eval/external)
#   bash eval/run_eval.sh --setup    # only create venv + install deps, then exit
#
# Idempotent: safe to re-run. Creates a local virtualenv in eval/.venv,
# installs eval/requirements.txt, fetches benchmark data into eval/external/
# (gitignored), then runs the eval driver.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQS="${SCRIPT_DIR}/requirements.txt"
EXTERNAL_DIR="${SCRIPT_DIR}/external"

DO_FETCH=1
SETUP_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-fetch) DO_FETCH=0 ;;
    --setup)    SETUP_ONLY=1 ;;
    -h|--help)  sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# --- 1. Python virtualenv --------------------------------------------------
PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "ERROR: ${PYTHON} not found. Install Python 3.10+ and retry." >&2
  exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
  echo "==> Creating virtualenv at ${VENV_DIR}"
  "${PYTHON}" -m venv "${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

echo "==> Installing eval dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r "${REQS}"

if [ "${SETUP_ONLY}" = "1" ]; then
  echo "==> Setup complete. Activate with: source ${VENV_DIR}/bin/activate"
  exit 0
fi

# --- 2. Fetch benchmark data (gitignored) ----------------------------------
if [ "${DO_FETCH}" = "1" ]; then
  echo "==> Fetching benchmark data"
  bash "${SCRIPT_DIR}/fetch_benchmarks.sh"
else
  echo "==> Skipping fetch (--no-fetch)"
fi

# --- 3. Run the eval driver ------------------------------------------------
echo "==> Running evaluation"
python "${SCRIPT_DIR}/run_eval.py" --external-dir "${EXTERNAL_DIR}"
