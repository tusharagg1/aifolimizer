<#
.SYNOPSIS
  Watchdog for the aifolimizer backend. Checks port 8000 every invocation;
  restarts backend if not responding. Intended to run every 5 min via Task Scheduler.

.NOTES
  Task Scheduler action: powershell.exe -NoProfile -ExecutionPolicy Bypass -File
    "C:\Users\Tusha\Documents\projects\aifolimizer\scripts\backend-watchdog.ps1"
  Trigger: repeat every 5 minutes indefinitely.
#>

$Backend  = 'C:\Users\Tusha\Documents\projects\aifolimizer\backend'
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

Write-Log 'backend not responding — restarting'

# Kill any stale uvicorn/python on 8000 first.
$pid8000 = (netstat -ano | Select-String ':8000 .*LISTENING' | ForEach-Object {
    ($_ -split '\s+')[-1]
}) | Select-Object -First 1
if ($pid8000) {
    Stop-Process -Id ([int]$pid8000) -Force -ErrorAction SilentlyContinue
    Write-Log "killed stale pid $pid8000"
}

# Restart backend.
Start-Process -FilePath $Py -ArgumentList $Run `
    -WorkingDirectory $Backend -WindowStyle Hidden
Write-Log 'backend process started'
