#!/usr/bin/env bash
# Regenerate `backend/requirements.lock` with hash-locked, fully resolved
# transitive deps from `backend/requirements.txt`.
#
# Why a lockfile: `requirements.txt` uses >= ranges so dev installs stay
# convenient. CI must reproduce a known-good resolution every time and
# verify each wheel by SHA256, otherwise a compromised PyPI mirror or a
# malicious dependency-confusion package can slip in.
#
# Usage:
#   bash backend/scripts/lock_deps.sh
#
# CI pins via `pip install --require-hashes -r backend/requirements.lock`
# once the lockfile is committed.
set -euo pipefail

cd "$(dirname "$0")/.."
python -m pip install --quiet --upgrade pip pip-tools
pip-compile \
  --generate-hashes \
  --resolver=backtracking \
  --output-file=requirements.lock \
  requirements.txt
echo "Wrote backend/requirements.lock"
