# aifolimizer - Automation Runbook

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

## Phase 0 - one-time setup (do this first)

> Replace `<REPO>` below with your absolute repo path (e.g. `C:\src\aifolimizer`). Set `$env:AIFOLIMIZER_ROOT = '<REPO>'` once and subsequent scripts auto-detect.

1. **Register the MCP server** (already done if `claude mcp get aifolimizer` shows Connected):
   ```powershell
   claude mcp add aifolimizer "<REPO>\backend\.venv\Scripts\python.exe" "<REPO>\backend\mcp_server.py"
   ```
2. **Log in to Wealthsimple** (creds come from `backend\.env`; you only type the MFA code):
   ```powershell
   cd <REPO>\backend
   .venv\Scripts\python mcp_login.py      # enter the MFA code when prompted
   ```
3. **Smoke test** the skill path - restart Claude Code, then in chat: `get my profile`
   or `/daily-briefing`. Live data = working.

## Backend service (keeps the session warm + fallback engine)

Elevated PowerShell (NSSM required - `choco install nssm`):
```powershell
<REPO>\scripts\install-backend-service.ps1
curl http://127.0.0.1:8000/health      # -> {"status":"ok"}
```

## Schedule skills

```powershell
cd <REPO>
scripts\register-skill-task.ps1 -Skill daily-briefing   -Time 07:00 -Days MON,TUE,WED,THU,FRI
scripts\register-skill-task.ps1 -Skill top-trades-today -Time 08:00 -Days MON,TUE,WED,THU,FRI
scripts\register-skill-task.ps1 -Skill position-review  -Time 18:30 -Days DAILY
# Other skills with free-LLM fallback runners are whatever app/services/agent_registry.py
# exposes (run_skill_fallback.py resolves against it) - e.g. weekly-mirror, portfolio-health,
# risk-assessment, adversarial-research, sector-rotation, dividend-strategy, tax-loss-review.
```

## Scheduled data jobs (headless, no LLM)

Two Python jobs run on a schedule independent of the skills above:

```powershell
# Headless morning digest -> Telegram (no LLM, supports --dry-run)
python backend\scripts\send_daily_briefing.py

# Daily post-close data pipeline: scores recommendations, refreshes the
# track-record / calibration / equity loops
python backend\scripts\run_maintenance.py
```

Register them with `register-skill-task.ps1`-style Scheduled Tasks (or cron / launchd on POSIX) the same way as the skill tasks. `send_daily_briefing.py` is the headless task wired to the `aifolimizer-daily-briefing` job; `run_maintenance.py` feeds the nightly scoring loops.

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
  runner - they only run under Claude. The wrapper reports this and exits non-zero.

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

## POSIX equivalents (macOS / Linux)

WS session file path is identical: `~/.aifolimizer/ws_session.json` (mode 0600).
The PowerShell wrapper `run-claude-skill.ps1` now ships a POSIX twin at
[`run-claude-skill.sh`](run-claude-skill.sh) (same claude → free-LLM → Telegram
flow, exit codes 0/1/2). Ready-to-edit launchd / systemd unit files plus a
`sed`-install guide live in [`posix/`](posix/README.md). The reference snippets
below show the same thing inline.

### macOS - launchd

`~/Library/LaunchAgents/com.aifolimizer.daily-briefing.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.aifolimizer.daily-briefing</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/you/aifolimizer/scripts/run-claude-skill.sh</string>
    <string>daily-briefing</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>/Users/you/.aifolimizer/skill-runs.log</string>
  <key>StandardErrorPath</key><string>/Users/you/.aifolimizer/skill-runs.log</string>
</dict>
</plist>
```

Load:
```bash
launchctl load ~/Library/LaunchAgents/com.aifolimizer.daily-briefing.plist
launchctl start com.aifolimizer.daily-briefing   # test now
```

### Linux - systemd --user

`~/.config/systemd/user/aifolimizer-daily-briefing.service`:

```ini
[Unit]
Description=aifolimizer daily-briefing skill

[Service]
Type=oneshot
ExecStart=/bin/bash %h/aifolimizer/scripts/run-claude-skill.sh daily-briefing
StandardOutput=append:%h/.aifolimizer/skill-runs.log
StandardError=append:%h/.aifolimizer/skill-runs.log
```

`~/.config/systemd/user/aifolimizer-daily-briefing.timer`:

```ini
[Unit]
Description=Run aifolimizer daily-briefing weekdays at 07:00

[Timer]
OnCalendar=Mon..Fri 07:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:
```bash
systemctl --user daemon-reload
systemctl --user enable --now aifolimizer-daily-briefing.timer
systemctl --user start aifolimizer-daily-briefing.service   # test now
```

### Cron one-liner (either OS)

```cron
0 7 * * 1-5 /path/to/aifolimizer/scripts/run-claude-skill.sh daily-briefing
```

### Minimal bash wrapper (illustrative)

This is a minimal illustration of the flow, not the shipped file. The real
`scripts/run-claude-skill.sh` is hardened (JSON-envelope parsing, quota/auth
failure classifiers, `AIFOLIMIZER_ROOT` override) - use it directly. Make
executable: `chmod +x scripts/run-claude-skill.sh`.

```bash
#!/usr/bin/env bash
set -uo pipefail
SKILL="${1:?usage: run-claude-skill.sh <skill>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/.aifolimizer/skill-runs.log"
mkdir -p "$(dirname "$LOG")"
SESSION="$HOME/.aifolimizer/ws_session.json"

[[ -r "$SESSION" ]] || { echo "[$(date -Is)] $SKILL re-auth needed" >>"$LOG"; \
  echo "re-auth needed" | "$REPO/backend/.venv/bin/python" "$REPO/backend/scripts/send_telegram.py"; exit 2; }

OUT="$(claude -p "/$SKILL" 2>&1)"; RC=$?
if [[ $RC -ne 0 || -z "$OUT" ]]; then
  OUT="$("$REPO/backend/.venv/bin/python" "$REPO/backend/scripts/run_skill_fallback.py" "$SKILL" 2>&1)"; RC=$?
  [[ $RC -eq 0 ]] && OUT="[fallback: free-LLM]"$'\n'"$OUT"
fi

if [[ $RC -eq 0 ]]; then
  printf '%s' "$OUT" | "$REPO/backend/.venv/bin/python" "$REPO/backend/scripts/send_telegram.py"
  echo "[$(date -Is)] $SKILL OK" >>"$LOG"
else
  echo "$SKILL FAILED" | "$REPO/backend/.venv/bin/python" "$REPO/backend/scripts/send_telegram.py"
  echo "[$(date -Is)] $SKILL FAILED rc=$RC" >>"$LOG"; exit $RC
fi
```
