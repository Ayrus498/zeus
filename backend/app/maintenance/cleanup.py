"""Remove everything Zeus-Agent keeps for ONE user. Run by the package's removal script (as that user, never as root):  python -m app.maintenance.cleanup [--models] [--dry-run]

What `apt remove zeus` takes with it, for every user that used Zeus-Agent, are the SYSTEM locations in the user's home folder only:
  * ~/.zeus (settings copy, last-session pointer) and ~/.local/share/zeus (database with conversations and settings, logs, terminal history, python shim),
  * the desktop window's own storage (~/.local/share, ~/.cache and ~/.config of `ai.zeus.desktop`: remembered window state, caches).
`--models` (`apt purge zeus`) also removes the GGUF models Zeus-Agent downloaded itself; `apt remove` keeps them (they can be gigabytes) and says where they are.
Never touched: anything inside the user's projects (their files AND each project's own `.zeus` folder with its saved conversations and project memory: that is
project data that travels with the project, and Zeus-Agent restores it when the project is opened again), Ollama and its models, models in the user's own folders.

Safety (this runs while a package is being removed, so it is strict): the user's data is cleaned by the user, not by root, so permissions apply; only known files are
deleted (the same rules as the Reset dialog, `persistence/reset.py`) and no path read from a data file is ever deleted; no symlink is ever followed (a link is removed, never its target); a user can opt out by creating
`~/.zeus/keep-data`. It never fails the removal: problems are reported and the exit status is 0.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import reset

WINDOW_ID = "ai.zeus.desktop"                                     # the desktop app's identifier: names the folders its web view keeps
KEEP_FILE = "keep-data"


def window_dirs(home: Path) -> list[Path]:
    return [home / ".local" / "share" / WINDOW_ID, home / ".cache" / WINDOW_ID, home / ".config" / WINDOW_ID]


def _tree_size(p: Path) -> int:
    total = 0
    for dirpath, dirs, files in os.walk(p, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(dirpath) / d).is_symlink()]
        for f in files:
            total += reset._lsize(Path(dirpath) / f)
    return total


def has_anything(home: Path, zeus_home: Path, data_dir: Path) -> bool:
    return any(os.path.lexists(p) for p in (zeus_home, data_dir, *window_dirs(home)))


def plan(home: Path, zeus_home: Path, data_dir: Path, models: bool = False) -> dict:
    """What would be removed: {"files": [...], "dirs": [...], "bytes": n, "models_kept": (dir, bytes) | None}."""
    files: list[Path] = []
    files += reset.db_files(str(data_dir / "zeus.db"))
    files += [zeus_home / n for n in ("settings.json", "state.json") if (zeus_home / n).is_file() and not (zeus_home / n).is_symlink()]
    files += reset.log_files(data_dir)
    mf = reset.model_files(data_dir / "models")
    kept = None
    if models:
        files += mf
    elif mf:
        kept = (str(data_dir / "models"), sum(reset._lsize(f) for f in mf))
    dirs = [d for d in window_dirs(home) if os.path.lexists(d)]
    size = sum(reset._lsize(f) for f in files) + sum(_tree_size(d) for d in dirs if d.is_dir() and not d.is_symlink())
    return {"files": files, "dirs": dirs, "bytes": size, "models_kept": kept}


def run(home: Path, zeus_home: Path, data_dir: Path, models: bool = False) -> dict:
    p = plan(home, zeus_home, data_dir, models)
    errors: list[str] = []
    for f in p["files"]:
        reset._unlink(f, errors)
    for d in p["dirs"]:
        try:
            if d.is_symlink():
                d.unlink()                                          # a link is removed, never what it points to
            else:
                shutil.rmtree(d)
        except OSError as e:
            errors.append(f"{d}: {e}")
    reset._rmdirs([data_dir / "shims", data_dir / "models", data_dir, zeus_home])
    return {**p, "errors": errors}


def mb(n: int) -> str:
    return f"{n / 2**20:.1f} MB" if n >= 2**20 else f"{max(n // 1024, 0)} KB"


def main(argv: list[str] | None = None, environ: dict | None = None) -> int:
    env = os.environ if environ is None else environ
    ap = argparse.ArgumentParser(prog="python -m app.maintenance.cleanup", description="Remove the Zeus-Agent data of the current user.")
    ap.add_argument("--models", action="store_true", help="also remove the GGUF models Zeus-Agent downloaded")
    ap.add_argument("--dry-run", action="store_true", help="show what would be removed, remove nothing")
    ap.add_argument("--home", help="the user's home folder (default: $HOME)")
    a = ap.parse_args(argv)
    try:
        home = Path(a.home or env.get("HOME") or Path.home())
        zeus_home = Path(env["ZEUS_HOME"]) if env.get("ZEUS_HOME") else home / ".zeus"
        data_dir = Path(env["ZEUS_DATA_DIR"]) if env.get("ZEUS_DATA_DIR") else home / ".local" / "share" / "zeus"
        if not has_anything(home, zeus_home, data_dir):
            return 0
        if (zeus_home / KEEP_FILE).exists() or env.get("ZEUS_KEEP_USER_DATA") == "1":
            print(f"zeus: kept the Zeus-Agent data of {home} (opt-out file {zeus_home / KEEP_FILE}).")
            return 0
        if a.dry_run:
            p = plan(home, zeus_home, data_dir, a.models)
            for f in p["files"]:
                print(f"would remove {f}")
            for d in p["dirs"]:
                print(f"would remove {d}/")
            print(f"zeus: would free {mb(p['bytes'])}")
            return 0
        r = run(home, zeus_home, data_dir, a.models)
        print(f"zeus: removed the Zeus-Agent settings, history and window data in {home} ({len(r['files'])} files, {mb(r['bytes'])}).")
        if r["models_kept"]:
            d, n = r["models_kept"]
            print(f"zeus: kept the models Zeus-Agent downloaded ({mb(n)} in {d}); 'sudo apt purge zeus' removes them too.")
        for e in r["errors"][:5]:
            print(f"zeus: could not remove {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 - a package removal must never fail because of this
        print(f"zeus: could not clean the Zeus-Agent data ({type(e).__name__}: {e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
