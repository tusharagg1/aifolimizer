# PowerShell mirror of `lock_deps.sh`. See that script for the rationale.
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot/.."
python -m pip install --quiet --upgrade pip pip-tools
pip-compile `
  --generate-hashes `
  --resolver=backtracking `
  --output-file=requirements.lock `
  requirements.txt
Write-Host 'Wrote backend/requirements.lock'
