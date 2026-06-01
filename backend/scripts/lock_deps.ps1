# PowerShell mirror of `lock_deps.sh`. See that script for the rationale.
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot/.."
python -m pip install --quiet --upgrade pip uv
python -m uv pip compile `
  --generate-hashes `
  --python-version 3.12 `
  --python-platform x86_64-unknown-linux-gnu `
  --output-file requirements.lock `
  requirements.txt
Write-Host 'Wrote backend/requirements.lock'
