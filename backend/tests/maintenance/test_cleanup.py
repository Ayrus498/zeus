"""app.maintenance.cleanup: what `apt remove zeus` takes from a user's home folder, and what it must never touch."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.maintenance import cleanup

PY3 = "/usr/bin/python3"


def build_home(tmp_path):
    """A home folder as Zeus-Agent leaves it, with a project that has its own files, a project known only to the database, and things that must survive."""
    home = tmp_path / "home"
    z, data = home / ".zeus", home / ".local/share/zeus"
    z.mkdir(parents=True)
    (data / "shims").mkdir(parents=True)
    (data / "models").mkdir()
    proj1, proj2 = tmp_path / "work/proj1", tmp_path / "work/proj2"
    for p in (proj1, proj2):
        (p / "src").mkdir(parents=True)
        (p / "src/main.py").write_text("print('mine')\n")
        (p / "README.md").write_text("# mine\n")
        (p / ".zeus/sessions").mkdir(parents=True)
        (p / ".zeus/sessions/abc.json").write_text("{}")
        (p / ".zeus/memory.md").write_text("- uses FastAPI\n")
        (p / ".zeus/project.json").write_text("{}")
        (p / ".zeus/README.md").write_text("# .zeus")
        (p / ".zeus/.gitignore").write_text("*\n")
    (z / "settings.json").write_text(json.dumps({"projects": [{"path": str(proj1), "name": "proj1"}]}))
    (z / "state.json").write_text("{}")
    con = sqlite3.connect(data / "zeus.db")
    con.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT, path TEXT)")
    con.execute("INSERT INTO projects(name,path) VALUES('proj2', ?)", (str(proj2),))
    con.commit()
    con.close()
    for n in ("zeus.log", "cli-backend.log", "llama-server.log", "cli_history"):
        (data / n).write_text("log\n")
    (data / "shims/python").symlink_to(PY3)
    (data / "models/m.gguf").write_bytes(b"G" * 5000)
    (data / "models/m2.gguf.part").write_bytes(b"P" * 100)
    for d in (".local/share/ai.zeus.desktop/localstorage", ".cache/ai.zeus.desktop/WebKitCache", ".config/ai.zeus.desktop"):
        (home / d).mkdir(parents=True)
        (home / d / "f").write_text("x")
    # things that must survive
    (home / ".ollama/models/blobs").mkdir(parents=True)
    (home / ".ollama/models/blobs/sha256-keep").write_bytes(b"O" * 100)
    (home / ".cache/huggingface/hub").mkdir(parents=True)
    (home / ".cache/huggingface/hub/keep.txt").write_text("hf")
    (home / ".local/share/other-app").mkdir()
    (home / ".local/share/other-app/data").write_text("other")
    (home / "Documents").mkdir()
    (home / "Documents/own.gguf").write_bytes(b"mine")
    return home, proj1, proj2


def paths(home):
    return {"settings": home / ".zeus/settings.json", "state": home / ".zeus/state.json", "db": home / ".local/share/zeus/zeus.db", "log": home / ".local/share/zeus/zeus.log",
            "hist": home / ".local/share/zeus/cli_history", "shim": home / ".local/share/zeus/shims", "web": home / ".local/share/ai.zeus.desktop", "cache": home / ".cache/ai.zeus.desktop",
            "cfg": home / ".config/ai.zeus.desktop"}


def run_main(home, *args, env=None, capsys=None):
    rc = cleanup.main([*args, "--home", str(home)], environ={"HOME": str(home), **(env or {})})
    out = capsys.readouterr() if capsys else None
    return rc, out


def test_remove_cleans_the_home_locations_but_keeps_models_and_everything_inside_projects(tmp_path, capsys):
    home, proj1, proj2 = build_home(tmp_path)
    rc, out = run_main(home, capsys=capsys)
    assert rc == 0
    for name, p in paths(home).items():
        assert not os.path.lexists(p), f"{name} should be gone: {p}"
    for proj in (proj1, proj2):                                                           # one project is listed in settings.json, the other only in the database
        assert (proj / ".zeus/memory.md").read_text() == "- uses FastAPI\n" and (proj / ".zeus/sessions/abc.json").exists()      # project data stays with the project
        assert (proj / ".zeus/project.json").exists() and (proj / ".zeus/README.md").exists() and (proj / ".zeus/.gitignore").exists()
        assert (proj / "src/main.py").read_text() == "print('mine')\n" and (proj / "README.md").read_text() == "# mine\n"
    assert (home / ".local/share/zeus/models/m.gguf").exists()                              # downloaded models: kept by apt remove
    assert "removed the Zeus-Agent settings" in out.out and "kept the models" in out.out and "apt purge zeus" in out.out and str(home / ".local/share/zeus/models") in out.out


def test_purge_also_removes_the_downloaded_models_and_then_the_empty_folders(tmp_path, capsys):
    home, *_ = build_home(tmp_path)
    (home / ".local/share/zeus/models/notes.txt").write_text("not a model")
    run_main(home, "--models", capsys=capsys)
    assert not (home / ".local/share/zeus/models/m.gguf").exists() and not (home / ".local/share/zeus/models/m2.gguf.part").exists()
    assert (home / ".local/share/zeus/models/notes.txt").exists()                          # unknown files stay (so does their folder)
    (home / ".local/share/zeus/models/notes.txt").unlink()
    run_main(home, "--models", capsys=capsys)
    assert not (home / ".local/share/zeus").exists() and not (home / ".zeus").exists()


def test_everything_that_is_not_zeus_agents_survives(tmp_path, capsys):
    home, *_ = build_home(tmp_path)
    run_main(home, "--models", capsys=capsys)
    assert (home / ".ollama/models/blobs/sha256-keep").read_bytes() == b"O" * 100          # Ollama's store
    assert (home / ".cache/huggingface/hub/keep.txt").exists() and (home / ".local/share/other-app/data").read_text() == "other"
    assert (home / "Documents/own.gguf").read_bytes() == b"mine"                            # a model in the user's own folder


def test_files_of_the_user_next_to_zeus_agents_are_kept(tmp_path, capsys):
    home, proj1, _ = build_home(tmp_path)
    (home / ".zeus/extra.txt").write_text("mine")
    (home / ".local/share/zeus/llama.cpp").mkdir()
    (home / ".local/share/zeus/llama.cpp/llama-server").write_text("a program the user put here")
    run_main(home, capsys=capsys)
    assert (home / ".zeus/extra.txt").exists() and (home / ".local/share/zeus/llama.cpp/llama-server").exists()
    assert not (home / ".zeus/settings.json").exists()


def test_the_user_can_keep_their_data(tmp_path, capsys):
    home, proj1, _ = build_home(tmp_path)
    (home / ".zeus/keep-data").write_text("")
    rc, out = run_main(home, capsys=capsys)
    assert rc == 0 and "kept the Zeus-Agent data" in out.out and (home / ".zeus/settings.json").exists() and (proj1 / ".zeus/memory.md").exists() and paths(home)["web"].exists()
    (home / ".zeus/keep-data").unlink()
    rc, out = run_main(home, env={"ZEUS_KEEP_USER_DATA": "1"}, capsys=capsys)
    assert rc == 0 and (home / ".zeus/settings.json").exists()


def test_dry_run_lists_and_removes_nothing(tmp_path, capsys):
    home, proj1, _ = build_home(tmp_path)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    rc, out = run_main(home, "--dry-run", capsys=capsys)
    assert rc == 0 and "would remove" in out.out and "would free" in out.out and str(home / ".zeus/settings.json") in out.out
    assert str(proj1) not in out.out                                                      # nothing inside a project is even listed
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def test_nothing_to_clean_is_silent_and_it_is_safe_to_run_twice(tmp_path, capsys):
    empty = tmp_path / "empty-home"
    empty.mkdir()
    rc, out = run_main(empty, capsys=capsys)
    assert rc == 0 and out.out == "" and out.err == ""
    home, *_ = build_home(tmp_path)
    run_main(home, capsys=capsys)
    rc, out = run_main(home, capsys=capsys)
    assert rc == 0 and "Traceback" not in out.err


def test_symlinks_are_never_followed(tmp_path, capsys):
    home, proj1, _ = build_home(tmp_path)
    import shutil
    shutil.rmtree(home / ".local/share/ai.zeus.desktop")
    precious = tmp_path / "precious-window-data"
    precious.mkdir()
    (precious / "keep").write_text("x")
    (home / ".local/share/ai.zeus.desktop").symlink_to(precious)                           # the window folder itself is a link
    (home / ".cache/ai.zeus.desktop/WebKitCache/link").symlink_to(precious)                # a link inside a real window folder
    outside = tmp_path / "outside-data"
    outside.mkdir()
    (outside / "zeus.log").write_text("keep")
    (home / ".local/share/zeus/cli-backend.log").unlink(missing_ok=True)
    (home / ".local/share/zeus/cli-backend.log").symlink_to(outside / "zeus.log")          # a log file that is a link
    run_main(home, capsys=capsys)
    assert (precious / "keep").read_text() == "x" and not (home / ".local/share/ai.zeus.desktop").is_symlink()   # the link went, not its target
    assert not (home / ".cache/ai.zeus.desktop").exists() and (precious / "keep").exists()
    assert (outside / "zeus.log").read_text() == "keep"                                    # a linked log is not followed (nor deleted through the link)
    assert (proj1 / ".zeus/memory.md").exists()


def test_paths_written_in_the_data_files_are_never_used_to_delete_anything(tmp_path, capsys):
    """settings.json and the database are the user's files: a path in them must never make the cleanup delete anything outside Zeus-Agent's own folders."""
    home, proj1, proj2 = build_home(tmp_path)
    target = tmp_path / "work/precious"
    (target / ".zeus/sessions").mkdir(parents=True)
    (target / ".zeus/sessions/s.json").write_text("{}")
    (target / ".zeus/memory.md").write_text("- keep")
    s = json.loads((home / ".zeus/settings.json").read_text())
    s["projects"] += [{"path": str(target)}, {"path": "/"}, {"path": str(home)}, {"path": str(tmp_path)}, {"path": "../../etc"}]
    (home / ".zeus/settings.json").write_text(json.dumps(s))
    before = {str(p) for p in (tmp_path / "work").rglob("*")}
    run_main(home, "--models", capsys=capsys)
    assert {str(p) for p in (tmp_path / "work").rglob("*")} == before
    assert (home / "Documents/own.gguf").exists() and (home / ".ollama/models/blobs/sha256-keep").exists()


def test_a_broken_database_or_settings_file_does_not_stop_the_cleanup(tmp_path, capsys):
    home, proj1, proj2 = build_home(tmp_path)
    (home / ".local/share/zeus/zeus.db").write_bytes(b"this is not a database")
    (home / ".zeus/settings.json").write_text("{ not json")
    rc, out = run_main(home, capsys=capsys)
    assert rc == 0 and not paths(home)["db"].exists() and not paths(home)["settings"].exists()
    assert (proj1 / ".zeus/memory.md").exists() and (proj2 / ".zeus/memory.md").exists() and (proj1 / "src/main.py").exists()


def test_a_failure_is_reported_but_never_fails_the_removal(tmp_path, capsys):
    home, *_ = build_home(tmp_path)
    locked = home / ".local/share/zeus"
    locked.chmod(0o500)                                                                    # files in it cannot be deleted
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("running with privileges that ignore directory permissions")
        rc, out = run_main(home, capsys=capsys)
        assert rc == 0 and "could not remove" in out.err
    finally:
        locked.chmod(0o700)


def test_the_environment_can_move_the_folders(tmp_path, capsys):
    home, *_ = build_home(tmp_path)
    elsewhere = tmp_path / "elsewhere-data"
    elsewhere.mkdir()
    (elsewhere / "zeus.log").write_text("x")
    rc, _ = run_main(home, env={"ZEUS_DATA_DIR": str(elsewhere), "ZEUS_HOME": str(tmp_path / "elsewhere-home")}, capsys=capsys)
    assert not (elsewhere / "zeus.log").exists()
    assert (home / ".zeus/settings.json").exists() and (home / ".local/share/zeus/zeus.db").exists()    # the default folders were not the ones in use


def test_it_only_imports_the_standard_library():
    import ast
    tree = ast.parse(Path(cleanup.__file__).read_text())
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    assert mods <= {"argparse", "os", "shutil", "sys", "pathlib", "__future__"}      # it runs with the system python while a package is being removed
