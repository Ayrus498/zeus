#!/usr/bin/env bash
# Dev helper: run the ZEUS backend from the repo venv (creates it on first run).
set -euo pipefail
cd "$(dirname "$0")/../backend"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt; }
exec .venv/bin/python -m app
