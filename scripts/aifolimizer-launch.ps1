<#
.SYNOPSIS
  WS session / MFA refresh launcher. Run when you sit at your PC and the
  aifolimizer skills report "session expired / re-auth needed".

  Sequence:
    1. Probe the cached WS session directly via the backend service layer
       (app.services.wealthsimple.restore_session) - no :8000 server needed.
    2. If the session is still valid, report ready and exit.
    3. If expired/missing, run mcp_login.py interactively so you can enter
       the 6-digit MFA code. The refreshed token is cached to
       ~/.aifolimizer/ws_session.json, which the MCP server reloads per call.

.NOTES
  Pin a desktop shortcut to this script. No arguments. This is the ONLY
  step needed to recover from a forced-MFA / expired-session state - the
  MCP server (spawned on demand by Claude) picks up the new token
  automatically. There is no long-running FastAPI backend in this model.
#>

$Repo    = if ($env:AIFOLIMIZER_ROOT) { $env:AIFOLIMIZER_ROOT } else { Split-Path -Parent $PSScriptRoot }
$Backend = Join-Path $Repo 'backend'
$Py      = Join-Path $Backend '.venv\Scripts\python.exe'
$Login   = Join-Path $Backend 'mcp_login.py'
$LogDir  = Join-Path $HOME '.aifolimizer'
$Log     = Join-Path $LogDir 'launch.log'
$Stamp   = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Write-Log($msg) { Add-Content -Path $Log -Value "$Stamp [launch] $msg" }

if (-not (Test-Path $Py))    { Write-Host "venv python not found: $Py" -ForegroundColor Red; Write-Log 'venv python missing'; exit 1 }
if (-not (Test-Path $Login)) { Write-Host "mcp_login.py not found: $Login" -ForegroundColor Red; Write-Log 'mcp_login.py missing'; exit 1 }

# Probe the cached session without standing up a server. restore_session()
# returns a token string on success, None on expiry. The requests-timeout
# patch in wealthsimple.py caps any Cloudflare hang, so this cannot block
# indefinitely.
function Test-Session {
    Push-Location $Backend
    try {
        & $Py -c "import sys; from app.services import wealthsimple as w; sys.exit(0 if w.restore_session() else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        Pop-Location
    }
}

Write-Host 'aifolimizer | session launcher'
Write-Host '=============================='

if (Test-Session) {
    Write-Host 'WS session: OK (no MFA needed).' -ForegroundColor Green
    Write-Log 'session healthy - ready'
    exit 0
}

Write-Host 'WS session: expired/missing. Starting interactive MFA login...' -ForegroundColor Yellow
Write-Host '(Enter your 6-digit code from email or authenticator when prompted.)'
Write-Log 'session expired - launching mcp_login.py'

Push-Location $Backend
try {
    & $Py $Login
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
Write-Log "mcp_login exit=$code"

if ($code -ne 0) {
    Write-Host "Login exited $code. If Cloudflare rate-limited (1015), wait 15-60 min and retry ONCE." -ForegroundColor Red
    exit $code
}

if (Test-Session) {
    Write-Host 'WS session: refreshed. Skills ready.' -ForegroundColor Green
    Write-Log 'session refreshed via login - ready'
    exit 0
}

Write-Host 'Session probe still failing after login. Check ~/.aifolimizer/launch.log.' -ForegroundColor Red
Write-Log 'post-login probe failed'
exit 1
