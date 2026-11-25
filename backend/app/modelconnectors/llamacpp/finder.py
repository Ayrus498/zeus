"""Finds llama.cpp's `llama-server` program on this computer, wherever it was installed or built.

A desktop app does not see the PATH of your terminal, and llama.cpp is not installed in one standard place: people unpack a release into ~/Downloads,
build it in ~/Documents/ai/llama.cpp/build/bin, or keep it under /opt. So besides PATH and the usual bin folders, this looks through the home folder
and /opt, /usr/local (breadth first, a few levels deep, skipping folders that never hold programs, with a time and entry budget so a huge disk never
makes it slow) for a file named `llama-server`.

A file is only accepted when it looks like a real llama.cpp installation (so a stray or unrelated file with that name is never run):
  * a regular executable ELF program,
  * with the rest of llama.cpp next to it (its `libllama*.so` / `libggml*.so` libraries or `llama-cli`),
  * and not Ollama's own internal runner (`/usr/local/lib/ollama/llama-server`), which is a different program with a different interface.
Nothing is executed here; the caller probes the accepted programs for their version and GPU support.
"""
from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path

NAME = "llama-server"
SKIP = {"node_modules", ".git", ".cache", ".venv", "venv", "env", "__pycache__", "site-packages", "target", ".npm", ".cargo", ".rustup", "snap", ".Trash",
        ".mozilla", ".config", ".var", ".steam", ".wine", ".docker", "proc", "sys", "dist-packages", ".gradle", ".m2", ".nvm", ".pyenv", ".conda", "miniconda3",
        "anaconda3", ".ollama", "ollama", "models", "datasets"}
HIDDEN_OK = {".local", ".opt", ".llama.cpp"}
MAX_DEPTH = 6
MAX_ENTRIES = 80_000
DEADLINE_S = 4.0
SIBLING_EVIDENCE = ("libllama", "libggml", "libmtmd")
SIBLING_PROGRAMS = ("llama-cli", "llama-bench", "llama-quantize", "llama-cli.exe")


def is_ollama_path(p: str | Path) -> bool:
    return "ollama" in {part.lower() for part in Path(p).parts}


def is_executable_elf(p: Path) -> bool:
    try:
        if not p.is_file() or not os.access(p, os.X_OK):
            return False
        with open(p, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def has_llama_siblings(p: Path) -> bool:
    """The rest of llama.cpp is next to the program (or in the ../lib folder of an installed tree): its libraries or its other tools."""
    for d in (p.parent, p.parent.parent / "lib", p.parent.parent / "lib64"):
        try:
            for n in os.listdir(d):
                if n.startswith(SIBLING_EVIDENCE) or n in SIBLING_PROGRAMS:
                    return True
        except OSError:
            continue
    return False


def is_genuine(p: Path) -> bool:
    try:
        real = p.resolve()
    except OSError:
        return False
    return not is_ollama_path(real) and not is_ollama_path(p) and is_executable_elf(real) and has_llama_siblings(real)


def scan(roots: list[Path], max_depth: int = MAX_DEPTH, max_entries: int = MAX_ENTRIES, deadline: float = DEADLINE_S) -> list[Path]:
    """Files named llama-server under the roots, breadth first (shallow places first), within the entry and time budget. Symlinked folders are not followed."""
    out: list[Path] = []
    seen_dirs: set[str] = set()
    end = time.monotonic() + deadline
    entries = 0
    q: deque[tuple[Path, int]] = deque((r, 0) for r in roots if r.is_dir())
    while q:
        d, depth = q.popleft()
        if time.monotonic() > end or entries > max_entries:
            break
        try:
            key = str(d.resolve())
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            with os.scandir(d) as it:
                for e in it:
                    entries += 1
                    name = e.name
                    try:
                        if e.is_symlink():
                            if name == NAME and e.is_file():                     # a link to the program (the usual `ln -s` into ~/bin)
                                out.append(Path(e.path))
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth and name not in SKIP and (not name.startswith(".") or name in HIDDEN_OK):
                                q.append((Path(e.path), depth + 1))
                        elif name == NAME:
                            out.append(Path(e.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def default_roots(home: Path, data_dir: Path) -> list[Path]:
    return [home, Path("/opt"), Path("/usr/local"), data_dir]


def label(p: Path, home: Path) -> str:
    """A short place name for the interface: 'Downloads', 'Documents', '/opt', 'built from source' ..."""
    try:
        rel = p.relative_to(home)
        return str(rel.parts[0]) if len(rel.parts) > 1 else "home"
    except ValueError:
        return "/".join(p.parts[:3]) if len(p.parts) > 3 else str(p.parent)


def discover(home: Path, data_dir: Path, extra_roots: list[Path] | None = None) -> list[dict]:
    """Genuine llama-server programs found by scanning: [{"path", "source"}], one per real file."""
    found, seen = [], set()
    for p in scan([*(extra_roots or []), *default_roots(home, data_dir)]):
        try:
            real = str(p.resolve())
        except OSError:
            continue
        if real in seen or not is_genuine(p):
            continue
        seen.add(real)
        found.append({"path": str(p), "real": real, "source": label(p, home)})
    return found
