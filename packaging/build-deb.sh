#!/usr/bin/env bash
# Build the Zeus-Agent .deb for the version stored in the VERSION file. Run from anywhere.
#
#   packaging/build-deb.sh                     build the stored version (dist/zeus_<version>_amd64.deb)
#   packaging/build-deb.sh --tag subrelease    x.y.z -> x.y.(z+1), then build it
#   packaging/build-deb.sh --tag mainrelease   x.y.z -> x.(y+1).0, then build it
#
# VERSION is the only place a version is decided (see packaging/version.py): --tag bumps it, writes it into every file that carries
# it (app, Cargo, Tauri, npm), and from then on every build produces exactly that version. dist/ ends up with only that .deb (plus its
# .sha256 and RELEASE.json); debs of other versions are moved to dist/archive/ and are never produced again. If a build with --tag fails,
# the previous version is restored. The git tag (for example v02_03) is printed at the end; creating and pushing it is yours to do.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }
TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) [ $# -ge 2 ] || { echo "--tag needs mainrelease or subrelease" >&2; exit 2; }; TAG="$2"; shift 2 ;;
    --tag=*) TAG="${1#--tag=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$TAG" in ""|mainrelease|subrelease) ;; *) echo "unknown release tag '$TAG': use mainrelease or subrelease" >&2; exit 2 ;; esac
command -v python3 >/dev/null || { echo "missing tool: python3" >&2; exit 1; }

# --tag: bump the stored version first (everything below, tests included, then runs on the new version), and undo it if the build fails.
BACKUP=""
if [ -n "$TAG" ]; then
  BACKUP="$(mktemp -d)"
  while read -r f; do mkdir -p "$BACKUP/$(dirname "$f")"; cp -p "$f" "$BACKUP/$f"; done < <(python3 packaging/version.py files)
  restore_version() {
    rc=$?
    if [ "$rc" -ne 0 ] && [ -n "$BACKUP" ]; then
      while read -r f; do cp -p "$BACKUP/$f" "$f"; done < <(python3 packaging/version.py files)
      echo "Build failed: version restored to $(cat VERSION)." >&2
    fi
    [ -n "$BACKUP" ] && rm -rf "$BACKUP"
    exit "$rc"
  }
  trap restore_version EXIT
  python3 packaging/version.py bump "$TAG"
fi

# Every build: all version files must agree with VERSION, so the package, the app and the release notes cannot drift apart.
python3 packaging/version.py check
VERSION="$(tr -d '[:space:]' < VERSION)"
DEB="zeus_${VERSION}_amd64.deb"
RELEASE_TAG="$(python3 -c "import sys; sys.path.insert(0, 'packaging'); import version; print(version.tag_name('$VERSION'))")"
echo "Building Zeus-Agent $VERSION ($RELEASE_TAG, $(python3 -c "import sys; sys.path.insert(0, 'packaging'); import version; print(version.kind_of('$VERSION'))")) -> dist/$DEB"
grep -q "^## $VERSION\b" RELEASE_NOTES.md || echo "warning: RELEASE_NOTES.md has no section for $VERSION" >&2
grep -q "^## $VERSION: UNRELEASED" RELEASE_NOTES.md && echo "warning: the $VERSION release notes still say UNRELEASED: write them before publishing" >&2

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

# End-to-end tests of the real GUI (Chromium) and the real terminal app (pty) against fake model engines. See scripts/e2e/run.py.
# They gate the package: a failure stops the build. ZEUS_E2E=skip bypasses them (loudly) on a machine that cannot run a browser.
if [ -x backend/.venv/bin/python ]; then
  echo "Running end-to-end tests (GUI + CLI)..."
  (cd frontend && npm ci --silent)
  backend/.venv/bin/python scripts/e2e/run.py --out "$ROOT/dist/e2e-report" || { rc=$?; [ "$rc" -eq 2 ] && echo "End-to-end suite could not run (exit 2): install Playwright (backend/.venv/bin/pip install -r backend/requirements-dev.txt; backend/.venv/bin/playwright install chromium) or set ZEUS_E2E=skip" >&2; exit "$rc"; }
fi

# Never ship developer bytecode caches inside the package.
find backend/app -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "Preparing offline Python wheelhouse..."
packaging/make-wheelhouse.sh

echo "Pre-fetching Rust crates (first run can take several minutes on a slow network)..."
cargo fetch --manifest-path src-tauri/Cargo.toml

(cd frontend && npm ci && npm run build)
# Build artefacts of earlier versions must not be mistaken for this build's output.
BUNDLE=src-tauri/target/release/bundle/deb
rm -f "$BUNDLE"/*.deb
frontend/node_modules/.bin/tauri build --bundles deb

[ -f "$BUNDLE/$DEB" ] || { echo "expected $BUNDLE/$DEB was not produced (the packaged version does not match VERSION $VERSION)" >&2; exit 1; }
BUILT="$(dpkg-deb -f "$BUNDLE/$DEB" Version)"
[ "$BUILT" = "$VERSION" ] || { echo "the package says version $BUILT but VERSION is $VERSION" >&2; exit 1; }

# Only this version stays in dist/: other versions, and an earlier build of this same version, are moved to dist/archive/ (nothing is overwritten or deleted).
python3 packaging/publish.py "$VERSION" "$BUNDLE/$DEB"
ls -la "dist/$DEB" "dist/$DEB.sha256"
echo "Released: $VERSION ($RELEASE_TAG)  sha256 $(cut -d' ' -f1 "dist/$DEB.sha256")"
echo "Next: commit, then  git tag $RELEASE_TAG && git push origin $RELEASE_TAG   (not done for you)"
