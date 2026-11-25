#!/usr/bin/env python3
"""Put a freshly built .deb into dist/: only the stored version stays there, and nothing is ever overwritten or deleted.

    packaging/publish.py <version> <path-to-the-built.deb>

* Every other version's package (and its .sha256) moves to dist/archive/.
* A package of the SAME version that already sits in dist/ and differs from the new build moves to dist/archive/ as zeus_<version>_amd64.<sha12>.deb
  (a rebuild must not destroy the file you may already have installed or shared).
* The new package is copied in, with <name>.sha256 (checkable with `cd dist && sha256sum -c`) and RELEASE.json (version, git tag name, kind, checksum, commit).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import version as ver  # noqa: E402


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _archive(root: Path, f: Path, suffix: str = "") -> Path:
    arch = root / "dist" / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stem = f.name[: -len(".deb")]
    target = arch / (f"{stem}.{suffix}.deb" if suffix else f.name)
    if target.exists():                                   # never replace something already archived
        target = arch / f"{stem}.{sha256(f)[:12]}.deb"
        n = 1
        while target.exists():
            target = arch / f"{stem}.{sha256(f)[:12]}-{n}.deb"
            n += 1
    shutil.move(str(f), str(target))
    side = f.with_name(f.name + ".sha256")
    if side.exists():
        shutil.move(str(side), str(target.with_name(target.name + ".sha256")))
    return target


def publish(root: Path, version: str, built: Path) -> dict:
    ver.parse(version)
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    name = f"zeus_{version}_amd64.deb"
    if built.name != name:
        raise SystemExit(f"the built package is {built.name}, expected {name}")
    moved = []
    for f in sorted(dist.glob("zeus_*_amd64.deb")):
        if f.name != name:
            moved.append(str(_archive(root, f).relative_to(root)))
    cur = dist / name
    new_sha = sha256(built)
    if cur.exists() and sha256(cur) != new_sha:
        moved.append(str(_archive(root, cur, suffix=sha256(cur)[:12]).relative_to(root)))
    shutil.copy2(built, cur)
    (dist / f"{name}.sha256").write_text(f"{new_sha}  {name}\n")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip() or None
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True).stdout.strip()) if commit else None
    except OSError:
        commit, dirty = None, None
    manifest = {"version": version, "tag": ver.tag_name(version), "kind": ver.kind_of(version), "deb": name, "sha256": new_sha,
                "built": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": commit, "uncommitted_changes": dirty}
    (dist / "RELEASE.json").write_text(json.dumps(manifest, indent=1))
    return {"manifest": manifest, "archived": moved}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    r = publish(Path(__file__).resolve().parents[1], sys.argv[1], Path(sys.argv[2]))
    for a in r["archived"]:
        print(f"archived: {a}")
    print(f"published: dist/{r['manifest']['deb']}  sha256 {r['manifest']['sha256']}  tag {r['manifest']['tag']}")
