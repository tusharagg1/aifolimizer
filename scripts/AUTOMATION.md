# aifolimizer — Automation Runbook

Run analysis skills **automatically, by Claude**, with output pushed to Telegram,
surviving reboots. Free-LLM backend route is the fallback if Claude Pro is lost.

## Architecture

```
Windows Task Scheduler (per skill, cron-like)
   └─ scripts\run-claude-skill.ps1 -Skill <name>
        1. preflight: WS session file present + parseable
        2. PRIMARY:  claude -p "/<skill>"   (Pro)         → high quality
        3. FALLBACK: backend\scripts\run_skill_fallback.py (free LLM, Pro lost)
        4. push result → backend\scripts\send_telegram.py
        5. log → ~\.aifolimizer\skill-runs.log

NSSM service "aifolimizer-backend"  (uvicorn, 24/7)
   └─ keeps the WS session warm (scheduler refreshes the token)
   └─ runs mechanical jobs (alerts, track-record) + is the free-LLM fallback engine
```

WS session file (single, shared): `~\.aifolimizer\ws_session.json`. Written by
`mcp_login.py`, auto-refreshed on use by ws-api, read by both the MCP server and
the backend. Token refreshes silently for the full refresh-token lifetime
(days/weeks); MFA is only needed on first login and when Wealthsimple forces
re-auth.

## Phase 0 — one-time setup (do this first)

1. **Register the MCP server** (already done if `claude mcp get aifolimizer` shows Connected):
   ```powershell
   claude mcp add aifolimizer "C:\Users\Tusha\Documents\projects\aifolimizer\backend\.venv\Scripts\python.exe" "C:\Users\Tusha\Documents\projects\aifolimizer\backend\mcp_server.py"
   ```
2. **Log in to Wealthsimple** (creds come from `backend\.env`; you only type the MFA code):
   ```powershell
   cd C:\Users\Tusha\Documents\projects\aifolimizer\backend
   .venv\Scripts\python mcp_login.py      # enter the MFA code when prompted
   ```
3. **Smoke test** the skill path — restart Claude Code, then in chat: `get my profile`
   or `/daily-briefing`. Live data = working.

## Backend service (keeps the session warm + fallback engine)

Elevated PowerShell (NSSM required — `choco install nssm`):
```powershell
C:\Users\Tusha\Documents\projects\aifolimizer\scripts\install-backend-service.ps1
curl http://127.0.0.1:8000/health      # -> {"status":"ok"}
```

## Schedule skills

```powershell
cd C:\Users\Tusha\Documents\projects\aifolimizer
scripts\register-skill-task.ps1 -Skill daily-briefing   -Time 07:00 -Days MON,TUE,WED,THU,FRI
scripts\register-skill-task.ps1 -Skill top-trades-today -Time 08:00 -Days MON,TUE,WED,THU,FRI
scripts\register-skill-task.ps1 -Skill position-review  -Time 18:30 -Days DAILY
# others (have free-LLM fallback runners): weekly-mirror, portfolio-health,
# risk-assessment, adversarial-research, sector-rotation, dividend-strategy,
# auto-rebalance, tax-loss-review
```

Test a task immediately:
```powershell
Start-ScheduledTask -TaskName 'aifolimizer\daily-briefing'
Get-Content ~\.aifolimizer\skill-runs.log -Tail 5
```

## Manual run (no scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-claude-skill.ps1 -Skill daily-briefing
```

## MFA re-auth (rare)

When Wealthsimple forces re-auth, the backend pushes a Telegram alert and the
wrapper sends "re-auth needed". Fix: re-run `mcp_login.py` (Phase 0 step 2) and
type the MFA code. Optional future upgrade: reply to Telegram with the code
(see the plan's "MFA relay" phase).

## Resilience tiers

- **Claude Pro present** → `claude -p` runs the skill (primary).
- **Pro lost / not logged in / no API key** → wrapper auto-falls back to the
  free-LLM backend runner; brief is tagged `[fallback: free-LLM]`. Requires ≥1
  free key in `backend\.env`: `GITHUB_TOKEN` / `GOOGLE_API_KEY` /
  `OPENROUTER_API_KEY` / `DASHSCOPE_API_KEY`.
- New composer skills (`top-trades-today`, `position-review`) have **no** free-LLM
  runner — they only run under Claude. The wrapper reports this and exits non-zero.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Telegram "re-auth needed" | Session expired → run `mcp_login.py`. |
| Telegram "... FAILED" | Claude unavailable AND no fallback runner. Check `claude` login / PATH, or add a free LLM key. |
| No Telegram at all | `backend\.env` missing `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; test: `echo hi | .venv\Scripts\python scripts\send_telegram.py`. |
| `mcp list` slow / times out | MCP cold-import ~5s (eager service imports). Harmless; `claude -p` waits. Faster start = lazy-import pass (perf-optimizer). |
| Task didn't run | PC was off/asleep at trigger; `StartWhenAvailable` runs it at next wake. Verify task in Task Scheduler. |

## Logs

- Skill runs: `~\.aifolimizer\skill-runs.log`
- Backend service: `~\.aifolimizer\logs\backend.{out,err}.log`
