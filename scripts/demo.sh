#!/usr/bin/env bash
# generate synthetic data -> ingest -> train -> analyse. Usage: ./scripts/demo.sh [--quick]
set -euo pipefail
cd "$(dirname "$0")/../backend"
python -m app.cli all "$@"
