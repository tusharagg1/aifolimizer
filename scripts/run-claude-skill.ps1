<#
.SYNOPSIS
  Run an aifolimizer analysis skill headlessly via Claude and push the result to
  Telegram. Falls back to the free-LLM backend runner if Claude is unavailable
  (Pro lost / not logged in / no API key). Skill-agnostic — one entry per skill
  in Task Scheduler, no per-skill code.

.PARAMETER Skill
  Skill name, e.g. daily-briefing, top-trades-today, position-review.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\run-claude-skill.ps1 -Skill daily-briefing

.NOTES
  Enforce a max runtime via the Task Scheduler action ("Stop the task if it runs
  longer than"). --dangerously-skip-permissions is required for unattended tool
  use; the MCP server is local + PII-filtered.
#>
param(
    [Parameter(Mandatory = $true)][string]$Skill
)

$Repo     = if ($env:AIFOLIMIZER_ROOT) { $env:AIFOLIMIZER_ROOT } else { Split-Path -Parent $PSScriptRoot }
$Backend  = Join-Path $Repo 'backend'
$Py       = Join-Path $Backend '.venv\Scripts\python.exe'
$Send     = Join-Path $Backend 'scripts\send_telegram.py'
$Fallback = Join-Path $Backend 'scripts\run_skill_fallback.py'
$Session  = Join-Path $HOME '.aifolimizer\ws_session.json'
$LogDir   = Join-Path $HOME '.aifolimizer'
$Log      = Join-Path $LogDir 'skill-runs.log'
$Stamp    = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$Title    = "aifolimizer | $Skill | $(Get-Date -Format 'yyyy-MM-dd')"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Write-Log($msg) { Add-Content -Path $Log -Value "$Stamp [$Skill] $msg" }
function Send-Telegram($text, $title) { $text | & $Py $Send --title $title | Out-Null }

# 1. Preflight — WS session must exist + parse, else tell user to re-auth.
if (-not (Test-Path $Session)) {
    Write-Log 'no WS session file — re-auth needed'
    Send-Telegram 'WS session missing. Run: cd backend; .venv\Scripts\python mcp_login.py (enter MFA).' 'aifolimizer · re-auth needed'
    exit 2
}
try { Get-Content $Session -Raw | ConvertFrom-Json | Out-Null }
catch {
    Write-Log 'WS session file unparseable — re-auth needed'
    Send-Telegram 'WS session corrupt. Re-run mcp_login.py (enter MFA).' 'aifolimizer · re-auth needed'
    exit 2
}

# 2. Primary tier — Claude headless. Resolve claude on PATH; if absent, fall back.
$out = ''
$tier = 'claude'
$ok = $false
$claudeExe = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($claudeExe) {
    Push-Location $Repo
    try {
        $out = (& $claudeExe -p "/$Skill" --dangerously-skip-permissions 2>$null | Out-String)
        if ($LASTEXITCODE -eq 0 -and $out.Trim()) { $ok = $true }
        else { Write-Log "claude exit=$LASTEXITCODE empty=$([string]::IsNullOrWhiteSpace($out))" }
    } catch {
        Write-Log "claude threw: $_"
    } finally {
        Pop-Location
    }
    # Auth/quota failure can surface as text on a zero exit — treat as failure.
    if ($ok -and ($out -match '(?i)(not logged in|unauthorized|authentication failed|quota|rate.?limit|subscription)')) {
        Write-Log 'claude output matched auth/quota error — falling back'
        $ok = $false
    }
} else {
    Write-Log 'claude not found on PATH — falling back'
}

# 3. Fallback tier — free-LLM backend runner (degraded, keeps the brief flowing).
if (-not $ok) {
    $fb = (& $Py $Fallback $Skill 2>$null | Out-String)
    if ($LASTEXITCODE -eq 0 -and $fb.Trim()) {
        $out = $fb
        $tier = 'fallback-free-llm'
        $ok = $true
        Write-Log 'free-LLM fallback produced output'
    } else {
        Write-Log 'fallback unavailable'
        Send-Telegram "Claude unavailable and no free-LLM fallback for $Skill. Check Pro login / API key, or run mcp_login.py." "aifolimizer · $Skill FAILED"
        exit 1
    }
}

# 4. Push the winning output to Telegram.
$pushTitle = $Title
if ($tier -eq 'fallback-free-llm') { $pushTitle = "$Title [fallback: free-LLM]" }
Send-Telegram $out $pushTitle
Write-Log "tier=$tier chars=$($out.Length) pushed"
exit 0
