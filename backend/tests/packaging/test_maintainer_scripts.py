"""The package's removal scripts (packaging/debian/prerm and postrm), run for real under `sh` with stand-ins for getent/runuser and real throw-away processes."""
from ..paths import REPO
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = REPO
BACKEND = ROOT / "backend"
PRERM, POSTRM = ROOT / "packaging/debian/prerm", ROOT / "packaging/debian/postrm"


def wait_gone(pid: int, seconds: float = 8.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            with open(f"/proc/{pid}/stat") as f:
                if f.read().rsplit(")", 1)[1].split()[0] == "Z":              # a zombie has exited; it only waits to be collected
                    return True
        except OSError:
            return True
        time.sleep(0.1)
    return False


class Box:
    """A fake machine: users with home folders, an installed package folder, stand-in system commands."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.res = tmp / "usr/lib/zeus"
        self.bin = tmp / "usr/bin/zeus"
        self.stubs = tmp / "stubs"
        self.log = tmp / "runuser.log"
        self.passwd = tmp / "passwd"
        self.homes: dict[str, Path] = {}
        (self.res / "venv/bin").mkdir(parents=True)
        (self.res / "venv/bin/python").symlink_to("/usr/bin/python3")
        (self.res / "backend").symlink_to(BACKEND)
        self.bin.parent.mkdir(parents=True)
        shutil.copy2("/bin/sleep", self.bin)
        self.stubs.mkdir()
        (self.stubs / "getent").write_text('#!/bin/sh\n[ "$1" = passwd ] && cat "$STUB_PASSWD"\n')
        (self.stubs / "runuser").write_text('#!/bin/sh\n# runuser -u USER -- command...   (no user switch here: the user is recorded)\necho "$2" >> "$STUB_LOG"\nshift 3\nexec "$@"\n')
        for s in ("getent", "runuser"):
            (self.stubs / s).chmod(0o755)
        self.lines: list[str] = []

    def user(self, name: str, uid: int, data: bool = True, models: bool = False, home_exists: bool = True) -> Path:
        home = self.tmp / "homes" / name
        if home_exists:
            home.mkdir(parents=True)
        self.homes[name] = home
        self.lines.append(f"{name}:x:{uid}:{uid}::{home}:/bin/bash")
        if data and home_exists:
            (home / ".zeus").mkdir()
            (home / ".zeus/settings.json").write_text("{}")
            (home / ".zeus/state.json").write_text("{}")
            d = home / ".local/share/zeus"
            d.mkdir(parents=True)
            (d / "zeus.log").write_text("x")
            (home / ".local/share/ai.zeus.desktop").mkdir(parents=True)
            (home / ".local/share/ai.zeus.desktop/localstorage").write_text("x")
        if models and home_exists:
            m = home / ".local/share/zeus/models"
            m.mkdir(parents=True, exist_ok=True)
            (m / "dl.gguf").write_bytes(b"G" * 100)
            (m / "x.gguf.part").write_bytes(b"P")
        (home / "Documents").mkdir(exist_ok=True) if home_exists else None
        (home / "Documents/own.txt").write_text("mine") if home_exists else None
        return home

    def run(self, script: Path, action: str, path: str | None = None, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        self.passwd.write_text("\n".join(self.lines) + "\n")
        env = {"PATH": path or f"{self.stubs}:/usr/bin:/bin", "ZEUS_RES_DIR": str(self.res), "ZEUS_BIN": str(self.bin), "STUB_PASSWD": str(self.passwd), "STUB_LOG": str(self.log),
               "HOME": str(self.tmp)}
        env.update(extra_env or {})
        return subprocess.run(["sh", str(script), action], env=env, capture_output=True, text=True, timeout=60)

    def users_cleaned(self) -> list[str]:
        return self.log.read_text().split() if self.log.exists() else []


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def has_data(home: Path) -> bool:
    return any(os.path.lexists(home / p) for p in (".zeus/settings.json", ".local/share/zeus/zeus.log", ".local/share/ai.zeus.desktop"))


# ---------------------------------------------------------------- the scripts are valid
@pytest.mark.parametrize("script", [PRERM, POSTRM, ROOT / "packaging/debian/postinst"])
def test_scripts_are_valid_posix_shell_and_executable(script):
    assert subprocess.run(["sh", "-n", str(script)], capture_output=True).returncode == 0
    assert os.access(script, os.X_OK) and script.read_text().startswith("#!/bin/sh")


def test_the_package_ships_both_removal_scripts():
    import json
    deb = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())["bundle"]["linux"]["deb"]
    assert deb["preRemoveScript"].endswith("debian/prerm") and deb["postRemoveScript"].endswith("debian/postrm") and deb["postInstallScript"].endswith("debian/postinst")


# ---------------------------------------------------------------- apt remove
def test_remove_cleans_every_real_user_and_nobody_else(box):
    alice, bob = box.user("alice", 1000, models=True), box.user("bob", 1001)
    root = box.user("root", 0)
    daemon = box.user("daemon", 999)                                 # a system account: never touched
    box.user("carol", 1002, data=False)                              # never used Zeus-Agent: nothing to do (and no helper started)
    box.user("ghost", 1003, home_exists=False)                       # no home folder
    box.user("nobody", 65534)                                        # the nobody account
    r = box.run(PRERM, "remove")
    assert r.returncode == 0, r.stderr
    assert box.users_cleaned() == ["alice", "bob", "root"]
    for h in (alice, bob, root):
        assert not has_data(h) and (h / "Documents/own.txt").read_text() == "mine"
    assert has_data(daemon) and has_data(box.homes["nobody"])
    assert (alice / ".local/share/zeus/models/dl.gguf").exists()      # apt remove keeps the downloaded models...
    assert "kept the models" in r.stdout and "apt purge zeus" in r.stdout    # ...and says so


def test_a_user_can_opt_out_with_a_keep_file(box):
    alice, bob = box.user("alice", 1000), box.user("bob", 1001)
    (alice / ".zeus/keep-data").write_text("")
    r = box.run(PRERM, "remove")
    assert r.returncode == 0 and has_data(alice) and not has_data(bob) and "kept the Zeus-Agent data" in r.stdout


def test_remove_stops_zeus_agent_and_its_llama_server_but_nothing_else(box, tmp_path):
    box.user("alice", 1000)
    fake = tmp_path / "fakeapp/app"
    fake.mkdir(parents=True)
    llama = tmp_path / "llama-server"
    shutil.copy2("/bin/sleep", llama)
    (fake / "__main__.py").write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        child = subprocess.Popen(["{llama}", "60"])
        open("{tmp_path}/child-of-%d.pid" % os.getpid(), "w").write(str(child.pid))        # one file per parent: the bystander also runs this
        time.sleep(60)
    """))
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": str(tmp_path / "fakeapp")}   # (never the real app: a temp cwd and HOME)
    here = {"cwd": str(tmp_path), "env": env}
    backend = subprocess.Popen([str(box.res / "venv/bin/python"), "-m", "app"], **here)            # looks like: /usr/lib/zeus/venv/bin/python -m app
    desktop = subprocess.Popen([str(box.bin), "60"], cwd=str(tmp_path))                             # looks like: /usr/bin/zeus
    bystander_llama = subprocess.Popen([str(llama), "60"], cwd=str(tmp_path))                       # someone else's llama-server: not ours
    bystander_python = subprocess.Popen(["/usr/bin/python3", "-m", "app"], **here)                  # another python: not ours
    try:
        mine, theirs = tmp_path / f"child-of-{backend.pid}.pid", tmp_path / f"child-of-{bystander_python.pid}.pid"
        for _ in range(80):
            if all(f.exists() and f.read_text() for f in (mine, theirs)):
                break
            time.sleep(0.1)
        child, other_child = int(mine.read_text()), int(theirs.read_text())
        r = box.run(PRERM, "remove")
        assert r.returncode == 0, r.stderr
        assert wait_gone(backend.pid) and wait_gone(desktop.pid) and wait_gone(child)             # the app, the backend and the llama-server the backend started
        assert bystander_llama.poll() is None and bystander_python.poll() is None                  # unrelated processes were left alone...
        assert os.path.exists(f"/proc/{other_child}") and open(f"/proc/{other_child}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"      # ...and so was the child of the other python
    finally:
        for p in (backend, desktop, bystander_llama, bystander_python):
            p.kill()
            p.wait()
        for f in tmp_path.glob("child-of-*.pid"):
            try:
                os.kill(int(f.read_text()), 9)
            except (OSError, ValueError):
                pass


def test_the_data_is_cleaned_after_the_app_is_stopped_so_it_cannot_come_back(box, tmp_path):
    """The old behaviour: the app kept running from deleted files and wrote its data again. Stopping comes first."""
    home = box.user("alice", 1000)
    desktop = subprocess.Popen([str(box.bin), "60"])
    try:
        r = box.run(PRERM, "remove")
        assert r.returncode == 0 and wait_gone(desktop.pid) and not has_data(home)
    finally:
        desktop.kill()
        desktop.wait()


def test_upgrades_and_reinstalls_never_touch_user_data_or_running_programs(box):
    home = box.user("alice", 1000, models=True)
    desktop = subprocess.Popen([str(box.bin), "60"])
    try:
        for action in ("upgrade", "failed-upgrade", "deconfigure", "abort-install"):
            r = box.run(PRERM, action)
            assert r.returncode == 0, (action, r.stderr)
            assert has_data(home) and (home / ".local/share/zeus/models/dl.gguf").exists(), action
        assert desktop.poll() is None and box.users_cleaned() == []
        assert not (box.res / "venv").exists()                         # (the virtualenv is rebuilt by postinst, as before)
    finally:
        desktop.kill()
        desktop.wait()


def test_the_virtualenv_and_bytecode_are_still_removed(box):
    box.user("alice", 1000)
    (box.res / "backend").unlink()
    (box.res / "backend").mkdir()
    (box.res / "backend/__pycache__").mkdir()
    (box.res / "backend/__pycache__/x.pyc").write_text("x")
    r = box.run(PRERM, "remove", extra_env={})
    assert r.returncode == 0 and not (box.res / "venv").exists() and not (box.res / "backend/__pycache__").exists()


def test_without_the_virtualenv_the_system_python_is_used(box):
    home = box.user("alice", 1000)
    (box.res / "venv/bin/python").unlink()
    r = box.run(PRERM, "remove", extra_env={})
    assert r.returncode == 0 and not has_data(home)


def test_a_failing_cleanup_never_fails_the_removal(box):
    box.user("alice", 1000)
    (box.stubs / "runuser").write_text("#!/bin/sh\nexit 3\n")
    r = box.run(PRERM, "remove")
    assert r.returncode == 0 and "could not clean the Zeus-Agent data in" in r.stderr


def test_without_runuser_it_explains_and_still_succeeds(box, tmp_path):
    home = box.user("alice", 1000)
    tools = tmp_path / "tools"
    tools.mkdir()
    for t in ("sh", "cat", "find", "rm", "sleep", "pgrep", "python3", "env", "dirname", "wc", "rmdir"):
        found = shutil.which(t)
        if found:
            (tools / t).symlink_to(found)
    (tools / "getent").write_text((box.stubs / "getent").read_text())
    (tools / "getent").chmod(0o755)
    r = box.run(PRERM, "remove", path=str(tools))
    assert r.returncode == 0 and "runuser missing" in r.stderr and has_data(home)


# ---------------------------------------------------------------- apt purge
def test_purge_removes_the_downloaded_models_of_every_user_and_only_those(box):
    alice, bob = box.user("alice", 1000, models=True), box.user("bob", 1001, models=True)
    (alice / ".local/share/zeus/models/notes.txt").write_text("not a model")
    daemon = box.user("daemon", 999, models=True)
    r = box.run(POSTRM, "purge")
    assert r.returncode == 0, r.stderr
    assert not (alice / ".local/share/zeus/models/dl.gguf").exists() and not (bob / ".local/share/zeus/models").exists() and "removed 2 downloaded model file(s)" in r.stdout
    assert (alice / ".local/share/zeus/models/notes.txt").exists()               # unknown files are never deleted
    assert (daemon / ".local/share/zeus/models/dl.gguf").exists()                  # system accounts are not touched
    assert (alice / "Documents/own.txt").exists() and (alice / ".zeus/settings.json").exists()      # purge's own job is only the models (prerm cleaned the rest)


def test_a_full_purge_leaves_no_zeus_agent_folders(box):
    home = box.user("alice", 1000, models=True)
    assert box.run(PRERM, "remove").returncode == 0                                # dpkg runs prerm remove, then postrm remove, then postrm purge
    assert box.run(POSTRM, "remove").returncode == 0
    assert (home / ".local/share/zeus/models/dl.gguf").exists()
    assert box.run(POSTRM, "purge").returncode == 0
    left = [str(p.relative_to(home)) for p in home.rglob("*") if "zeus" in str(p).lower()]
    assert left == [], left
    assert (home / "Documents/own.txt").read_text() == "mine"


@pytest.mark.parametrize("action", ["remove", "upgrade", "failed-upgrade", "abort-install", "abort-upgrade", "disappear"])
def test_postrm_does_nothing_except_on_purge(box, action):
    home = box.user("alice", 1000, models=True)
    r = box.run(POSTRM, action)
    assert r.returncode == 0 and (home / ".local/share/zeus/models/dl.gguf").exists() and box.users_cleaned() == []


def test_purge_respects_the_keep_file_and_a_symlinked_models_folder(box, tmp_path):
    alice, bob = box.user("alice", 1000, models=True), box.user("bob", 1001, models=False)
    (alice / ".zeus/keep-data").write_text("")
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "big.gguf").write_bytes(b"mine")
    (bob / ".local/share/zeus").mkdir(parents=True, exist_ok=True)
    (bob / ".local/share/zeus/models").symlink_to(precious)                        # the models folder is a link to the user's own models
    r = box.run(POSTRM, "purge")
    assert r.returncode == 0 and (alice / ".local/share/zeus/models/dl.gguf").exists() and (precious / "big.gguf").read_bytes() == b"mine"


def test_removal_never_touches_anything_inside_a_project(box, tmp_path):
    """A project's own .zeus folder (saved conversations, project memory) is project data: it stays, even though the project is listed in the user's settings."""
    import json
    home = box.user("alice", 1000, models=True)
    proj = tmp_path / "work/p1"
    (proj / ".zeus/sessions").mkdir(parents=True)
    (proj / ".zeus/sessions/a.json").write_text("{}")
    (proj / ".zeus/memory.md").write_text("- keep me\n")
    (proj / "main.py").write_text("mine")
    (home / ".zeus/settings.json").write_text(json.dumps({"projects": [{"path": str(proj)}]}))
    assert box.run(PRERM, "remove").returncode == 0
    assert not (home / ".zeus/settings.json").exists()                                  # the home locations are cleaned...
    assert (proj / ".zeus/memory.md").read_text() == "- keep me\n" and (proj / ".zeus/sessions/a.json").exists() and (proj / "main.py").read_text() == "mine"   # ...the project is not
    assert box.run(POSTRM, "purge").returncode == 0 and (proj / ".zeus/memory.md").exists()
