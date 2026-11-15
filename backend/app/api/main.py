"""FastAPI app: wiring only. Business logic lives in the sibling packages."""
from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .. import config
from ..agent.engine import AgentEngine
from ..database.db import Database, now
from ..permissions.manager import (ACTIONS, DEFAULT_POLICY, MODES, PathViolation, PermissionManager, classify_command,
                                   parse_custom, resolve_in_workspace)
from ..projects.manager import ProjectError, ProjectManager
from .. import metrics
from ..providers.ollama import OllamaError, OllamaProvider, context_warning
from ..sessions.manager import SessionManager
from ..sessions.runtime import SessionRuntime
from ..terminal.runner import Runner
from ..tools.engine import ToolContext, ToolEngine


class AddProject(BaseModel):
    path: str

class CreateProject(BaseModel):
    parent: str
    name: str

class PermUpdate(BaseModel):
    mode: str
    custom: dict | None = None

class SessionCreate(BaseModel):
    project_id: int
    title: str | None = None
    model: str | None = None
    num_ctx: int | None = None

class SessionPatch(BaseModel):
    title: str | None = None
    model: str | None = None
    num_ctx: int | None = None

class SettingsUpdate(BaseModel):
    ollama_url: str | None = None
    default_model: str | None = None
    default_num_ctx: int | None = None


def create_app(db_path=None) -> FastAPI:
    app = FastAPI(title="ZEUS backend", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    db = Database(db_path or config.DB_PATH)
    projects, sessions = ProjectManager(db), SessionManager(db)
    runner, tools = Runner(), ToolEngine()
    state = {"provider": OllamaProvider(db.get_setting("ollama_url", config.DEFAULT_OLLAMA))}
    runtimes: dict[int, SessionRuntime] = {}
    agent = AgentEngine(db, state["provider"], tools)
    app.state.db, app.state.runtimes, app.state.projects = db, runtimes, projects

    def provider() -> OllamaProvider:
        return state["provider"]

    def project_perms(proj: dict) -> PermissionManager:
        return PermissionManager(proj["permission_mode"], parse_custom(proj["custom_permissions"]))

    def runtime(sid: int) -> SessionRuntime:
        if sid not in runtimes:
            s = sessions.get(sid)
            if not s:
                raise HTTPException(404, "unknown session")
            runtimes[sid] = SessionRuntime(sid, project_perms(projects.get(s["project_id"])))
        return runtimes[sid]

    def proj_or_404(pid: int) -> dict:
        try:
            return projects.get(pid)
        except ProjectError as e:
            raise HTTPException(404, str(e))

    # ---------- health / settings / ollama
    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": "0.1.0"}

    @app.get("/api/settings")
    async def get_settings():
        return {"home": str(Path.home()), "ollama_url": provider().base_url, "default_model": db.get_setting("default_model"),
                "default_num_ctx": int(db.get_setting("default_num_ctx", "16384")),
                "modes": {k: v for k, v in MODES.items()}, "actions": ACTIONS}

    @app.put("/api/settings")
    async def put_settings(s: SettingsUpdate):
        if s.ollama_url:
            url = s.ollama_url.strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise HTTPException(400, "endpoint must start with http:// or https://")
            db.set_setting("ollama_url", url)
            state["provider"] = OllamaProvider(url)
            agent.provider = state["provider"]
        if s.default_model:
            db.set_setting("default_model", s.default_model)
        if s.default_num_ctx:
            db.set_setting("default_num_ctx", str(s.default_num_ctx))
        return await get_settings()

    @app.get("/api/ollama/status")
    async def ollama_status():
        st = await provider().status()
        st["loaded"] = [m.get("name") for m in await provider().running()] if st["connected"] else []
        return st

    sampler = metrics.Sampler()

    @app.get("/api/metrics")
    async def get_metrics():
        """Live CPU/RAM/GPU readings plus `ollama ps` (what is loaded, and how it is split between GPU and CPU)."""
        out = await asyncio.get_running_loop().run_in_executor(None, sampler.sample)
        st = await provider().status()
        out["ollama"] = {"connected": st["connected"], "version": st.get("version"),
                         "loaded": metrics.describe_ps(await provider().running()) if st["connected"] else []}
        return out

    @app.get("/api/models")
    async def models():
        try:
            return await provider().list_models()
        except OllamaError as e:
            raise HTTPException(503, str(e))

    @app.get("/api/models/info")
    async def model_info(name: str, num_ctx: int | None = None):
        try:
            meta = await provider().show(name)
            size = next((m["size"] for m in await provider().list_models() if m["name"] == name), 0)
        except OllamaError as e:
            raise HTTPException(404 if "not installed" in str(e) else 503, str(e))
        meta["size"] = size
        meta["tools_supported"] = ("tools" in meta["capabilities"]) if meta["capabilities"] else None
        if num_ctx:
            meta["context_check"] = context_warning(meta, size, num_ctx)
        return meta

    # ---------- projects
    @app.get("/api/projects")
    async def list_projects():
        return projects.list()

    @app.post("/api/projects")
    async def add_project(b: AddProject):
        try:
            return projects.add(b.path)
        except ProjectError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/projects/create")
    async def create_project(b: CreateProject):
        try:
            return projects.create(b.parent, b.name)
        except ProjectError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/pick-folder")
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

    @app.delete("/api/projects/{pid}")
    async def remove_project(pid: int):
        proj_or_404(pid)
        projects.remove(pid)   # DB rows only; files on disk are never touched
        return {"ok": True}

    @app.post("/api/projects/{pid}/open")
    async def open_project(pid: int):
        proj_or_404(pid)
        projects.touch(pid)
        return projects.get(pid)

    @app.get("/api/projects/{pid}/tree")
    async def tree(pid: int, rel: str = ""):
        proj_or_404(pid)
        try:
            return projects.tree(pid, rel)
        except ProjectError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/projects/{pid}/file")
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

    @app.get("/api/projects/{pid}/git")
    async def git_status(pid: int):
        proj_or_404(pid)
        return projects.git_status(pid)

    @app.get("/api/projects/{pid}/changes")
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

    @app.get("/api/projects/{pid}/changes/diff")
    async def change_diff(pid: int, path: str, session_id: int | None = None):
        proj_or_404(pid)
        rows = db.q("SELECT op,before,after FROM file_changes WHERE project_id=? AND path=?"
                    + (" AND session_id=?" if session_id else "") + " ORDER BY id",
                    (pid, path, session_id) if session_id else (pid, path))
        if not rows:
            raise HTTPException(404, "no recorded changes")
        return {"path": path, "before": rows[0]["before"] or "", "after": rows[-1]["after"] or "",
                "status": "created" if rows[0]["op"] == "create" else "deleted" if rows[-1]["op"] == "delete" else "modified"}

    @app.put("/api/projects/{pid}/permissions")
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

    @app.get("/api/projects/{pid}/permissions")
    async def get_permissions(pid: int):
        proj = proj_or_404(pid)
        pm = project_perms(proj)
        return {"mode": pm.mode, "custom": pm.custom, "policy": pm.policy_table()}

    # ---------- sessions
    @app.get("/api/sessions")
    async def list_sessions(project_id: int | None = None):
        return sessions.list(project_id)

    @app.post("/api/sessions")
    async def create_session(b: SessionCreate):
        proj_or_404(b.project_id)
        model = b.model or db.get_setting("default_model")
        ctx = b.num_ctx or int(db.get_setting("default_num_ctx", "16384"))
        return sessions.create(b.project_id, b.title or "New session", model, ctx)

    @app.patch("/api/sessions/{sid}")
    async def patch_session(sid: int, b: SessionPatch):
        if not sessions.get(sid):
            raise HTTPException(404, "unknown session")
        return sessions.update(sid, **b.model_dump())

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: int):
        rt = runtimes.pop(sid, None)
        if rt and rt.task:
            rt.reject_all(); rt.task.cancel()
        sessions.delete(sid)
        return {"ok": True}

    @app.get("/api/sessions/{sid}/messages")
    async def session_messages(sid: int):
        if not sessions.get(sid):
            raise HTTPException(404, "unknown session")
        return sessions.messages(sid)

    @app.get("/api/executions")
    async def executions(project_id: int, session_id: int | None = None):
        q = "SELECT id,session_id,command,started,finished,exit_code,status,output FROM executions WHERE project_id=?"
        a: list = [project_id]
        if session_id:
            q += " AND session_id=?"; a.append(session_id)
        return db.q(q + " ORDER BY id DESC LIMIT 100", a)

    # ---------- agent websocket
    async def start_run(sid: int, text: str):
        rt = runtime(sid)
        s = sessions.get(sid)
        if rt.running:
            await rt.emit({"type": "error", "message": "A run is already in progress for this session."})
            return
        if not s["model"]:
            await rt.emit({"type": "error", "message": "No model selected. Choose a model first."})
            return
        proj = projects.get(s["project_id"])
        rt.perms.mode, rt.perms.custom = proj["permission_mode"], parse_custom(proj["custom_permissions"])
        sessions.auto_title(sid, text)
        ctx = ToolContext(project=proj, session_id=sid, agent="coder", perms=rt.perms, db=db, runner=runner,
                          emit=rt.emit, request=rt.request_permission)

        async def job():
            try:
                await agent.run(ctx, sessions.get(sid), text)
            except asyncio.CancelledError:
                rt.reject_all()
                await rt.emit({"type": "run_end", "status": "stopped"})
            except Exception:
                pass  # already reported via 'error' event

        rt.task = asyncio.create_task(job())

    @app.websocket("/ws/sessions/{sid}")
    async def ws_session(ws: WebSocket, sid: int):
        await ws.accept()
        if not sessions.get(sid):
            await ws.close(4404)
            return
        rt = runtime(sid)
        rt.subscribers.add(ws)
        await ws.send_json({"type": "state", "session_id": sid, "running": rt.running,
                            "pending": [r for _, r in rt.pending.values()]})
        try:
            while True:
                m = await ws.receive_json()
                t = m.get("type")
                if t == "prompt" and str(m.get("content", "")).strip():
                    await start_run(sid, m["content"].strip())
                elif t == "stop" and rt.running:
                    rt.reject_all()
                    runner.cancel_all()
                    rt.task.cancel()
                elif t == "permission_response":
                    rt.respond(m.get("id", ""), m.get("decision", "reject"))
        except WebSocketDisconnect:
            pass
        finally:
            rt.subscribers.discard(ws)

    # ---------- user terminal websocket
    @app.websocket("/ws/terminal/{pid}")
    async def ws_terminal(ws: WebSocket, pid: int):
        await ws.accept()
        try:
            proj = projects.get(pid)
        except ProjectError:
            await ws.close(4404)
            return
        lock = asyncio.Lock()

        async def send(ev):
            try:
                async with lock:
                    await ws.send_json(ev)
            except Exception:
                pass

        tasks: set[asyncio.Task] = set()
        try:
            while True:
                m = await ws.receive_json()
                if m.get("type") == "run":
                    cmd = str(m.get("command", "")).strip()
                    proj = projects.get(pid)
                    pm = project_perms(proj)
                    action = classify_command(cmd)
                    if not cmd:
                        continue
                    if action == "blocked" or pm.policy_for("run_command") == "deny":
                        await send({"type": "command_denied", "command": cmd,
                                    "reason": "blocked command" if action == "blocked" else "read-only mode forbids commands"})
                        continue
                    # A command typed by the user in the terminal panel is their own explicit action.
                    ctx = ToolContext(project=proj, session_id=None, agent="user", perms=pm, db=db, runner=runner, emit=send, request=None)
                    t = asyncio.create_task(tools.run_approved_command(ctx, cmd, int(m.get("timeout") or config.COMMAND_TIMEOUT)))
                    tasks.add(t); t.add_done_callback(tasks.discard)
                elif m.get("type") == "cancel":
                    runner.cancel(m.get("run_id", ""))
        except WebSocketDisconnect:
            pass
        finally:
            for t in tasks:
                t.cancel()

    return app




def main():
    import uvicorn
    uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
