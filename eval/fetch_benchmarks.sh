#!/usr/bin/env bash
#
# Fetch external benchmark data into eval/external/ (gitignored).
#
# We do NOT vendor RefactorBench into the repo: it bundles full copies of 9 OSS
# repos under mixed licenses, including GPLv3 (Ansible) — committing them would
# pull GPL obligations onto Refactorika.
#
# Usage:
#   bash eval/fetch_benchmarks.sh   # fetch RefactorBench
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTERNAL_DIR="${SCRIPT_DIR}/external"

# Pin to a known-good commit for reproducibility. Update deliberately.
REFACTORBENCH_REPO="https://github.com/microsoft/RefactorBench.git"
REFACTORBENCH_REF="main"   # TODO: pin to a specific commit SHA before relying on it
REFACTORBENCH_DIR="${EXTERNAL_DIR}/refactorbench"

mkdir -p "${EXTERNAL_DIR}"

if [ -d "${REFACTORBENCH_DIR}/.git" ]; then
  echo "RefactorBench already present at ${REFACTORBENCH_DIR} — fetching updates..."
  git -C "${REFACTORBENCH_DIR}" fetch --depth 1 origin "${REFACTORBENCH_REF}"
  git -C "${REFACTORBENCH_DIR}" checkout "${REFACTORBENCH_REF}"
  git -C "${REFACTORBENCH_DIR}" reset --hard "origin/${REFACTORBENCH_REF}" 2>/dev/null || true
else
  echo "Cloning RefactorBench into ${REFACTORBENCH_DIR}..."
  git clone --depth 1 --branch "${REFACTORBENCH_REF}" "${REFACTORBENCH_REPO}" "${REFACTORBENCH_DIR}"
fi

echo "RefactorBench ready: ${REFACTORBENCH_DIR}"
