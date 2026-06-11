<#
.SYNOPSIS
  Watchdog for the aifolimizer backend. Checks port 8000 every invocation;
  restarts backend if not responding. Intended to run every 5 min via Task Scheduler.

.NOTES
  Task Scheduler action: powershell.exe -NoProfile -ExecutionPolicy Bypass -File
    "<REPO>\scripts\backend-watchdog.ps1"
  Trigger: repeat every 5 minutes indefinitely.
  Set $env:AIFOLIMIZER_ROOT to override repo location; otherwise auto-detected from script path.
#>

$Repo     = if ($env:AIFOLIMIZER_ROOT) { $env:AIFOLIMIZER_ROOT } else { Split-Path -Parent $PSScriptRoot }
$Backend  = Join-Path $Repo 'backend'
$Py       = Join-Path $Backend '.venv\Scripts\python.exe'
$Run      = Join-Path $Backend 'run.py'
$LogDir   = Join-Path $HOME '.aifolimizer'
$Log      = Join-Path $LogDir 'watchdog.log'
$Stamp    = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
function Write-Log($msg) { Add-Content -Path $Log -Value "$Stamp [watchdog] $msg" }

# Check if backend is alive.
$alive = $false
try {
    $resp = Invoke-RestMethod -Uri 'http://localhost:8000/health' -TimeoutSec 5
    if ($resp.status -eq 'ok') { $alive = $true }
} catch {}

if ($alive) { exit 0 }

Write-Log 'backend not responding - restarting'

# Kill ALL stale listeners on 8000 (uvicorn binds both IPv4 and IPv6 → two
# LISTENING rows). Killing only the first PID leaves the other stack holding
# the port, and the restart below fails with 'address already in use'.
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen `
    -ErrorAction SilentlyContinue
foreach ($conn in $listeners) {
    $stalePid = $conn.OwningProcess
    if ($stalePid) {
        Stop-Process -Id ([int]$stalePid) -Force -ErrorAction SilentlyContinue
        Write-Log "killed stale pid $stalePid (local $($conn.LocalAddress):8000)"
    }
}

# Restart backend.
Start-Process -FilePath $Py -ArgumentList $Run `
    -WorkingDirectory $Backend -WindowStyle Hidden
Write-Log 'backend process started'
