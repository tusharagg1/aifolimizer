#!/usr/bin/env bash
# Regenerate `backend/requirements.lock` with hash-locked, fully resolved
# transitive deps from `backend/requirements.txt`.
#
# Why a lockfile: `requirements.txt` uses >= ranges so dev installs stay
# convenient. CI must reproduce a known-good resolution every time and
# verify each wheel by SHA256, otherwise a compromised PyPI mirror or a
# malicious dependency-confusion package can slip in.
#
# uv (not pip-tools) so we can pin the target Python version. CI runs
# Python 3.12, so the lockfile must resolve under that interpreter.
#
# Usage:
#   bash backend/scripts/lock_deps.sh
#
# CI pins via `pip install --require-hashes -r backend/requirements.lock`.
set -euo pipefail

cd "$(dirname "$0")/.."
python -m pip install --quiet --upgrade pip uv
# Target the CI runner's platform (ubuntu-latest x86_64) explicitly so a
# maintainer regenerating from Windows doesn't pull in pywin32 / colorama
# wheels that Linux can't install. CI is the source of truth for "what
# must install"; dev machines install whatever transitives their OS needs
# from the same requirements.txt without going through the lockfile.
python -m uv pip compile \
  --generate-hashes \
  --python-version 3.12 \
  --python-platform x86_64-unknown-linux-gnu \
  --output-file requirements.lock \
  requirements.txt
echo "Wrote backend/requirements.lock"
