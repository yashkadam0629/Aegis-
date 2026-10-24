#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python benchmarks/scripts/run_benchmarks.py "$@"
