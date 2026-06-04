#!/usr/bin/env bash
#
# One-command setup for a fresh clone (macOS / Linux / WSL / Git-Bash).
#
#   ./setup.sh
#
# Idempotent: re-running never clobbers an existing .venv, backend/.env, or
# .mcp.json. It creates the venv, installs deps, seeds backend/.env from the
# template, writes .mcp.json with absolute paths for THIS machine, registers
# the MCP server with Claude (if the CLI is present), and runs the doctor.
#
# Override the interpreter used to build the venv with PYTHON=/path/to/python.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

PY_BIN="${PYTHON:-python3}"
command -v "$PY_BIN" >/dev/null 2>&1 || { echo "ERROR: '$PY_BIN' not found. Install Python 3.12+ or set PYTHON=."; exit 1; }
"$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)' \
  || { echo "ERROR: Python 3.12+ required (found $($PY_BIN -V 2>&1)). Set PYTHON= to a 3.12+ interpreter."; exit 1; }

VENV_PY="$REPO/backend/.venv/bin/python"

echo "==> 1/5 virtualenv"
if [ ! -x "$VENV_PY" ]; then
  "$PY_BIN" -m venv backend/.venv
  echo "    created backend/.venv"
else
  echo "    backend/.venv exists — keeping"
fi

echo "==> 2/5 dependencies"
"$VENV_PY" -m pip install --upgrade pip -q
"$VENV_PY" -m pip install -q -r backend/requirements.txt
echo "    installed backend/requirements.txt"

echo "==> 3/5 backend/.env"
if [ ! -f backend/.env ]; then
  cp .env.example backend/.env
  echo "    created backend/.env — EDIT IT and fill WS_EMAIL / WS_PASSWORD"
else
  echo "    backend/.env exists — keeping"
fi

echo "==> 4/5 .mcp.json"
if [ ! -f .mcp.json ]; then
  # Emit via the venv python so paths are JSON-escaped correctly on every OS.
  "$VENV_PY" -c 'import json,sys; json.dump({"mcpServers":{"aifolimizer":{"command":sys.argv[1],"args":[sys.argv[2]]}}}, open(".mcp.json","w"), indent=2)' \
    "$VENV_PY" "$REPO/backend/mcp_server.py"
  echo "    wrote .mcp.json"
else
  echo "    .mcp.json exists — keeping"
fi

if command -v claude >/dev/null 2>&1; then
  echo "    registering MCP server with Claude CLI"
  claude mcp add aifolimizer "$VENV_PY" "$REPO/backend/mcp_server.py" >/dev/null 2>&1 \
    && echo "    registered (restart Claude to pick it up)" \
    || echo "    already registered or registration skipped"
else
  echo "    claude CLI not on PATH — register manually:"
  echo "      claude mcp add aifolimizer \"$VENV_PY\" \"$REPO/backend/mcp_server.py\""
fi

echo "==> 5/5 doctor"
"$VENV_PY" backend/scripts/health_check.py || true

cat <<EOF

Setup done. Next:
  1. Edit backend/.env  (WS_EMAIL / WS_PASSWORD — optional, only for portfolio skills)
  2. cd backend && .venv/bin/python mcp_login.py   # first Wealthsimple login (MFA)
  3. .venv/bin/python run.py                        # start backend on :8000
  4. Restart Claude, then ask "get my profile" or run /daily-briefing
EOF
