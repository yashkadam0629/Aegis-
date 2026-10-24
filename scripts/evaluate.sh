#!/usr/bin/env bash
# Business-impact benchmark + evaluation report (needs a trained model and pipeline run). Usage: ./scripts/evaluate.sh
set -euo pipefail
cd "$(dirname "$0")/../backend"
python -m app.report
