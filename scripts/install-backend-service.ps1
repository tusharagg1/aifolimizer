<#
.SYNOPSIS
  Install the aifolimizer FastAPI backend as a Windows service via NSSM, so it
  auto-starts at boot and auto-restarts on crash. The running backend keeps the
  Wealthsimple session warm (scheduler ticks refresh the token), which is what
  lets the headless Claude skill runs reach live portfolio data without
  re-entering MFA for days/weeks.

  Run this in an ELEVATED PowerShell (services require admin).

.NOTES
  NSSM required. Install with: choco install nssm   (or download nssm.exe and
  put it on PATH). The backend binds 127.0.0.1 only (local; WS creds never leave
  the machine, per the privacy rules).
#>
param(
    [string]$ServiceName = 'aifolimizer-backend',
    [int]$Port = 8000
)

$Repo    = 'C:\Users\Tusha\Documents\projects\aifolimizer'
$Backend = Join-Path $Repo 'backend'
$Py      = Join-Path $Backend '.venv\Scripts\python.exe'
$LogDir  = Join-Path $HOME '.aifolimizer\logs'

$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) {
    throw "nssm not found. Install it (choco install nssm) or put nssm.exe on PATH, then re-run."
}
if (-not (Test-Path $Py)) { throw "venv python not found: $Py" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Idempotent: remove any prior install first.
& $nssm stop $ServiceName 2>$null | Out-Null
& $nssm remove $ServiceName confirm 2>$null | Out-Null

& $nssm install $ServiceName $Py "-m uvicorn main:app --host 127.0.0.1 --port $Port"
& $nssm set $ServiceName AppDirectory $Backend
& $nssm set $ServiceName AppStdout (Join-Path $LogDir 'backend.out.log')
& $nssm set $ServiceName AppStderr (Join-Path $LogDir 'backend.err.log')
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppRestartDelay 5000
& $nssm start $ServiceName

Write-Host "Service '$ServiceName' installed + started on 127.0.0.1:$Port."
Write-Host "Health:  curl http://127.0.0.1:$Port/health"
Write-Host "Manage:  nssm stop/start/restart/edit $ServiceName   |   logs: $LogDir"
