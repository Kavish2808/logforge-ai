#!/usr/bin/env bash
# Reproducible LogForge AI Demo Mode (unknown vendor -> v1 -> drift -> learning -> v2 -> rollback).
# Drives the running stack through its public API only; every run starts with
# a reset scoped to demo-owned rows, so it is safe to repeat.
#
#   scripts/demo_mode.sh            run all 23 stages (human decisions scripted as demo-operator)
#   scripts/demo_mode.sh --pause    stop for Enter at every human decision
#   scripts/demo_mode.sh --status   show demo progress
#   scripts/demo_mode.sh --reset    remove demo-owned state only
#
# Requires the stack: docker compose up --build
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ " $* " == *" --pause "* ]]; then
  exec docker compose exec backend python -m app.demo "$@"
fi
exec docker compose exec -T backend python -m app.demo "$@"
