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

# Failure classifiers — match against Claude's JSON result, stderr, and raw
# stdout combined. quota = retryable later (usage cap / overload); auth = needs
# re-login; anything else is generic.
$QuotaRe = '(?i)(usage limit|limit reached|reset at|out of (credits|tokens)|rate.?limit|\b429\b|\b529\b|quota|overloaded|5-?hour limit|weekly limit)'
$AuthRe  = '(?i)(not logged in|unauthorized|authentication failed|invalid api key|please (run )?/?login|session expired|subscription required)'

# 2. Primary tier — Claude headless (JSON output for reliable failure detection).
$out = ''
$tier = 'claude'
$ok = $false
$failReason = 'other'   # other | quota | auth | absent
$claudeExe = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($claudeExe) {
    $errFile = [System.IO.Path]::GetTempFileName()
    $raw = ''
    $exit = -1
    Push-Location $Repo
    try {
        # --output-format json yields {is_error, result, ...}; stderr (where
        # usage-limit / auth errors land) is captured separately for diagnosis.
        $raw = (& $claudeExe -p "/$Skill" --output-format json --dangerously-skip-permissions 2>$errFile | Out-String)
        $exit = $LASTEXITCODE
    } catch {
        Write-Log "claude threw: $_"
    } finally {
        Pop-Location
    }
    $err = ''
    try { $err = (Get-Content $errFile -Raw -ErrorAction SilentlyContinue) } catch {}
    Remove-Item $errFile -Force -ErrorAction SilentlyContinue

    # Parse the JSON envelope; fall back to raw stdout for older claude builds
    # that don't support --output-format json (text-mode compatibility).
    $result = $null
    $isErr = $false
    $parsed = $false
    if ($raw.Trim()) {
        try {
            $j = $raw | ConvertFrom-Json -ErrorAction Stop
            $parsed = $true
            if ($j.PSObject.Properties.Name -contains 'result' -and $j.result) { $result = [string]$j.result }
            if ($j.PSObject.Properties.Name -contains 'is_error') { $isErr = [bool]$j.is_error }
        } catch { $parsed = $false }
    }
    if (-not $parsed) { $result = $raw }   # text-mode: raw stdout is the message

    $diag = "$result`n$err"
    $clean = $result -and $result.Trim() -and ($result -notmatch $QuotaRe) -and ($result -notmatch $AuthRe)
    if ($exit -eq 0 -and -not $isErr -and $clean) {
        $out = $result
        $ok = $true
    } else {
        if ($diag -match $AuthRe)       { $failReason = 'auth' }
        elseif ($diag -match $QuotaRe)  { $failReason = 'quota' }
        else                            { $failReason = 'other' }
        Write-Log "claude failed exit=$exit is_error=$isErr reason=$failReason - falling back"
    }
} else {
    $failReason = 'absent'
    Write-Log 'claude not found on PATH — falling back'
}

# 3. Fallback tier — free-LLM backend runner (degraded, keeps the brief flowing).
if (-not $ok) {
    $fb = (& $Py $Fallback $Skill 2>$null | Out-String)
    if ($LASTEXITCODE -eq 0 -and $fb.Trim()) {
        $out = $fb
        $tier = 'fallback-free-llm'
        $ok = $true
        Write-Log "free-LLM fallback produced output (claude reason=$failReason)"
    } else {
        Write-Log "fallback unavailable (claude reason=$failReason)"
        switch ($failReason) {
            'auth'  { $msg = "Claude not logged in / token expired and no free-LLM fallback for $Skill. Run claude login, or check Pro / mcp_login.py." }
            'quota' { $msg = "Claude usage limit reached (resets later) and no free-LLM fallback for $Skill. Set a free LLM key (GITHUB_TOKEN/GOOGLE_API_KEY/...) or wait for reset." }
            default { $msg = "Claude unavailable and no free-LLM fallback for $Skill. Check Pro login / API key, or run mcp_login.py." }
        }
        Send-Telegram $msg "aifolimizer · $Skill FAILED"
        exit 1
    }
}

# 4. Push the winning output to Telegram.
$pushTitle = $Title
if ($tier -eq 'fallback-free-llm') {
    $why = if ($failReason -eq 'other') { 'claude unavailable' } else { "claude $failReason" }
    $pushTitle = "$Title [fallback: free-LLM - $why]"
}
Send-Telegram $out $pushTitle
Write-Log "tier=$tier reason=$failReason chars=$($out.Length) pushed"
exit 0
