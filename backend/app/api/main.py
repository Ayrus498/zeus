"""FastAPI app: wiring only. Business logic lives in the sibling packages."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .. import config
from ..agent.engine import AgentEngine
from ..database.db import Database, now
from ..permissions.manager import (ACTIONS, DEFAULT_POLICY, MODES, PathViolation, PermissionManager, classify_command,
                                   parse_custom, resolve_in_workspace)
from ..projects.manager import ProjectError, ProjectManager
from .. import metrics
from ..about import about as about_info
from ..convert import service as convert_service
from ..agent.mission import MAX_DOC_BYTES, MissionRunner
from ..agent.router import AUTO, AutoRouter, classify, decide
from ..persistence.zeusdir import ZeusFiles
from ..providers import catalog as model_catalog
from ..providers.downloads import DownloadManager
from ..providers import modelinfo
from ..providers.ollama import OllamaError, OllamaProvider, context_warning, system_memory
from ..sessions.manager import SessionManager
from ..sessions.runtime import SessionRuntime
from ..terminal.runner import Runner
from ..permissions.manager import PathViolation
from ..tools.engine import ToolContext, ToolEngine, _resolve_write


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

class ModelReq(BaseModel):
    model: str


class UrlReq(BaseModel):
    url: str


class SaveFile(BaseModel):
    path: str
    content: str
    session_id: int | None = None
    overwrite: bool = False


class PinsUpdate(BaseModel):
    code: str | None = None
    think: str | None = None
    clear: list[str] = []


class PreviewReq(BaseModel):
    prompt: str
    last_model: str | None = None


class SettingsUpdate(BaseModel):
    ollama_url: str | None = None
    default_model: str | None = None
    default_num_ctx: int | None = None


def create_app(db_path=None) -> FastAPI:
    app = FastAPI(title="ZEUS backend", version=config.VERSION)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    db = Database(db_path or config.DB_PATH)
    projects, sessions = ProjectManager(db), SessionManager(db)
    runner, tools = Runner(), ToolEngine()
    downloads = DownloadManager(db)
    zfiles = ZeusFiles(db)
    restored = zfiles.restore_home()          # settings / projects / sessions found in ~/.zeus and <project>/.zeus
    sessions.files = zfiles
    db.on_recreated = zfiles.restore_home
    state = {"provider": OllamaProvider(db.get_setting("ollama_url", config.DEFAULT_OLLAMA))}
    runtimes: dict[int, SessionRuntime] = {}
    agent = AgentEngine(db, state["provider"], tools)
    agent.on_change = zfiles.schedule
    auto = AutoRouter(db, lambda: state["provider"])
    agent.router = auto
    mission_runner = MissionRunner(db, agent)
    app.state.db, app.state.runtimes, app.state.projects, app.state.zfiles = db, runtimes, projects, zfiles
    zfiles.write_home()

    @app.on_event("shutdown")
    def _flush_zeus_files():
        zfiles.flush()
        zfiles.write_home()

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
        return {"ok": True, "version": config.VERSION}

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
        zfiles.write_home()
        return await get_settings()

    @app.get("/api/ollama/status")
    async def ollama_status():
        st = await provider().status()
        st["loaded"] = [m.get("name") for m in await provider().running()] if st["connected"] else []
        return st

    # ---------- model library: catalog, overview, download / unload
    MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:@]{0,198}$")
    OPEN_OK = re.compile(r"https://(?:(?:www\.|docs\.)?ollama\.com|github\.com/ollama)(?:[/?#][^\s]*)?")

    def valid_model(name: str) -> str:
        name = (name or "").strip()
        if not MODEL_RE.match(name):
            raise HTTPException(400, f"invalid model name: {name!r}")
        return name

    @app.get("/api/ollama/catalog")
    async def ollama_catalog():
        return {"purposes": model_catalog.PURPOSES, "library_url": model_catalog.LIBRARY_URL, "models": model_catalog.CATALOG,
                "note": "Curated snapshot. Sizes are approximate; see each model's page on ollama.com for the latest tags."}

    @app.get("/api/ollama/overview")
    async def ollama_overview():
        """One cheap call the Models panel polls: connection, installed + loaded models, downloads, hardware."""
        st = await provider().status()
        loop = asyncio.get_running_loop()
        mem = await loop.run_in_executor(None, system_memory)
        mi = metrics._meminfo()
        out = {**st, "installed": [], "loaded": [], "downloads": downloads.list(),
               "hardware": {"gpu": mem["gpu"], "ram_total": mi.get("MemTotal"), "ram_available": mem["ram_available"]}}
        if st["connected"]:
            try:
                out["installed"] = await provider().list_models()
            except OllamaError:
                pass
            out["loaded"] = metrics.describe_ps(await provider().running())
        return out

    @app.post("/api/ollama/pull")
    async def ollama_pull(b: ModelReq):
        name = valid_model(b.model)
        st = await provider().status()
        if not st["connected"]:
            raise HTTPException(503, f"Ollama is not reachable at {st['endpoint']}. Start it with `ollama serve`.")
        return downloads.start(provider(), name)

    @app.post("/api/ollama/downloads/{did}/cancel")
    async def ollama_cancel(did: int):
        if not await downloads.cancel(did):
            raise HTTPException(404, "no running download with that id")
        return {"ok": True}

    @app.delete("/api/ollama/downloads/{did}")
    async def ollama_dismiss(did: int):
        downloads.dismiss(did)
        return {"ok": True}

    @app.get("/api/ollama/library/{family}")
    async def ollama_library(family: str, refresh: bool = False):
        """Latest public details (summary, README, tags, capabilities) for a model family from ollama.com."""
        try:
            return await modelinfo.fetch(family.lower(), db, force=refresh)
        except ValueError:
            raise HTTPException(400, "invalid model family")

    @app.post("/api/ollama/delete")
    async def ollama_delete(b: ModelReq):
        """Uninstall a model from disk. Refuses while it is downloading; frees it from memory first."""
        name = valid_model(b.model)
        if any(d["model"] == name and d["status"] in ("queued", "running") for d in downloads.list()):
            raise HTTPException(409, f"{name} is still downloading. Cancel the download first.")
        try:
            try:
                await provider().unload(name)
            except OllamaError:
                pass
            await provider().delete(name)
        except OllamaError as e:
            raise HTTPException(502, f"Could not uninstall {name}: {e}")
        return {"ok": True}

    @app.post("/api/ollama/unload")
    async def ollama_unload(b: ModelReq):
        name = valid_model(b.model)
        try:
            await provider().unload(name)
        except OllamaError as e:
            raise HTTPException(502, f"Could not unload {name}: {e}")
        return {"ok": True}

    @app.get("/api/auto/plan")
    async def auto_plan():
        """What Auto mode would use for coding vs thinking right now, with warnings and download suggestions."""
        try:
            return await auto.plan()
        except OllamaError as e:
            raise HTTPException(503, str(e))

    @app.post("/api/auto/preview")
    async def auto_preview(b: PreviewReq):
        """Dry run: which model would Auto pick for this prompt, and why. Nothing is loaded or changed."""
        try:
            profiles, gpu, ram = await auto.gather()
        except OllamaError as e:
            raise HTTPException(503, str(e))
        c = decide(b.prompt, profiles, gpu, ram, b.last_model, auto.pins())
        i = classify(b.prompt)
        return {"intent": i.role, "choice": None if not c else {"model": c.model, "role": c.role, "tools": c.tools, "reason": c.reason}}

    @app.put("/api/auto/pins")
    async def auto_pins(b: PinsUpdate):
        installed = {m["name"] for m in await provider().list_models()} if (await provider().status())["connected"] else set()
        for role in ("code", "think"):
            v = getattr(b, role)
            if v:
                if v not in installed:
                    raise HTTPException(400, f"{v} is not installed")
                db.set_setting(f"auto_{role}_model", v)
            elif role in b.clear:
                db.x("DELETE FROM settings WHERE key=?", (f"auto_{role}_model",))
        zfiles.write_home()
        return auto.pins()

    @app.post("/api/open-url")
    async def open_url(b: UrlReq):
        """Open an allow-listed documentation link in the user's default browser (the webview cannot open new windows)."""
        if not OPEN_OK.fullmatch(b.url or ""):
            raise HTTPException(400, "only Ollama documentation links can be opened from here")
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise HTTPException(501, "no browser opener found (install xdg-utils)")
        cmd = [opener, b.url] if opener.endswith("xdg-open") else [opener, "open", b.url]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True}

    sampler = metrics.Sampler()

    @app.get("/api/about")
    async def get_about():
        """App info plus real device readings (CPU, GPU/VRAM, RAM) for Settings → About."""
        st = await provider().status()
        return await asyncio.get_running_loop().run_in_executor(None, about_info, st.get("version") if st["connected"] else None)

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
            p = projects.add(b.path)
        except ProjectError as e:
            raise HTTPException(400, str(e))
        zfiles.restore_project(p)      # bring back conversations kept in <project>/.zeus
        zfiles.write_home()
        return p

    @app.post("/api/projects/create")
    async def create_project(b: CreateProject):
        try:
            p = projects.create(b.parent, b.name)
        except ProjectError as e:
            raise HTTPException(400, str(e))
        zfiles.write_home()
        return p

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
        zfiles.flush()          # make sure the project's .zeus/ is current; it stays on disk and restores if re-added
        projects.remove(pid)   # DB rows only; files on disk are never touched
        zfiles.write_home()
        return {"ok": True}

    @app.post("/api/projects/{pid}/open")
    async def open_project(pid: int):
        proj = proj_or_404(pid)
        projects.touch(pid)
        zfiles.restore_project(proj)
        return projects.get(pid)

    @app.post("/api/projects/{pid}/files")
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

    async def start_mission(sid: int, m: dict):
        rt = runtime(sid)
        s = sessions.get(sid)
        if rt.running:
            await rt.emit({"type": "error", "message": "A run is already in progress for this session."})
            return
        if not s["model"]:
            await rt.emit({"type": "error", "message": "No model selected. Choose a model first."})
            return
        name = re.sub(r"[^\w .()+-]", "_", os.path.basename(str(m.get("name") or "mission.md")))[:120] or "mission.md"
        content = str(m.get("content") or "")
        if not name.lower().endswith((".md", ".markdown")):
            await rt.emit({"type": "error", "message": "Only Markdown (.md) documents can be attached."})
            return
        if not content.strip() or len(content.encode("utf-8")) > MAX_DOC_BYTES:
            await rt.emit({"type": "error", "message": f"The document is empty or larger than {MAX_DOC_BYTES // 1000} KB."})
            return
        proj = projects.get(s["project_id"])
        rt.perms.mode, rt.perms.custom = proj["permission_mode"], parse_custom(proj["custom_permissions"])
        ctx = ToolContext(project=proj, session_id=sid, agent="coder", perms=rt.perms, db=db, runner=runner,
                          emit=rt.emit, request=rt.request_permission)

        async def job():
            try:
                await mission_runner.run(ctx, sessions.get(sid), name, content, str(m.get("note") or "")[:4000],
                                         int(m.get("max_rounds") or 5), "think" if m.get("verifier") == "think" else "same")
            except asyncio.CancelledError:
                rt.reject_all()
                await rt.emit({"type": "run_end", "status": "stopped"})
            except Exception:
                pass  # already reported via 'error' event

        rt.task = asyncio.create_task(job())

    @app.get("/api/convert/formats")
    async def convert_formats():
        """Which attachment types ZEUS can turn into Markdown, and whether MarkItDown is installed."""
        return convert_service.info()

    @app.post("/api/convert")
    async def convert_file(request: Request, filename: str):
        """Convert an uploaded document (raw request body) to Markdown with MarkItDown, in an isolated subprocess.
        The result is only returned to the caller; nothing is stored or written into any project."""
        limit = convert_service.MAX_INPUT
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"The file is larger than {limit // 2**20} MB.")
        chunks, size = [], 0
        async for c in request.stream():
            size += len(c)
            if size > limit:
                raise HTTPException(413, f"The file is larger than {limit // 2**20} MB.")
            chunks.append(c)
        try:
            return await convert_service.convert_bytes(b"".join(chunks), filename)
        except convert_service.ConvertError as e:
            raise HTTPException(e.status, e.message)

    @app.get("/api/sessions/{sid}/missions")
    async def session_missions(sid: int):
        if not sessions.get(sid):
            raise HTTPException(404, "unknown session")
        rows = db.q("SELECT * FROM missions WHERE session_id=? ORDER BY id", (sid,))
        for r in rows:
            for k in ("criteria", "plan", "log"):
                try:
                    r[k] = json.loads(r[k])
                except Exception:
                    r[k] = [] if k != "plan" else {}
        return rows

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
                elif t == "mission":
                    await start_mission(sid, m)
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
