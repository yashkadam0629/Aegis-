$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python benchmarks/scripts/run_benchmarks.py @args
