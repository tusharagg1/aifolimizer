# Register / unregister a Windows Scheduled Task that runs run_alerts.py
# every 30 min during NY market hours (9:30 AM - 4:00 PM ET, Mon-Fri).
#
# Usage:
#   .\schedule_alerts.ps1                  # register
#   .\schedule_alerts.ps1 -Unregister      # remove
#   .\schedule_alerts.ps1 -DryRun          # register but pass --dry-run to script
#
# Requires running as the local user (no admin needed for user tasks).

param(
  [switch]$Unregister,
  [switch]$DryRun,
  [string]$TaskName = "aifolimizer-alerts"
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed task: $TaskName"
  } else {
    Write-Host "Task not found: $TaskName"
  }
  return
}

$BackendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonExe  = Join-Path $BackendDir ".venv\Scripts\python.exe"
$Script     = Join-Path $BackendDir "scripts\run_alerts.py"

if (-not (Test-Path $PythonExe)) { throw "Python venv not found at $PythonExe" }
if (-not (Test-Path $Script))    { throw "run_alerts.py not found at $Script" }

$argList = "`"$Script`""
if ($DryRun) { $argList = "$argList --dry-run" }

$action = New-ScheduledTaskAction `
  -Execute $PythonExe `
  -Argument $argList `
  -WorkingDirectory $BackendDir

# Every 30 min, indefinitely. Omitting -RepetitionDuration repeats forever
# (the old -RepetitionDuration 6h30m made the task fire one day then stop, and
# setting .DaysOfWeek on a -Once trigger is a no-op — that combo never recurred).
# Off-hours/weekend runs are cheap: no new price data => alerts dedup to no-op.
$trigger = New-ScheduledTaskTrigger `
  -Once -At 9:30am `
  -RepetitionInterval (New-TimeSpan -Minutes 30)

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -DontStopOnIdleEnd `
  -RestartCount 2 `
  -RestartInterval (New-TimeSpan -Minutes 5) `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "aifolimizer portfolio alerts - runs every 30 min, indefinitely (off-hours dedup to no-op)" `
  -Force

Write-Host "Registered task: $TaskName"
Write-Host "  Python:  $PythonExe"
Write-Host "  Script:  $Script"
Write-Host "  Dry run: $($DryRun.IsPresent)"
Write-Host ""
Write-Host ("Verify in Task Scheduler GUI or via: Get-ScheduledTask -TaskName " + $TaskName)
Write-Host ('Remove with: schedule_alerts.ps1 -Unregister')
