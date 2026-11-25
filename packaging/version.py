#!/usr/bin/env python3
"""Zeus-Agent's one version number.

`VERSION` (repository root) is the only place a release version is decided. Everything that carries it is written from there:

    backend/app/core/config.py          VERSION = "x.y.z"            (what the app reports; also in session and state files)
    src-tauri/Cargo.toml           [package] version            (and the zeus entry of Cargo.lock)
    src-tauri/tauri.conf.json      "version"                    (names the .deb)
    frontend/package.json          "version"                    (and package-lock.json)

    packaging/version.py show              print the version, the next ones and the release tag
    packaging/version.py check             exit 1 when any file disagrees with VERSION
    packaging/version.py sync              write VERSION into every file
    packaging/version.py bump mainrelease  x.y.z -> x.(y+1).0   then sync
    packaging/version.py bump subrelease   x.y.z -> x.y.(z+1)   then sync
    packaging/version.py set x.y.z         use exactly this version (also to go back), then sync
    packaging/version.py files             list the files that carry the version (one per line)

Release tag names follow the existing tags: 0.2.1 -> v02_01 (0.x versions: minor and patch, two digits each); 1.2.3 -> v1_02_03.
A version whose patch is 0 is a main release, any other a sub release. Standard library only; text edits keep each file's formatting.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
KINDS = ("mainrelease", "subrelease")


class VersionError(Exception):
    pass


def parse(v: str) -> tuple[int, int, int]:
    m = SEMVER.match((v or "").strip())
    if not m:
        raise VersionError(f"'{v}' is not a version like 1.2.3")
    return int(m[1]), int(m[2]), int(m[3])


def bump(v: str, kind: str) -> str:
    major, minor, patch = parse(v)
    if kind == "mainrelease":
        return f"{major}.{minor + 1}.0"
    if kind == "subrelease":
        return f"{major}.{minor}.{patch + 1}"
    raise VersionError(f"unknown release tag '{kind}': use one of {', '.join(KINDS)}")


def kind_of(v: str) -> str:
    return "mainrelease" if parse(v)[2] == 0 else "subrelease"


def tag_name(v: str) -> str:
    major, minor, patch = parse(v)
    return f"v{minor:02d}_{patch:02d}" if major == 0 else f"v{major}_{minor:02d}_{patch:02d}"


# ------------------------------------------------------------------------------------------------ the files that carry the version
def _sub_first(text: str, pattern: str, new: str, flags: int = 0) -> str:
    out, n = re.subn(pattern, lambda m: m.group(1) + new + m.group(2), text, count=1, flags=flags)
    if n != 1:
        raise VersionError(f"pattern not found: {pattern}")
    return out


def _find(text: str, pattern: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(2) if m else None


# (relative path, regex with group 1 = text before the version, group 2 = the version... group 3 handled by lambda) -> use (prefix)(version)(suffix)
SPECS = {
    "backend/app/core/config.py": [r'(^VERSION = ")(\d+\.\d+\.\d+)(")'],
    "src-tauri/Cargo.toml": [r'(\[package\][^\[]*?^version = ")(\d+\.\d+\.\d+)(")'],
    "src-tauri/Cargo.lock": [r'(\[\[package\]\]\nname = "zeus"\nversion = ")(\d+\.\d+\.\d+)(")'],
    "src-tauri/tauri.conf.json": [r'(^\s*"version":\s*")(\d+\.\d+\.\d+)(")'],
    "frontend/package.json": [r'(^\s*"version":\s*")(\d+\.\d+\.\d+)(")'],
    "frontend/package-lock.json": [r'(\A\{[^{]*?"version":\s*")(\d+\.\d+\.\d+)(")', r'("packages":\s*\{\s*"":\s*\{[^{}]*?"version":\s*")(\d+\.\d+\.\d+)(")'],
}
FLAGS = re.M | re.S


def files() -> list[str]:
    return list(SPECS)


def read_versions(root: Path = ROOT) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rel, pats in SPECS.items():
        text = (root / rel).read_text("utf-8")
        found = []
        for p in pats:
            m = re.search(p, text, FLAGS)
            found.append(m.group(2) if m else "(not found)")
        out[rel] = found
    return out


def stored(root: Path = ROOT) -> str:
    v = (root / "VERSION").read_text("utf-8").strip()
    parse(v)
    return v


def check(root: Path = ROOT) -> list[str]:
    """Human-readable problems; empty when every file carries the stored version."""
    want = stored(root)
    return [f"{rel}: {v} (VERSION says {want})" for rel, vs in read_versions(root).items() for v in vs if v != want]


def sync(root: Path = ROOT, version: str | None = None) -> list[str]:
    """Write the version into every file; returns the files that changed. All files are read and edited in memory first, so a pattern that
    no longer matches stops everything before any file is touched."""
    v = version or stored(root)
    parse(v)
    edits: dict[str, str] = {}
    for rel, pats in SPECS.items():
        text = (root / rel).read_text("utf-8")
        new = text
        for p in pats:
            new = re.sub(p, lambda m: m.group(1) + v + m.group(3), new, count=1, flags=FLAGS) if re.search(p, new, FLAGS) else _missing(rel, p)
        if new != text:
            edits[rel] = new
    for rel, new in edits.items():
        (root / rel).write_text(new, "utf-8")
    if (root / "VERSION").read_text("utf-8").strip() != v:
        (root / "VERSION").write_text(v + "\n", "utf-8")
    json.loads((root / "frontend/package.json").read_text("utf-8"))           # the JSON files must still parse
    json.loads((root / "src-tauri/tauri.conf.json").read_text("utf-8"))
    json.loads((root / "frontend/package-lock.json").read_text("utf-8"))
    return sorted(edits)


def _missing(rel: str, pattern: str):
    raise VersionError(f"{rel}: could not find the version to update (the file layout changed; update packaging/version.py)")


def notes_stub(root: Path, v: str) -> bool:
    """Make sure RELEASE_NOTES.md has a section for the version (a heading to fill in). Returns True when one was added."""
    p = root / "RELEASE_NOTES.md"
    if not p.exists():
        return False
    text = p.read_text("utf-8")
    if re.search(rf"^## {re.escape(v)}\b", text, re.M):
        return False
    head = f"## {v}: UNRELEASED, write the notes before publishing ({date.today().isoformat()})\n\n### New\n- \n\n"
    marker = text.find("\n## ")
    p.write_text(text[: marker + 1] + head + text[marker + 1:] if marker != -1 else text + "\n" + head, "utf-8")
    return True


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "show"
    try:
        if cmd == "show":
            v = stored()
            print(f"version {v}  ({kind_of(v)})  tag {tag_name(v)}  deb zeus_{v}_amd64.deb")
            print(f"next mainrelease {bump(v, 'mainrelease')} ({tag_name(bump(v, 'mainrelease'))}); next subrelease {bump(v, 'subrelease')} ({tag_name(bump(v, 'subrelease'))})")
        elif cmd == "check":
            problems = check()
            for p in problems:
                print("version mismatch:", p, file=sys.stderr)
            if problems:
                print("Fix: packaging/version.py sync   (or packaging/build-deb.sh --tag mainrelease|subrelease)", file=sys.stderr)
                return 1
            print(f"all version files agree: {stored()}")
        elif cmd == "sync":
            changed = sync()
            print(f"version {stored()}: " + (", ".join(changed) if changed else "already in sync"))
        elif cmd == "bump":
            if len(argv) != 2:
                raise VersionError(f"usage: version.py bump {'|'.join(KINDS)}")
            old = stored()
            new = bump(old, argv[1])
            sync(ROOT, new)
            added = notes_stub(ROOT, new)
            print(f"{old} -> {new}  ({argv[1]}, tag {tag_name(new)})" + ("  [RELEASE_NOTES.md: added a section to fill in]" if added else ""))
        elif cmd == "set":
            if len(argv) != 2:
                raise VersionError("usage: version.py set x.y.z")
            old = stored()
            parse(argv[1])
            sync(ROOT, argv[1])
            print(f"{old} -> {argv[1]}  (set, tag {tag_name(argv[1])}, {kind_of(argv[1])})")
        elif cmd == "files":
            print("\n".join(["VERSION", *files(), "RELEASE_NOTES.md"]))
        else:
            raise VersionError(f"unknown command '{cmd}'; see the top of packaging/version.py")
    except VersionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
