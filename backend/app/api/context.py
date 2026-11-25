"""The objects every router shares, built once per application.

Nothing here is a route. `ApiContext` creates the database, the managers, the agent and the model engines in the order the application needs them, and offers the
small helpers that several routers use (`runtime`, `proj_or_404`, `record_state`, ...). Each router in `routes/` receives it as `router(ctx)` and binds only the
names it uses, so a router's first lines say exactly what it depends on.
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, Request

from ..agents.engine import AgentEngine
from ..agents.mission import MissionRunner
from ..agents.router import AUTO, AutoRouter
from ..core import config
from ..core.database import Database
from ..modelconnectors import registry as eng
from ..modelconnectors.downloads import DownloadManager
from ..modelconnectors.llamacpp.provider import LlamaCppProvider
from ..modelconnectors.ollama.provider import OllamaProvider
from ..modelhub import arena
from ..system.about import about as about_info
from ..toolchain.engine import ToolEngine
from ..toolchain.permissions import PermissionManager, parse_custom
from ..toolchain.runner import Runner
from ..workspace.projects import ProjectError, ProjectManager
from ..workspace.runtime import SessionRuntime
from ..workspace.sessions import SessionManager
from ..workspace.state import StateStore
from ..workspace.zeusdir import ZeusFiles


class ApiContext:
    def __init__(self, app: FastAPI, db_path=None):
        self.app = app
        self.db = db = Database(db_path or config.DB_PATH)
        self.projects, self.sessions = ProjectManager(db), SessionManager(db)
        self.runner, self.tools = Runner(), ToolEngine()
        self.downloads = DownloadManager(db)
        self.zfiles = zfiles = ZeusFiles(db)
        self.restored = zfiles.restore_home()          # settings / projects / sessions found in ~/.zeus and <project>/.zeus
        self.sessions.files = zfiles
        db.on_recreated = zfiles.restore_home
        self.llama = LlamaCppProvider(db)
        self.engines = eng.Engines(db, OllamaProvider(db.get_setting("ollama_url", config.DEFAULT_OLLAMA)), self.llama)
        self.state_store = StateStore()
        self.llama_run: dict = {"task": None, "error": None}
        self.runtimes: dict[int, SessionRuntime] = {}
        self.agent = AgentEngine(db, eng.ProviderProxy(self.engines), self.tools)        # follows the engine of the conversation that is running
        self.agent.on_change = zfiles.schedule
        self.auto = AutoRouter(db, lambda: self.engines.ollama)                          # Auto mode picks among Ollama models only
        self.agent.router = self.auto
        self.mission_runner = MissionRunner(db, self.agent)
        app.state.db, app.state.runtimes, app.state.projects, app.state.zfiles, app.state.downloads = db, self.runtimes, self.projects, zfiles, self.downloads
        zfiles.write_home()

    # ------------------------------------------------------------------ helpers used by several routers
    def provider(self) -> OllamaProvider:
        """The Ollama provider (the /api/ollama/* endpoints are Ollama-specific). Other code uses `engines.get(...)`."""
        return self.engines.ollama

    @staticmethod
    def client_of(request: Request) -> str:
        return "cli" if request.headers.get("x-zeus-client") == "cli" else "gui"

    def record_state(self, sid: int | None = None, client: str | None = None) -> None:
        """Remember which conversation, project, engine and model was last used (~/.zeus/state.json)."""
        try:
            doc: dict = {"client": client} if client else {}
            doc["engine"] = self.engines.default()
            sess = self.sessions.get(sid) if sid else None
            if sess:
                proj = self.projects.get(sess["project_id"])
                name = self.engines.name_of(sess)
                doc.update({"engine": name, "model": sess["model"], "num_ctx": sess["num_ctx"], "permission_mode": proj["permission_mode"],
                            "project": {"path": proj["path"], "name": proj["name"]}, "session": {"uid": sess["uid"], "id": sess["id"], "title": sess["title"]}})
                if sess["model"] and sess["model"] != AUTO:
                    doc["models"] = {name: sess["model"]}
            self.state_store.write(doc)
        except Exception:  # noqa: BLE001 - remembering is a convenience; never break the request
            pass

    @staticmethod
    def project_perms(proj: dict) -> PermissionManager:
        return PermissionManager(proj["permission_mode"], parse_custom(proj["custom_permissions"]))

    def runtime(self, sid: int) -> SessionRuntime:
        if sid not in self.runtimes:
            s = self.sessions.get(sid)
            if not s:
                raise HTTPException(404, "unknown session")
            self.runtimes[sid] = SessionRuntime(sid, self.project_perms(self.projects.get(s["project_id"])))
        return self.runtimes[sid]

    def proj_or_404(self, pid: int) -> dict:
        try:
            return self.projects.get(pid)
        except ProjectError as e:
            raise HTTPException(404, str(e))

    async def arena_context(self, refresh: bool = False):
        """The Arena data and this machine's hardware profile (used by the benchmarks and by the Hugging Face file list)."""
        data = await arena.fetch(self.db, force=refresh)
        info = await asyncio.get_running_loop().run_in_executor(None, about_info, None)
        return data, arena.hardware_profile(info, data)
