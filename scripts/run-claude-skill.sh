#!/usr/bin/env bash
#
# Run an aifolimizer analysis skill headlessly via Claude and push the result
# to Telegram. Falls back to the free-LLM backend runner when Claude is
# unavailable (Pro lost / not logged in / no API key). Skill-agnostic - one
# entry per skill in cron/launchd/systemd, no per-skill code.
#
# POSIX twin of scripts/run-claude-skill.ps1. Same exit codes:
#   0 pushed   1 hard-fail (no tier produced output)   2 WS re-auth needed
#
#   ./run-claude-skill.sh daily-briefing
#
# Override repo location with AIFOLIMIZER_ROOT. --dangerously-skip-permissions
# is required for unattended tool use; the MCP server is local + PII-filtered.
set -uo pipefail

SKILL="${1:?usage: run-claude-skill.sh <skill>}"
REPO="${AIFOLIMIZER_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PY="$REPO/backend/.venv/bin/python"
SEND="$REPO/backend/scripts/send_telegram.py"
FALLBACK="$REPO/backend/scripts/run_skill_fallback.py"
SESSION="$HOME/.aifolimizer/ws_session.json"
LOG="$HOME/.aifolimizer/skill-runs.log"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
TITLE="aifolimizer | $SKILL | $(date '+%Y-%m-%d')"

mkdir -p "$(dirname "$LOG")"
log()  { printf '%s [%s] %s\n' "$STAMP" "$SKILL" "$1" >>"$LOG"; }
send() { printf '%s' "$1" | "$PY" "$SEND" --title "$2" >/dev/null 2>&1 || true; }

# 1. Preflight - WS session must exist + parse, else tell user to re-auth.
if [ ! -r "$SESSION" ]; then
  log 'no WS session file - re-auth needed'
  send 'WS session missing. Run: cd backend && .venv/bin/python mcp_login.py (enter MFA).' 'aifolimizer · re-auth needed'
  exit 2
fi
if ! "$PY" -c 'import json,sys; json.load(open(sys.argv[1]))' "$SESSION" >/dev/null 2>&1; then
  log 'WS session file unparseable - re-auth needed'
  send 'WS session corrupt. Re-run mcp_login.py (enter MFA).' 'aifolimizer · re-auth needed'
  exit 2
fi

QUOTA_RE='usage limit|limit reached|reset at|out of (credits|tokens)|rate.?limit|429|529|quota|overloaded|5-?hour limit|weekly limit'
AUTH_RE='not logged in|unauthorized|authentication failed|invalid api key|please (run )?/?login|session expired|subscription required'

# 2. Primary tier - Claude headless (JSON output for reliable failure detection).
out=''
tier='claude'
ok=0
fail_reason='other'   # other | quota | auth | absent

if command -v claude >/dev/null 2>&1; then
  err_file="$(mktemp)"
  raw=''
  exit_code=1
  raw="$( cd "$REPO" && claude -p "/$SKILL" --output-format json --dangerously-skip-permissions 2>"$err_file" )" && exit_code=0 || exit_code=$?
  err="$(cat "$err_file" 2>/dev/null || true)"
  rm -f "$err_file"

  # Parse {result,is_error} envelope; fall back to raw stdout for older claude
  # builds without --output-format json (text-mode compatibility).
  result="$( printf '%s' "$raw" | "$PY" -c 'import json,sys
try:
    j=json.load(sys.stdin); print(j.get("result","") or "")
except Exception:
    pass' 2>/dev/null )"
  is_err="$( printf '%s' "$raw" | "$PY" -c 'import json,sys
try:
    print("1" if json.load(sys.stdin).get("is_error") else "0")
except Exception:
    print("?")' 2>/dev/null )"
  [ -z "$result" ] && [ "$is_err" = '?' ] && result="$raw"   # text-mode

  diag="$result"$'\n'"$err"
  clean=1
  [ -z "$(printf '%s' "$result" | tr -d '[:space:]')" ] && clean=0
  printf '%s' "$diag" | grep -Eqi "$QUOTA_RE" && clean=0
  printf '%s' "$diag" | grep -Eqi "$AUTH_RE"  && clean=0

  if [ "$exit_code" -eq 0 ] && [ "$is_err" != '1' ] && [ "$clean" -eq 1 ]; then
    out="$result"; ok=1
  else
    if   printf '%s' "$diag" | grep -Eqi "$AUTH_RE";  then fail_reason='auth'
    elif printf '%s' "$diag" | grep -Eqi "$QUOTA_RE"; then fail_reason='quota'
    else fail_reason='other'; fi
    log "claude failed exit=$exit_code is_error=$is_err reason=$fail_reason - falling back"
  fi
else
  fail_reason='absent'
  log 'claude not found on PATH - falling back'
fi

# 3. Fallback tier - free-LLM backend runner (degraded, keeps the brief flowing).
if [ "$ok" -ne 1 ]; then
  fb="$( "$PY" "$FALLBACK" "$SKILL" 2>/dev/null )" && fb_rc=0 || fb_rc=$?
  if [ "${fb_rc:-1}" -eq 0 ] && [ -n "$(printf '%s' "$fb" | tr -d '[:space:]')" ]; then
    out="$fb"; tier='fallback-free-llm'; ok=1
    log "free-LLM fallback produced output (claude reason=$fail_reason)"
  else
    log "fallback unavailable (claude reason=$fail_reason)"
    case "$fail_reason" in
      auth)  msg="Claude not logged in / token expired and no free-LLM fallback for $SKILL. Run claude login, or check Pro / mcp_login.py." ;;
      quota) msg="Claude usage limit reached (resets later) and no free-LLM fallback for $SKILL. Set a free LLM key (GITHUB_TOKEN/GOOGLE_API_KEY/...) or wait for reset." ;;
      *)     msg="Claude unavailable and no free-LLM fallback for $SKILL. Check Pro login / API key, or run mcp_login.py." ;;
    esac
    send "$msg" "aifolimizer · $SKILL FAILED"
    exit 1
  fi
fi

# 4. Push the winning output to Telegram.
push_title="$TITLE"
if [ "$tier" = 'fallback-free-llm' ]; then
  [ "$fail_reason" = 'other' ] && why='claude unavailable' || why="claude $fail_reason"
  push_title="$TITLE [fallback: free-LLM - $why]"
fi
send "$out" "$push_title"
log "tier=$tier reason=$fail_reason chars=${#out} pushed"
exit 0
