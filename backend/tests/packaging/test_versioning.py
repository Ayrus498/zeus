"""The release/version system: packaging/version.py and the --tag handling of packaging/build-deb.sh (no real build is run)."""
from ..paths import REPO
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = REPO
sys.path.insert(0, str(ROOT / "packaging"))
import version as ver  # noqa: E402

COPY = ["VERSION", "RELEASE_NOTES.md", "backend/app/core/config.py", "src-tauri/Cargo.toml", "src-tauri/Cargo.lock", "src-tauri/tauri.conf.json",
        "frontend/package.json", "frontend/package-lock.json", "packaging/version.py", "packaging/build-deb.sh", "packaging/publish.py"]


@pytest.fixture
def tree(tmp_path):
    for rel in COPY:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tmp_path / rel)
    return tmp_path


# ---- the real repository
def test_the_repository_versions_agree():
    assert ver.check(ROOT) == [], "run packaging/version.py sync"
    from app.core import config
    assert config.VERSION == ver.stored(ROOT)


# ---- pure functions
@pytest.mark.parametrize("v,kind,new", [("0.2.2", "subrelease", "0.2.3"), ("0.2.2", "mainrelease", "0.3.0"), ("1.9.9", "mainrelease", "1.10.0"), ("0.2.9", "subrelease", "0.2.10")])
def test_bump(v, kind, new):
    assert ver.bump(v, kind) == new


@pytest.mark.parametrize("bad", ["", "1.2", "v1.2.3", "1.2.3.4", "a.b.c", "1.2.x"])
def test_parse_rejects_malformed_versions(bad):
    with pytest.raises(ver.VersionError):
        ver.parse(bad)


def test_unknown_release_kind_is_refused():
    with pytest.raises(ver.VersionError):
        ver.bump("0.2.2", "bigrelease")


def test_tag_names_follow_the_existing_tags_and_kind_follows_the_patch():
    assert ver.tag_name("0.2.0") == "v02_00" and ver.tag_name("0.2.1") == "v02_01" and ver.tag_name("0.3.0") == "v03_00"
    assert ver.tag_name("1.2.3") == "v1_02_03"
    assert ver.kind_of("0.3.0") == "mainrelease" and ver.kind_of("0.3.1") == "subrelease"


# ---- the files
def test_read_versions_finds_every_file(tree):
    found = ver.read_versions(tree)
    assert set(found) == set(ver.files()) and all(v == [ver.stored(tree)] * len(v) for v in found.values())
    assert len(found["frontend/package-lock.json"]) == 2


def test_sync_writes_every_file_and_keeps_everything_else(tree):
    before = {r: (tree / r).read_text() for r in COPY}
    old_v = ver.stored(tree)                                                             # whatever version the repository is at when the tests run
    changed = ver.sync(tree, "0.9.4")
    assert set(changed) == set(ver.files())
    assert (tree / "VERSION").read_text() == "0.9.4\n" and ver.check(tree) == []
    for rel in ver.files():                                                              # only the version text changed: same lines, same formatting
        old, new = before[rel], (tree / rel).read_text()
        assert new.count("\n") == old.count("\n") and len(new.splitlines()) == len(old.splitlines())
        changed_lines = [(a, b) for a, b in zip(old.splitlines(), new.splitlines()) if a != b]
        assert len(changed_lines) == (2 if rel.endswith("package-lock.json") else 1), (rel, changed_lines)
        assert all(b == a.replace(old_v, "0.9.4") for a, b in changed_lines)
    lock = json.loads((tree / "frontend/package-lock.json").read_text())
    assert lock["version"] == "0.9.4" and lock["packages"][""]["version"] == "0.9.4"
    assert any(p["name"] == "zeus" and p["version"] == "0.9.4" for p in _cargo_packages(tree))
    assert 'name = "zeus"' in (tree / "src-tauri/Cargo.lock").read_text()
    assert json.loads((tree / "src-tauri/tauri.conf.json").read_text())["version"] == "0.9.4"


def _cargo_packages(tree):
    import re
    return [{"name": m[1], "version": m[2]} for m in re.finditer(r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', (tree / "src-tauri/Cargo.lock").read_text())]


def test_sync_does_not_touch_dependency_versions(tree):
    deps_before = [p for p in _cargo_packages(tree) if p["name"] != "zeus"]
    ver.sync(tree, "7.7.7")
    assert [p for p in _cargo_packages(tree) if p["name"] != "zeus"] == deps_before
    pkg = json.loads((tree / "frontend/package-lock.json").read_text())
    assert pkg["packages"]["node_modules/react"]["version"] != "7.7.7"


def test_check_reports_a_file_that_drifted(tree):
    cfg = tree / "backend/app/core/config.py"
    cfg.write_text(cfg.read_text().replace(f'VERSION = "{ver.stored(tree)}"', 'VERSION = "0.0.1"'))
    problems = ver.check(tree)
    assert len(problems) == 1 and "backend/app/core/config.py: 0.0.1" in problems[0]


def test_sync_changes_nothing_when_a_pattern_no_longer_matches(tree):
    before = {r: (tree / r).read_text() for r in COPY}
    (tree / "src-tauri/Cargo.toml").write_text("[package]\nname = 'zeus'\n")             # layout changed: no version line
    with pytest.raises(ver.VersionError):
        ver.sync(tree, "0.9.9")
    for rel in COPY:
        if rel != "src-tauri/Cargo.toml":
            assert (tree / rel).read_text() == before[rel], f"{rel} was modified although the sync failed"


def test_release_notes_get_a_section_to_fill_in_once(tree):
    (tree / "RELEASE_NOTES.md").write_text("# Zeus-Agent release notes\n\n## 0.2.2: something (2026-01-01)\n\n### New\n- a\n\n## 0.2.1: older (2025-12-01)\n- b\n")
    assert ver.notes_stub(tree, "0.2.3") is True
    text = (tree / "RELEASE_NOTES.md").read_text()
    assert text.index("## 0.2.3: UNRELEASED") < text.index("## 0.2.2")                    # newest first
    assert text.count("## 0.2.3") == 1 and "## 0.2.1: older" in text
    assert ver.notes_stub(tree, "0.2.3") is False and ver.notes_stub(tree, "0.2.2") is False   # an existing section is left alone


def test_set_goes_back_to_an_earlier_version_and_rejects_nonsense(tree, monkeypatch):
    monkeypatch.setattr(ver, "ROOT", tree)
    assert ver.main(["set", "0.1.0"]) == 0
    assert ver.stored(tree) == "0.1.0" and ver.check(tree) == []
    assert ver.main(["set", "banana"]) == 2 and ver.main(["set"]) == 2
    assert ver.stored(tree) == "0.1.0"                                                    # a refused value changes nothing


# ---- the script
def run(tree, *args, path=None):
    env = dict(os.environ)
    if path:
        env["PATH"] = path
    return subprocess.run(["bash", str(tree / "packaging/build-deb.sh"), *args], cwd=tree, env=env, capture_output=True, text=True, timeout=120)


def test_script_rejects_bad_arguments(tree):
    assert run(tree, "--tag", "bigrelease").returncode == 2
    assert run(tree, "--tag").returncode == 2
    assert run(tree, "--whatever").returncode == 2
    h = run(tree, "--help")
    assert h.returncode == 0 and "--tag mainrelease" in h.stdout and "set -euo" not in h.stdout
    assert ver.stored(tree) == (ROOT / "VERSION").read_text().strip()                     # nothing was bumped by a refused command


def test_a_failed_tagged_build_restores_the_previous_version(tree, tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    for tool in ("python3", "bash", "grep", "sed", "cp", "mkdir", "dirname", "tr", "rm", "mktemp", "cat", "date"):
        found = shutil.which(tool)
        if found:
            (stub / tool).symlink_to(found)
    before = {r: (tree / r).read_text() for r in COPY}
    old_v = ver.stored(tree)
    r = run(tree, "--tag", "subrelease", path=str(stub))                                  # node/npm/cargo are not on this PATH: the build fails after the bump
    assert r.returncode != 0 and "missing tool" in r.stderr
    assert f"{old_v} -> {ver.bump(old_v, 'subrelease')}" in r.stdout and f"version restored to {old_v}" in r.stderr
    for rel in COPY:
        assert (tree / rel).read_text() == before[rel], f"{rel} was not restored"


def test_a_plain_build_with_drifted_files_is_refused_before_anything_is_built(tree):
    cfg = tree / "backend/app/core/config.py"
    cur = ver.stored(tree)
    cfg.write_text(cfg.read_text().replace(f'VERSION = "{cur}"', 'VERSION = "0.0.1"'))
    r = run(tree)
    assert r.returncode == 1 and "version mismatch" in r.stderr and "backend/app/core/config.py" in r.stderr


# ---- publishing into dist/
def deb(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def pub(tmp_path):
    import publish
    return publish, tmp_path


def test_publish_keeps_only_the_stored_version_in_dist_and_archives_the_rest(pub):
    publish, root = pub
    deb(root / "dist/zeus_0.2.1_amd64.deb", b"old one")
    (root / "dist/zeus_0.2.1_amd64.deb.sha256").write_text("x  zeus_0.2.1_amd64.deb\n")
    deb(root / "dist/zeus_0.2.3_amd64.deb", b"newer one")
    built = deb(root / "bundle/zeus_0.2.2_amd64.deb", b"the build")
    r = publish.publish(root, "0.2.2", built)
    assert sorted(p.name for p in (root / "dist").glob("zeus_*")) == ["zeus_0.2.2_amd64.deb", "zeus_0.2.2_amd64.deb.sha256"]
    assert sorted(p.name for p in (root / "dist/archive").glob("*.deb")) == ["zeus_0.2.1_amd64.deb", "zeus_0.2.3_amd64.deb"]
    assert (root / "dist/archive/zeus_0.2.1_amd64.deb.sha256").exists()                    # the checksum travels with its package
    assert len(r["archived"]) == 2


def test_publish_never_overwrites_an_earlier_build_of_the_same_version(pub):
    publish, root = pub
    first = deb(root / "dist/zeus_0.2.2_amd64.deb", b"what I installed yesterday")
    keep = publish.sha256(first)
    rebuilt = deb(root / "bundle/zeus_0.2.2_amd64.deb", b"a rebuild with different bytes")
    r = publish.publish(root, "0.2.2", rebuilt)
    archived = list((root / "dist/archive").glob("zeus_0.2.2_amd64.*.deb"))
    assert len(archived) == 1 and archived[0].name == f"zeus_0.2.2_amd64.{keep[:12]}.deb" and archived[0].read_bytes() == b"what I installed yesterday"
    assert (root / "dist/zeus_0.2.2_amd64.deb").read_bytes() == b"a rebuild with different bytes"
    assert r["manifest"]["sha256"] == publish.sha256(rebuilt)


def test_publishing_the_identical_package_again_archives_nothing(pub):
    publish, root = pub
    built = deb(root / "bundle/zeus_0.2.2_amd64.deb", b"same bytes")
    publish.publish(root, "0.2.2", built)
    r = publish.publish(root, "0.2.2", built)
    assert r["archived"] == []
    assert not (root / "dist/archive").exists() or list((root / "dist/archive").glob("*")) == []


def test_an_archived_name_is_never_replaced(pub):
    publish, root = pub
    deb(root / "dist/archive/zeus_0.2.1_amd64.deb", b"already archived")
    deb(root / "dist/zeus_0.2.1_amd64.deb", b"another copy")
    built = deb(root / "bundle/zeus_0.2.2_amd64.deb", b"b")
    publish.publish(root, "0.2.2", built)
    assert (root / "dist/archive/zeus_0.2.1_amd64.deb").read_bytes() == b"already archived"
    assert any(p.read_bytes() == b"another copy" for p in (root / "dist/archive").glob("*.deb"))


def test_the_manifest_and_checksum_file_describe_the_published_package(pub):
    import json as _json
    publish, root = pub
    built = deb(root / "bundle/zeus_0.3.0_amd64.deb", b"x" * 1000)
    publish.publish(root, "0.3.0", built)
    m = _json.loads((root / "dist/RELEASE.json").read_text())
    assert m["version"] == "0.3.0" and m["tag"] == "v03_00" and m["kind"] == "mainrelease" and m["deb"] == "zeus_0.3.0_amd64.deb" and m["sha256"] == publish.sha256(built)
    side = (root / "dist/zeus_0.3.0_amd64.deb.sha256").read_text().split()
    assert side == [publish.sha256(built), "zeus_0.3.0_amd64.deb"]


def test_the_built_package_must_match_the_version(pub):
    publish, root = pub
    with pytest.raises(SystemExit):
        publish.publish(root, "0.2.2", deb(root / "bundle/zeus_0.2.1_amd64.deb", b"b"))
