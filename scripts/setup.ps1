# One-time setup (Windows PowerShell)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m pip install -e "backend[dev]"
Push-Location frontend; npm install --no-audit --no-fund; Pop-Location
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "Setup complete. Next: .\scripts\demo.ps1"
