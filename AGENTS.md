# aifolimizer — Agent Context

## Project
AI investment advisor for Canadian Wealthsimple portfolio. Live data via local FastAPI backend + MCP server. No Anthropic API key — Claude Code/Desktop Pro only.

## Stack
- Backend: FastAPI + FastMCP (Python 3.12, port 8000)
- Frontend: Next.js 16 + Tailwind 4 (port 3000, optional)
- Data: yfinance, FRED, CoinGecko (all free/keyless)

## Entry Points
- `backend/main.py` — FastAPI app
- `backend/mcp_server.py` — MCP server (80 tools)
- `.claude/skills/` — 21 analysis skills

## Important Folders
- `backend/app/services/` — all data/compute logic
- `backend/app/api/ws.py` — REST API routes
- `.claude/context/` — session state (changes.md, architecture.md, lessons.md)
- `scripts/` — automation (Telegram push, scheduled tasks)

## Commands
```bash
# backend
cd backend && .venv/Scripts/activate && uvicorn main:app --reload --port 8000
# frontend
cd frontend && npm run dev
# lint
cd backend && ruff check .
# compile check
cd backend && .venv/Scripts/python.exe -m py_compile mcp_server.py main.py
# tests
cd backend && $env:PYTHONPATH="." ; .venv/Scripts/python.exe -m pytest tests/ -q
```

## Do Not Read
- `backend/.venv/`
- `frontend/node_modules/`
- `frontend/.next/`
- `backend/.pytest_tmp*/`
- `*.log`
- `backend/data/*.jsonl` (runtime data, not source)

Override w/ explicit path when triaging (specific log line, jsonl row).

## Known Gotchas
- Use `ta` lib for technicals — NOT `pandas-ta` (Python 3.12 incompatible)
- MCP + FastAPI share L2 diskcache — cold MCP restart hits L2 if FastAPI warmed within TTL
- WS access+refresh token: RAM + persisted to `~/.aifolimizer/ws_session.json` (0600, outside repo, 8h TTL) to survive restart; password never persisted — `pii_filter.py` must run on every MCP response
- Always call `get_profile` first in any analysis — never hardcode account types or capital
- Single-letter `l` (lowercase L) is ruff E741 — rename to `lo` in candlestick code
