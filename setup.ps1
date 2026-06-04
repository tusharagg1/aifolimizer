<#
.SYNOPSIS
  One-command setup for a fresh clone (Windows PowerShell).

    powershell -ExecutionPolicy Bypass -File setup.ps1

  Idempotent: re-running never clobbers an existing .venv, backend\.env, or
  .mcp.json. Creates the venv, installs deps, seeds backend\.env from the
  template, writes .mcp.json with absolute paths for THIS machine, registers
  the MCP server with Claude (if the CLI is present), and runs the doctor.

.PARAMETER Python
  Interpreter used to build the venv. Default: 'python'. Must be 3.12+.
#>
param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$Repo = $PSScriptRoot
Set-Location $Repo

$pyExe = (Get-Command $Python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) { Write-Error "'$Python' not found. Install Python 3.12+ or pass -Python."; exit 1 }
& $pyExe -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)'
if ($LASTEXITCODE -ne 0) { Write-Error "Python 3.12+ required (found $(& $pyExe -V)). Pass -Python with a 3.12+ interpreter."; exit 1 }

$VenvPy = Join-Path $Repo 'backend\.venv\Scripts\python.exe'

Write-Host '==> 1/5 virtualenv'
if (-not (Test-Path $VenvPy)) {
    & $pyExe -m venv backend\.venv
    Write-Host '    created backend\.venv'
} else {
    Write-Host '    backend\.venv exists — keeping'
}

Write-Host '==> 2/5 dependencies'
& $VenvPy -m pip install --upgrade pip -q
& $VenvPy -m pip install -q -r backend\requirements.txt
Write-Host '    installed backend\requirements.txt'

Write-Host '==> 3/5 backend\.env'
if (-not (Test-Path backend\.env)) {
    Copy-Item .env.example backend\.env
    Write-Host '    created backend\.env — EDIT IT and fill WS_EMAIL / WS_PASSWORD'
} else {
    Write-Host '    backend\.env exists — keeping'
}

Write-Host '==> 4/5 .mcp.json'
if (-not (Test-Path .mcp.json)) {
    # Emit via the venv python so backslash paths are JSON-escaped correctly.
    & $VenvPy -c 'import json,sys; json.dump({"mcpServers":{"aifolimizer":{"command":sys.argv[1],"args":[sys.argv[2]]}}}, open(".mcp.json","w"), indent=2)' `
        $VenvPy (Join-Path $Repo 'backend\mcp_server.py')
    Write-Host '    wrote .mcp.json'
} else {
    Write-Host '    .mcp.json exists — keeping'
}

$claudeExe = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($claudeExe) {
    Write-Host '    registering MCP server with Claude CLI'
    & $claudeExe mcp add aifolimizer $VenvPy (Join-Path $Repo 'backend\mcp_server.py') *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host '    registered (restart Claude to pick it up)' }
    else { Write-Host '    already registered or registration skipped' }
} else {
    Write-Host '    claude CLI not on PATH — register manually:'
    Write-Host "      claude mcp add aifolimizer `"$VenvPy`" `"$(Join-Path $Repo 'backend\mcp_server.py')`""
}

Write-Host '==> 5/5 doctor'
& $VenvPy backend\scripts\health_check.py

Write-Host ''
Write-Host 'Setup done. Next:'
Write-Host '  1. Edit backend\.env  (WS_EMAIL / WS_PASSWORD — optional, only for portfolio skills)'
Write-Host '  2. cd backend; .venv\Scripts\python.exe mcp_login.py   # first Wealthsimple login (MFA)'
Write-Host '  3. .venv\Scripts\python.exe run.py                      # start backend on :8000'
Write-Host '  4. Restart Claude, then ask "get my profile" or run /daily-briefing'
