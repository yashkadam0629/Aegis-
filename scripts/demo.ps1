# generate synthetic data -> ingest -> train -> analyse.  Usage: .\scripts\demo.ps1 [--quick]
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\backend")
python -m app.cli all @args
