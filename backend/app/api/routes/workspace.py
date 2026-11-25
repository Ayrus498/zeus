"""Projects: registry, files, tree, git, change history, permissions, the folder picker."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...core import config
from ...core.database import now
from ...toolchain.engine import _resolve_write
from ...toolchain.permissions import ACTIONS, MODES, PathViolation, PermissionManager, resolve_in_workspace
from ...toolchain.permissions import PathViolation
from ...workspace.projects import ProjectError
from ..schemas import AddProject, CreateProject, PermUpdate, SaveFile


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, projects, sessions, zfiles, runtimes, project_perms, proj_or_404 = ctx.db, ctx.projects, ctx.sessions, ctx.zfiles, ctx.runtimes, ctx.project_perms, ctx.proj_or_404

    @r.get("/api/projects")
    async def list_projects():
        return projects.list()

    @r.post("/api/projects")
    async def add_project(b: AddProject):
        try:
            p = projects.add(b.path)
        except ProjectError as e:
            raise HTTPException(400, str(e))
        zfiles.restore_project(p)      # bring back conversations kept in <project>/.zeus
        zfiles.write_home()
        return p

    @r.post("/api/projects/create")
    async def create_project(b: CreateProject):
        try:
            p = projects.create(b.parent, b.name)
        except ProjectError as e:
            raise HTTPException(400, str(e))
        zfiles.write_home()
        return p

    @r.post("/api/pick-folder")
    async def pick_folder():
        """Native Linux folder picker (zenity/kdialog). Tauri's own dialog is used when available."""
        for cmd in (["zenity", "--file-selection", "--directory", "--title=Select project folder"],
                    ["kdialog", "--getexistingdirectory", str(Path.home())]):
            try:
                r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
            except FileNotFoundError:
                continue
            path = r.stdout.strip()
            return {"path": path or None}
        raise HTTPException(501, "no native folder picker (install zenity)")

    @r.delete("/api/projects/{pid}")
    async def remove_project(pid: int):
        proj_or_404(pid)
        zfiles.flush()          # make sure the project's .zeus/ is current; it stays on disk and restores if re-added
        projects.remove(pid)   # DB rows only; files on disk are never touched
        zfiles.write_home()
        return {"ok": True}

    @r.post("/api/projects/{pid}/open")
    async def open_project(pid: int):
        proj = proj_or_404(pid)
        projects.touch(pid)
        zfiles.restore_project(proj)
        return projects.get(pid)

    @r.post("/api/projects/{pid}/files")
    async def save_file(pid: int, b: SaveFile):
        """User-initiated 'Save as file' for a code block shown in chat. The click is the user's explicit action, so no
        approval prompt - but the same safety rules apply: inside the project only, never in .zeus, not in read-only mode."""
        proj = proj_or_404(pid)
        if proj["permission_mode"] == "read_only":
            raise HTTPException(403, "This project is in read-only mode. Change the permission mode to save files.")
        if len(b.content.encode("utf-8")) > config.MAX_FILE_BYTES:
            raise HTTPException(413, "Content is too large to save from here.")
        try:
            p = _resolve_write(proj["path"], b.path)
        except PathViolation as e:
            raise HTTPException(400, str(e))
        rel = str(p.relative_to(Path(proj["path"]).resolve()))
        if p.is_dir():
            raise HTTPException(400, f"{rel} is a folder")
        before = p.read_text("utf-8", errors="replace") if p.exists() else None
        if before is not None and not b.overwrite:
            raise HTTPException(409, f"{rel} already exists")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(b.content, encoding="utf-8")
        db.x("INSERT INTO file_changes(session_id,project_id,path,op,before,after,created) VALUES(?,?,?,?,?,?,?)",
             (b.session_id, pid, rel, "edit" if before is not None else "create", before, b.content, now()))
        return {"ok": True, "path": rel, "bytes": len(b.content.encode("utf-8")), "overwritten": before is not None}

    @r.get("/api/projects/{pid}/tree")
    async def tree(pid: int, rel: str = ""):
        proj_or_404(pid)
        try:
            return projects.tree(pid, rel)
        except ProjectError as e:
            raise HTTPException(400, str(e))

    @r.get("/api/projects/{pid}/file")
    async def read_file(pid: int, path: str):
        proj = proj_or_404(pid)
        try:
            p = resolve_in_workspace(proj["path"], path)
        except PathViolation as e:
            raise HTTPException(403, str(e))
        if not p.is_file():
            raise HTTPException(404, "not a file")
        if p.stat().st_size > config.MAX_FILE_BYTES:
            raise HTTPException(413, "file too large")
        data = p.read_bytes()
        if b"\x00" in data[:4096]:
            raise HTTPException(415, "binary file")
        return {"path": path, "content": data.decode(errors="replace")}

    @r.get("/api/projects/{pid}/git")
    async def git_status(pid: int):
        proj_or_404(pid)
        return projects.git_status(pid)

    @r.get("/api/projects/{pid}/changes")
    async def changes(pid: int, session_id: int | None = None):
        """Net change per path made by the agent: created / modified / deleted."""
        proj_or_404(pid)
        rows = db.q("SELECT id,path,op,before,after FROM file_changes WHERE project_id=?"
                    + (" AND session_id=?" if session_id else "") + " ORDER BY id",
                    (pid, session_id) if session_id else (pid,))
        agg: dict[str, dict] = {}
        for r in rows:
            a = agg.setdefault(r["path"], {"path": r["path"], "first_op": r["op"], "before": r["before"], "last_op": r["op"], "after": r["after"]})
            a["last_op"], a["after"] = r["op"], r["after"]
        out = []
        for a in agg.values():
            if a["first_op"] == "create" and a["last_op"] == "delete":
                continue
            status = "created" if a["first_op"] == "create" else "deleted" if a["last_op"] == "delete" else "modified"
            out.append({"path": a["path"], "status": status})
        return out

    @r.get("/api/projects/{pid}/changes/diff")
    async def change_diff(pid: int, path: str, session_id: int | None = None):
        proj_or_404(pid)
        rows = db.q("SELECT op,before,after FROM file_changes WHERE project_id=? AND path=?"
                    + (" AND session_id=?" if session_id else "") + " ORDER BY id",
                    (pid, path, session_id) if session_id else (pid, path))
        if not rows:
            raise HTTPException(404, "no recorded changes")
        return {"path": path, "before": rows[0]["before"] or "", "after": rows[-1]["after"] or "",
                "status": "created" if rows[0]["op"] == "create" else "deleted" if rows[-1]["op"] == "delete" else "modified"}

    @r.put("/api/projects/{pid}/permissions")
    async def set_permissions(pid: int, b: PermUpdate):
        proj_or_404(pid)
        if b.mode not in (*MODES, "custom"):
            raise HTTPException(400, "unknown mode")
        custom = {k: v for k, v in (b.custom or {}).items() if k in ACTIONS and v in ("allow", "ask", "deny")}
        db.x("UPDATE projects SET permission_mode=?, custom_permissions=? WHERE id=?", (b.mode, json.dumps(custom), pid))
        for sid, rt in runtimes.items():       # takes effect immediately, even mid-run
            s = sessions.get(sid)
            if s and s["project_id"] == pid:
                rt.perms.mode, rt.perms.custom = b.mode, custom
        return {"mode": b.mode, "custom": custom, "policy": PermissionManager(b.mode, custom).policy_table()}

    @r.get("/api/projects/{pid}/permissions")
    async def get_permissions(pid: int):
        proj = proj_or_404(pid)
        pm = project_perms(proj)
        return {"mode": pm.mode, "custom": pm.custom, "policy": pm.policy_table()}

    return r
