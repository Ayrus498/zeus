"""Reset to a fresh installation."""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, HTTPException, Request

from ...core import config
from ...maintenance import reset as zreset
from ...modelconnectors.llamacpp.provider import managed_dir
from ...modelconnectors.ollama import library as model_library
from ...modelhub import arena
from ..schemas import ResetBody


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, downloads, zfiles, engines, runtimes, agent = ctx.db, ctx.downloads, ctx.zfiles, ctx.engines, ctx.runtimes, ctx.agent

    LOCAL_ORIGIN = re.compile(r"^(?:tauri://localhost|https?://(?:tauri\.localhost|localhost|127\.0\.0\.1|\[::1\])(?::\d+)?)$")

    LOCAL_HOST = re.compile(r"^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$")

    def require_local_caller(request: Request):
        """A web page on the internet must never be able to trigger a reset through the browser: the request has to come from the app itself
        (its own origins), a local script, or the terminal app (no Origin header), addressed to this machine (Host check against DNS rebinding)."""
        origin, host = request.headers.get("origin"), request.headers.get("host", "")
        if (origin is not None and not LOCAL_ORIGIN.match(origin)) or not LOCAL_HOST.match(host):
            raise HTTPException(403, "this action is only available from the Zeus-Agent app on this computer")

    def reset_locations():
        projs = [r["path"] for r in db.q("SELECT path FROM projects")]
        try:
            doc = json.loads((zfiles.home / "settings.json").read_text("utf-8"))
            projs += [p["path"] for p in doc.get("projects", []) if isinstance(p, dict) and isinstance(p.get("path"), str)]
        except (OSError, ValueError):
            pass
        return zfiles.home, config.DATA_DIR, managed_dir(), list(dict.fromkeys(projs))

    @r.get("/api/reset")
    async def reset_preview(request: Request):
        """What a reset would remove, item by item, with sizes. Removes nothing."""
        require_local_caller(request)
        home, data_dir, models_dir, projs = reset_locations()
        return {"confirm": zreset.CONFIRM, "items": zreset.plan(db, str(config.DB_PATH), home, data_dir, models_dir, projs),
                "keeps": ["Your project files (only each project's .zeus history and memory folder can be removed)", "Ollama and every model installed in it",
                          "GGUF models in folders you keep yourself", "The installed program (remove it with: sudo apt remove zeus)"]}

    @r.post("/api/reset")
    async def reset_everything(b: ResetBody, request: Request):
        require_local_caller(request)
        if b.confirm != zreset.CONFIRM:
            raise HTTPException(400, f"type {zreset.CONFIRM} to confirm")
        ids = list(dict.fromkeys(b.items))
        if not ids or any(i not in zreset.ITEMS for i in ids):
            raise HTTPException(400, "choose at least one known item: " + ", ".join(zreset.ITEMS))
        home, data_dir, models_dir, projs = reset_locations()
        # 1. stop everything that could write while (or after) the files go
        for rt in list(runtimes.values()):
            if rt.task and not rt.task.done():
                rt.reject_all()
                rt.task.cancel()
        for t in list(downloads.tasks.values()):
            t.cancel()
        await asyncio.sleep(0.2)
        try:
            await engines.llama.shutdown()
        except Exception:  # noqa: BLE001
            pass
        zfiles.cancel_pending()
        # 2. remove what was chosen
        report = zreset.run(db, str(config.DB_PATH), ids, home, data_dir, models_dir, projs)
        # 3. forget everything held in memory so the running app is a fresh one
        runtimes.clear()
        downloads.tasks.clear()
        agent._meta = None
        arena._mem.clear()
        model_library._mem.clear()
        return {**report, "reload": True}

    return r
