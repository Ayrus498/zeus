from __future__ import annotations
"""Projects are references to real folders; Zeus-Agent never copies project content."""
import os
import subprocess
from pathlib import Path

from ..database.db import Database, now

IGNORE = {".zeus", ".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache",
          "target", "dist", "build", ".idea", ".next", ".cache"}


class ProjectError(Exception):
    pass


class ProjectManager:
    def __init__(self, db: Database):
        self.db = db

    def validate(self, path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise ProjectError("path must be absolute")
        if not p.exists():
            raise ProjectError(f"folder does not exist: {p}")
        if not p.is_dir():
            raise ProjectError(f"not a directory: {p}")
        if not os.access(p, os.R_OK | os.X_OK):
            raise ProjectError(f"no read access to {p}")
        return p.resolve()

    def access_info(self, p: Path) -> dict:
        return {"readable": os.access(p, os.R_OK), "writable": os.access(p, os.W_OK),
                "is_git": (p / ".git").exists()}

    def add(self, path: str) -> dict:
        p = self.validate(path)
        row = self.db.one("SELECT * FROM projects WHERE path=?", (str(p),))
        if row:
            self.db.x("UPDATE projects SET last_opened=? WHERE id=?", (now(), row["id"]))
            return self.get(row["id"])
        pid = self.db.x("INSERT INTO projects(name,path,last_opened) VALUES(?,?,?)", (p.name, str(p), now()))
        return self.get(pid)

    def create(self, parent: str, name: str) -> dict:
        if not name or "/" in name or name in (".", "..") or "\x00" in name:
            raise ProjectError("invalid project name")
        par = self.validate(parent)
        if not os.access(par, os.W_OK):
            raise ProjectError(f"no write access to {par}")
        target = par / name
        if target.exists():
            raise ProjectError(f"already exists: {target}")
        target.mkdir()
        return self.add(str(target))

    def list(self) -> list[dict]:
        rows = self.db.q("SELECT * FROM projects ORDER BY last_opened DESC")
        for r in rows:
            r["exists"] = Path(r["path"]).is_dir()
        return rows

    def get(self, pid: int) -> dict:
        r = self.db.one("SELECT * FROM projects WHERE id=?", (pid,))
        if not r:
            raise ProjectError("unknown project")
        r.update(self.access_info(Path(r["path"])) if Path(r["path"]).is_dir() else {"readable": False, "writable": False, "is_git": False})
        return r

    def touch(self, pid: int):
        self.db.x("UPDATE projects SET last_opened=? WHERE id=?", (now(), pid))

    def remove(self, pid: int):
        """Forget a project (sessions included). Never touches files on disk."""
        self.db.x("DELETE FROM projects WHERE id=?", (pid,))

    def tree(self, pid: int, rel: str = "") -> list[dict]:
        from ..permissions.manager import resolve_in_workspace, PathViolation
        proj = self.get(pid)
        root = Path(proj["path"])
        try:
            base = resolve_in_workspace(root, rel) if rel else root.resolve()
        except PathViolation as e:
            raise ProjectError(str(e))
        if not base.is_dir():
            raise ProjectError("not a directory")
        out = []
        for e in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if e.name in IGNORE:
                continue
            out.append({"name": e.name, "path": str(e.relative_to(root.resolve())),
                        "is_dir": e.is_dir() and not e.is_symlink(), "symlink": e.is_symlink()})
        return out

    def git_status(self, pid: int) -> dict:
        root = self.get(pid)["path"]
        if not (Path(root) / ".git").exists():
            return {"is_git": False, "files": []}
        r = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=20)
        return {"is_git": True, "files": [{"status": l[:2].strip(), "path": l[3:]} for l in r.stdout.splitlines()]}
