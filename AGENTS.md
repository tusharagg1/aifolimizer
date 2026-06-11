# aifolimizer - Agent Context

> Agent-optimized minimal context. Architecture, tech stack, MCP tool table, privacy rules, and code/workflow rules live in [CLAUDE.md](CLAUDE.md) - read that first. This file lists only what's unique for an agent navigating the repo.

## Entry Points

- `backend/main.py` - FastAPI app
- `backend/mcp_server.py` - MCP server (107 tools)
- `backend/mcp_login.py` - interactive WS MFA login (run once)
- `backend/run.py` - uvicorn entry point
- `.claude/skills/` - 28 analysis skills

## Important Folders

- `backend/app/services/` - all data/compute logic
- `backend/app/api/ws.py` - REST API routes
- `.claude/context/` - session state (changes.md, architecture.md, lessons.md, STATE.md)
- `scripts/` - automation (Telegram push, scheduled tasks, NSSM service)
- `docs/` - FAQ + sample skill outputs

## Commands

```powershell
# backend (Windows)
cd backend; .venv\Scripts\activate; uvicorn main:app --reload --port 8000

# register MCP server (one-time, absolute paths)
claude mcp add aifolimizer "<repo>/backend/.venv/Scripts/python.exe" "<repo>/backend/mcp_server.py"

# WS first-time login
cd backend; .venv\Scripts\python.exe mcp_login.py

# lint / compile / test
cd backend; ruff check .
cd backend; .venv\Scripts\python.exe -m py_compile mcp_server.py main.py
pytest   # run from repo root (pyproject testpaths = backend/tests)

# infra (optional)
docker compose up -d
```

```bash
# backend (macOS/Linux)
cd backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000
```

MCP tool names use the `mcp__aifolimizer__<tool>` convention when invoked from Claude.

## Do Not Read

- `backend/.venv/`
- `backend/.pytest_tmp*/`
- `*.log`
- `backend/data/*.jsonl` (runtime data, not source)
- `.data/` (Docker volumes - Postgres/Redis state)

Override with explicit path when triaging (specific log line, jsonl row).

## Known Gotchas

- Use `ta` lib for technicals - NOT `pandas-ta` (Python 3.14 incompatible; project pins 3.12).
- MCP + FastAPI share L2 diskcache - cold MCP restart hits L2 if FastAPI warmed within TTL.
- Always call `get_profile` first in any analysis - never hardcode account types or capital.
- Single-letter `l` (lowercase L) is ruff E741 - rename to `lo` in candlestick code.
- `.mcp.json` at repo root is gitignored (contains absolute paths). Use `.mcp.example.json` as template.
- `.secrets/pg_password.txt` must exist before `docker compose up` - gitignored, create on first clone.
