$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\backend")
python -m app.report
