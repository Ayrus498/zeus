#!/usr/bin/env bash
# Build the ZEUS .deb. Run from anywhere. Output: dist/zeus_<version>_amd64.deb
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Tolerate slow links to crates.io (its `windows` index file is ~300 KB and can crawl).
export CARGO_HTTP_TIMEOUT="${CARGO_HTTP_TIMEOUT:-600}"
export CARGO_HTTP_LOW_SPEED_LIMIT="${CARGO_HTTP_LOW_SPEED_LIMIT:-1}"
export CARGO_NET_RETRY="${CARGO_NET_RETRY:-10}"

need() { command -v "$1" >/dev/null || { echo "missing tool: $1" >&2; exit 1; }; }
need node; need npm; need cargo; need python3

# System libraries required by Tauri 2 on Ubuntu/Debian.
MISSING=""
for p in libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev libssl-dev patchelf build-essential pkg-config; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
  echo "Missing build dependencies:$MISSING" >&2
  echo "Install with:  sudo apt install$MISSING" >&2
  exit 1
fi

# Backend tests must pass before packaging.
if [ -x backend/.venv/bin/python ]; then
  (cd backend && .venv/bin/python -m pytest -q)
fi

# Never ship developer bytecode caches inside the package.
find backend/app -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "Preparing offline Python wheelhouse..."
packaging/make-wheelhouse.sh

echo "Pre-fetching Rust crates (first run can take several minutes on a slow network)..."
cargo fetch --manifest-path src-tauri/Cargo.toml

(cd frontend && npm ci && npm run build)
frontend/node_modules/.bin/tauri build --bundles deb

mkdir -p dist
cp src-tauri/target/release/bundle/deb/*.deb dist/
ls -la dist/*.deb
