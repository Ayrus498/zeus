#!/usr/bin/env bash
# Download the backend's Python dependencies as wheels into packaging/wheelhouse so the .deb can install
# them offline (no PyPI access at install time). Re-runs only when requirements or the Python version change.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/packaging/wheelhouse"
REQ="$ROOT/backend/requirements.txt"
TAG="$(python3 -c 'import sys,platform;print(f"cp{sys.version_info.major}{sys.version_info.minor}-{platform.machine()}")')"
STAMP="$(sha256sum "$REQ" | cut -d' ' -f1)-$TAG"

if [ -f "$OUT/.stamp" ] && [ "$(cat "$OUT/.stamp")" = "$STAMP" ] && ls "$OUT"/*.whl >/dev/null 2>&1; then
  echo "wheelhouse up to date ($TAG, $(ls "$OUT"/*.whl | wc -l) wheels)"; exit 0
fi
rm -rf "$OUT"; mkdir -p "$OUT"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
python3 -m venv "$TMP/v"
echo "Downloading wheels for $TAG (needs PyPI once, at build time)..."
"$TMP/v/bin/pip" download --quiet --disable-pip-version-check --only-binary=:all: \
  --timeout "${PIP_TIMEOUT:-120}" --retries "${PIP_RETRIES:-10}" -r "$REQ" -d "$OUT"
# Make sure the set really installs offline into a clean venv before we ship it.
"$TMP/v/bin/pip" install --quiet --disable-pip-version-check --no-index --find-links "$OUT" -r "$REQ"
echo "$STAMP" > "$OUT/.stamp"
echo "wheelhouse ready: $(ls "$OUT"/*.whl | wc -l) wheels, $(du -sh "$OUT" | cut -f1)"
