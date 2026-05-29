<#
.SYNOPSIS
  Register (or replace) a Windows Scheduled Task that runs an aifolimizer skill
  via run-claude-skill.ps1 on a schedule. One task per skill.

.PARAMETER Skill   Skill name (daily-briefing, top-trades-today, position-review, ...).
.PARAMETER Time    Local start time, 24h "HH:mm" (e.g. 07:00).
.PARAMETER Days    DAILY, or comma list of weekday short names (MON,TUE,WED,THU,FRI).
.PARAMETER RunMinutesLimit  Kill the run if it exceeds this (default 15).

.EXAMPLE
  scripts\register-skill-task.ps1 -Skill daily-briefing -Time 07:00 -Days MON,TUE,WED,THU,FRI
  scripts\register-skill-task.ps1 -Skill top-trades-today -Time 08:00 -Days MON,TUE,WED,THU,FRI
  scripts\register-skill-task.ps1 -Skill position-review -Time 18:30 -Days DAILY

.NOTES
  Task runs only while you are logged on (no stored password needed) - matches a
  PC that stays on + logged in. To run when logged off, re-create with
  -User/-Password (see AUTOMATION.md). StartWhenAvailable catches missed runs.
#>
param(
    [Parameter(Mandatory = $true)][string]$Skill,
    [Parameter(Mandatory = $true)][string]$Time,
    [string[]]$Days = @('MON','TUE','WED','THU','FRI'),
    [int]$RunMinutesLimit = 15
)

$Repo    = 'C:\Users\Tusha\Documents\projects\aifolimizer'
$Wrapper = Join-Path $Repo 'scripts\run-claude-skill.ps1'
$TaskName = "aifolimizer\$Skill"

if (-not (Test-Path $Wrapper)) { throw "wrapper not found: $Wrapper" }

$argLine = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Skill {1}' -f $Wrapper, $Skill
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argLine

$DaysStr = ($Days -join ',').ToUpper()
if ($DaysStr -eq 'DAILY') {
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
} else {
    $map = @{ MON='Monday'; TUE='Tuesday'; WED='Wednesday'; THU='Thursday';
              FRI='Friday'; SAT='Saturday'; SUN='Sunday' }
    $dow = $Days | ForEach-Object { $map[$_.Trim().ToUpper()] }
    if (-not $dow) { throw "could not parse -Days '$Days'" }
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dow -At $Time
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $RunMinutesLimit) `
    -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "aifolimizer $Skill via Claude (free-LLM fallback)" `
    -Force | Out-Null

Write-Host "Registered task '$TaskName' - $DaysStr at $Time (limit ${RunMinutesLimit}m)."
Write-Host "Test now:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove:    Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
