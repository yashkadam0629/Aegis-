#!/usr/bin/env bash
# One-time setup: Python deps + frontend deps. Usage: ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install -e "backend[dev]"
(cd frontend && npm install --no-audit --no-fund)
[ -f .env ] || cp .env.example .env
echo "Setup complete. Next: ./scripts/demo.sh"
