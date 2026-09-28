#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt -c backend/requirements.lock.txt
if [[ ! -f .env ]]; then .venv/bin/python scripts/generate_env.py; fi
if command -v npm >/dev/null 2>&1; then
  (cd frontend && if [[ -f package-lock.json ]]; then npm ci; else npm install; fi)
elif command -v pnpm >/dev/null 2>&1; then
  (cd frontend && pnpm install --frozen-lockfile)
else
  printf '%s\n' 'Install Node.js 22 LTS with npm or pnpm.' >&2
  exit 1
fi
.venv/bin/python scripts/run_local.py --migrate-only
printf '%s\n' 'Setup complete. Create accounts as described in README.md, then run bash scripts/start.sh.'

