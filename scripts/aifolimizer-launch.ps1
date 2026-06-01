<#
.SYNOPSIS
  User-facing launcher. Run this when you sit at your PC and want
  aifolimizer ready (skills, dashboard, Claude Code).

  Sequence:
    1. Ensure backend is up (curl :8000/health).
    2. Probe WS session via POST /ws/restore.
    3. If session expired, spawn local Tk popup (mfa_popup.py) for MFA.
    4. Re-probe; report ready / failed.

.NOTES
  Pin a desktop shortcut to this script. No arguments. No Telegram
  interaction at this layer — Telegram heads-up is sent by the
  background watchdog only.
#>

$Repo    = if ($env:AIFOLIMIZER_ROOT) { $env:AIFOLIMIZER_ROOT } else { Split-Path -Parent $PSScriptRoot }
$Backend = Join-Path $Repo 'backend'
$Py      = Join-Path $Backend '.venv\Scripts\python.exe'
$Popup   = Join-Path $Backend 'scripts\mfa_popup.py'
$LogDir  = Join-Path $HOME '.aifolimizer'
$Log     = Join-Path $LogDir 'launch.log'
$Stamp   = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Write-Log($msg) { Add-Content -Path $Log -Value "$Stamp [launch] $msg" }

function Test-Backend {
    try {
        $r = Invoke-RestMethod -Uri 'http://localhost:8000/health' `
            -TimeoutSec 5
        return ($r.status -eq 'ok')
    } catch { return $false }
}

function Test-Session {
    try {
        $r = Invoke-RestMethod -Uri 'http://localhost:8000/ws/restore' `
            -Method Post -TimeoutSec 10
        return [bool]$r.restored
    } catch { return $false }
}

Write-Host 'aifolimizer · launcher'
Write-Host '======================'

if (-not (Test-Backend)) {
    Write-Host 'Backend not responding. Start it via watchdog or run.py.' `
        -ForegroundColor Yellow
    Write-Log 'backend down at launch'
    exit 1
}
Write-Host 'Backend: OK'

if (Test-Session) {
    Write-Host 'WS session: OK (no MFA needed).' -ForegroundColor Green
    Write-Log 'session healthy — ready'
    exit 0
}

Write-Host 'WS session: expired. Opening MFA popup...' -ForegroundColor Yellow
Write-Log 'session expired — spawning popup'
$proc = Start-Process -FilePath $Py -ArgumentList $Popup `
    -WorkingDirectory $Backend -Wait -PassThru -NoNewWindow
Write-Log "mfa_popup exit=$($proc.ExitCode)"

if ($proc.ExitCode -ne 0) {
    Write-Host "MFA popup exited $($proc.ExitCode). Re-run launcher." `
        -ForegroundColor Red
    exit $proc.ExitCode
}

if (Test-Session) {
    Write-Host 'WS session: refreshed. Skills ready.' -ForegroundColor Green
    Write-Log 'session refreshed via popup — ready'
    exit 0
}

Write-Host 'Session probe still failing post-popup. Check logs.' `
    -ForegroundColor Red
Write-Log 'post-popup probe failed'
exit 1
