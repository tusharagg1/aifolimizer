---
name: health-check
description: Self-diagnostic for the aifolimizer harness wiring. Use when something is silently broken or before relying on the system — "is everything working?", "health check", "why is the MCP not responding?", "check my setup", "diagnose aifolimizer". Audits MCP server, services, WS token freshness, hooks. Catches stale-token / unregistered-MCP / broken-hook silent failures.
---

# Health Check (Harness Self-Diagnostic)

## Goal

Catch the silent-failure class a solo operator hits with no second pair of eyes:
stale WS token, MCP server that won't import, a service import break, missing
hooks. Output is a PASS/WARN/FAIL report with the one thing to fix.

## How to run

**Step 1 — Run the deterministic diagnostic:**
```
backend/.venv/Scripts/python.exe backend/scripts/health_check.py
```
Reports: python version, mcp_server import + tool count, core service imports,
WS session token freshness (vs WS_TOKEN_TTL_HOURS), settings.json hook events.

**Step 2 — Live data-source check (only if Step 1 PASS):**
- `mcp__aifolimizer__get_data_source_reliability` — per-source success rate +
  latency over the trailing window. Flags providers degrading (yfinance / FRED /
  CoinGecko / SEC).

**Step 3 — Optional backend reachability:**
- `curl -s http://127.0.0.1:8000/health` if the FastAPI backend is expected up.

## Interpreting results

- **ws_session WARN (stale)** → re-auth: delete `~/.aifolimizer/ws_session.json`
  and re-run WS login, or run `mcp_login`.
- **mcp_server_import FAIL** → a recent edit broke import; run
  `py_compile mcp_server.py` and read the traceback.
- **core_services FAIL** → a service has a syntax/dependency error; the detail
  names the module + exception.
- **settings_hooks missing event** → a hook didn't register; check
  `~/.claude/settings.json`.
- **data_source low success rate** → provider outage or rate-limit; the cached
  layer will serve stale data until it recovers.

## Output

Relay the script's OVERALL line + any non-PASS rows, then the single
highest-priority fix. Don't paste the full report unless asked.
