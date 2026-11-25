"""Reset Zeus-Agent to a fresh installation, deliberately and only as far as the user chose.

`plan` describes what each choice would remove (with sizes and counts) and `run` removes it. Everything Zeus-Agent itself created can be removed;
nothing else can: the code below only ever deletes files it knows by name or pattern, never follows a symlink, and never touches

  * a project's own files (only the known contents of its `.zeus/` folder: session files, memory.md, project.json, README.md and the "*" .gitignore),
  * Ollama and its models, or models in folders the user keeps (only `*.gguf` / `*.part` files in Zeus-Agent's own download folder, and only when chosen),
  * the installed program (`sudo apt remove zeus` does that).

The five choices (`ITEMS`): the app database (conversations, project list, settings, downloads), each project's `.zeus` history and memory, `~/.zeus`
(settings.json mirror and state.json), logs and caches, and downloaded GGUF models (not chosen by default: they can be gigabytes).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

CONFIRM = "RESET"
ITEMS = {
    "conversations": {"label": "Conversations, projects and settings", "default": True,
                      "detail": "Everything in Zeus-Agent's database: conversations and their messages, the list of projects, settings, missions, command history of runs and downloads."},
    "project_data": {"label": "History and memory inside your projects", "default": True,
                     "detail": "The <project>/.zeus folder of each known project: saved conversations and project memory. Your own project files are never touched."},
    "home": {"label": "Settings copy and last-session pointer", "default": True,
             "detail": "~/.zeus/settings.json (the readable copy of your settings) and ~/.zeus/state.json (where `zeus --cli --continue` resumes)."},
    "logs": {"label": "Logs, terminal history and caches", "default": True,
             "detail": "zeus.log, cli-backend.log, llama-server.log, the terminal app's input history and the python shim folder."},
    "models": {"label": "Downloaded llama.cpp models", "default": False,
               "detail": "GGUF files that Zeus-Agent downloaded into its own models folder (they can be large). Models in other folders and everything in Ollama are never touched."},
}
LOG_FILES = ("zeus.log", "cli-backend.log", "llama-server.log", "cli_history")
PROJECT_FILES = ("memory.md", "project.json", "README.md")


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _lsize(p: Path) -> int:
    try:
        return p.lstat().st_size if p.is_file() and not p.is_symlink() else 0
    except OSError:
        return 0


def _tree_files(root: Path) -> list[Path]:
    """Regular files under root (no symlinks followed, none returned)."""
    out: list[Path] = []
    if not root.is_dir() or root.is_symlink():
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        out += [Path(dirpath) / f for f in filenames if (Path(dirpath) / f).is_file() and not (Path(dirpath) / f).is_symlink()]
    return out


def project_files(project_path: str) -> list[Path]:
    """The Zeus-Agent files inside `<project>/.zeus`, or [] when that folder is missing, a symlink, or not directly inside the project."""
    root = Path(project_path)
    z = root / ".zeus"
    try:
        if not root.is_dir() or z.is_symlink() or not z.is_dir() or z.resolve().parent != root.resolve():
            return []
    except OSError:
        return []
    files = [z / n for n in PROJECT_FILES if (z / n).is_file() and not (z / n).is_symlink()]
    gi = z / ".gitignore"
    if gi.is_file() and not gi.is_symlink() and gi.read_text("utf-8", errors="replace").strip() == "*":
        files.append(gi)
    files += [f for f in _tree_files(z / "sessions") if f.suffix in (".json", ".tmp")]
    files += [f for f in z.glob(".memory-*") if f.is_file() and not f.is_symlink()]
    return files


def db_files(db_path: str) -> list[Path]:
    return [Path(db_path + s) for s in ("", "-wal", "-shm", "-journal") if Path(db_path + s).is_file()]


def log_files(data_dir: Path) -> list[Path]:
    out = [data_dir / n for n in LOG_FILES if (data_dir / n).is_file() and not (data_dir / n).is_symlink()]
    shims = data_dir / "shims"
    if shims.is_dir() and not shims.is_symlink():
        out += [f for f in shims.iterdir() if f.is_symlink() or f.is_file()]          # the shim is a symlink to python3: removing the link, never its target
    return out


def model_files(models_dir: Path) -> list[Path]:
    if not models_dir.is_dir() or models_dir.is_symlink():
        return []
    return [f for f in _tree_files(models_dir) if f.name.endswith((".gguf", ".part", ".gguf.part"))]


def plan(db, db_path: str, home: Path, data_dir: Path, models_dir: Path, project_paths: list[str]) -> list[dict]:
    """What each choice would remove. `project_paths` are the known projects (database and settings.json)."""
    counts = {t: db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("projects", "sessions", "messages", "missions", "downloads")}
    proj = {p: project_files(p) for p in dict.fromkeys(project_paths)}
    proj = {p: f for p, f in proj.items() if f}
    state = [home / n for n in ("settings.json", "state.json") if (home / n).is_file() and not (home / n).is_symlink()]
    logs = log_files(data_dir)
    models = model_files(models_dir)
    rows = {
        "conversations": (counts, sum(_lsize(f) for f in db_files(db_path)), [str(db_path)], f"{_n(counts['projects'], 'project')}, {_n(counts['sessions'], 'conversation')}, {_n(counts['messages'], 'message')}"),
        "project_data": ({"projects": len(proj), "files": sum(len(f) for f in proj.values())}, sum(_lsize(f) for fs in proj.values() for f in fs), [str(Path(p) / ".zeus") for p in proj],
                         f"{_n(len(proj), 'project')} of yours ({_n(sum(len(f) for f in proj.values()), 'file')})"),
        "home": ({"files": len(state)}, sum(_lsize(f) for f in state), [str(f) for f in state], f"{_n(len(state), 'file')} in {home}"),
        "logs": ({"files": len(logs)}, sum(_lsize(f) for f in logs), [str(f) for f in logs], f"{_n(len(logs), 'file')} in {data_dir}"),
        "models": ({"files": len(models)}, sum(_lsize(f) for f in models), [str(f) for f in models], f"{_n(len(models), 'model file')} in {models_dir}"),
    }
    out = []
    for key, meta in ITEMS.items():
        counts_, size, paths, summary = rows[key]
        out.append({"id": key, **meta, "summary": summary, "counts": counts_, "bytes": size, "paths": paths[:25], "paths_total": len(paths)})
    return out


def wipe_database(db, db_path: str) -> dict:
    """Empty every table (the file stays, so a running app keeps working) and compact it."""
    tables = [r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    with db.conn() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        for t in tables:
            c.execute(f'DELETE FROM "{t}"')
        if c.execute("SELECT name FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            c.execute("DELETE FROM sqlite_sequence")
    if db_path != ":memory:" and Path(db_path).is_file():
        try:
            v = sqlite3.connect(db_path, timeout=10)
            v.isolation_level = None
            v.execute("VACUUM")
            v.close()
        except sqlite3.Error:
            pass
    return {"tables": len(tables)}


def _unlink(f: Path, errors: list[str]) -> int:
    try:
        n = _lsize(f)
        f.unlink()
        return n
    except FileNotFoundError:
        return 0
    except OSError as e:
        errors.append(f"{f}: {e}")
        return 0


def _rmdirs(dirs: list[Path]) -> None:
    for d in dirs:
        try:
            d.rmdir()                                  # only when empty: anything unknown inside is left alone
        except OSError:
            pass


def run(db, db_path: str, ids: list[str], home: Path, data_dir: Path, models_dir: Path, project_paths: list[str]) -> dict:
    """Remove the chosen items. Returns {"items": [{"id", "ok", "files", "bytes", "errors"}], "removed_bytes"}. Unknown ids are refused by the caller."""
    report = []
    # project_data first: it needs the project list the database wipe would erase (the caller passes the list, computed before)
    for key in [k for k in ITEMS if k in ids]:
        errors: list[str] = []
        files = freed = 0
        if key == "conversations":
            freed = sum(_lsize(f) for f in db_files(db_path))
            wipe_database(db, db_path)
            files = 1
            freed = max(0, freed - sum(_lsize(f) for f in db_files(db_path)))
        elif key == "project_data":
            for p in dict.fromkeys(project_paths):
                fs = project_files(p)
                for f in fs:
                    freed += _unlink(f, errors)
                    files += 1
                z = Path(p) / ".zeus"
                _rmdirs([z / "sessions", z])
        elif key == "home":
            for n in ("settings.json", "state.json"):
                f = home / n
                if f.is_file() and not f.is_symlink():
                    freed += _unlink(f, errors)
                    files += 1
            _rmdirs([home])
        elif key == "logs":
            for f in log_files(data_dir):
                freed += _unlink(f, errors)
                files += 1
            _rmdirs([data_dir / "shims"])
        elif key == "models":
            for f in model_files(models_dir):
                freed += _unlink(f, errors)
                files += 1
            _rmdirs([models_dir])
        report.append({"id": key, "ok": not errors, "files": files, "bytes": freed, "errors": errors[:10]})
    return {"items": report, "removed_bytes": sum(r["bytes"] for r in report)}
