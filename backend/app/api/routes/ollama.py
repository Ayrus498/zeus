"""Ollama models: status, installed models, the curated guide, the public library, pull / delete / unload."""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException

from ...modelconnectors import modelinfo
from ...modelconnectors.ollama import catalog as model_catalog
from ...modelconnectors.ollama import library as model_library
from ...modelconnectors.ollama.provider import OllamaError
from ...system import metrics
from ...system.hardware import system_memory
from ..schemas import ModelReq


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, downloads, provider = ctx.db, ctx.downloads, ctx.provider

    @r.get("/api/ollama/status")
    async def ollama_status():
        st = await provider().status()
        st["loaded"] = [m.get("name") for m in await provider().running()] if st["connected"] else []
        return st

    MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:@]{0,198}$")

    def valid_model(name: str) -> str:
        name = (name or "").strip()
        if not MODEL_RE.match(name):
            raise HTTPException(400, f"invalid model name: {name!r}")
        return name

    @r.get("/api/ollama/catalog")
    async def ollama_catalog():
        return {"purposes": model_catalog.PURPOSES, "library_url": model_catalog.LIBRARY_URL, "models": model_catalog.CATALOG,
                "note": "Curated snapshot. Sizes are approximate; see each model's page on ollama.com for the latest tags."}

    @r.get("/api/ollama/overview")
    async def ollama_overview():
        """One cheap call the Models panel polls: connection, installed + loaded models, downloads, hardware."""
        st = await provider().status()
        loop = asyncio.get_running_loop()
        mem = await loop.run_in_executor(None, system_memory)
        mi = metrics._meminfo()
        out = {**st, "installed": [], "loaded": [], "downloads": [d for d in downloads.list() if not d["model"].startswith("hf:")],
               "hardware": {"gpu": mem["gpu"], "ram_total": mi.get("MemTotal"), "ram_available": mem["ram_available"]}}
        if st["connected"]:
            try:
                out["installed"] = await provider().list_models()
            except OllamaError:
                pass
            out["loaded"] = metrics.describe_ps(await provider().running())
        return out

    @r.post("/api/ollama/pull")
    async def ollama_pull(b: ModelReq):
        name = valid_model(b.model)
        st = await provider().status()
        if not st["connected"]:
            raise HTTPException(503, f"Ollama is not reachable at {st['endpoint']}. Start it with `ollama serve`.")
        return downloads.start(provider(), name)

    @r.post("/api/ollama/downloads/{did}/cancel")
    async def ollama_cancel(did: int):
        if not await downloads.cancel(did):
            raise HTTPException(404, "no running download with that id")
        return {"ok": True}

    @r.delete("/api/ollama/downloads/{did}")
    async def ollama_dismiss(did: int):
        downloads.dismiss(did)
        return {"ok": True}

    @r.get("/api/ollama/library")
    async def ollama_library_list(refresh: bool = False):
        """Every model family in the public Ollama library (name, description, capabilities, sizes, pulls), cached for a few hours."""
        return await model_library.fetch_library(db, force=refresh)

    @r.get("/api/ollama/search")
    async def ollama_library_search(q: str = ""):
        """Search the public library (official and community models). Only the typed text is sent to ollama.com."""
        return await model_library.search(q)

    @r.get("/api/ollama/library/{family:path}")
    async def ollama_library(family: str, refresh: bool = False):
        """Latest public details (summary, README, tags, capabilities) for a model family ("ornith") or community model ("user/name")."""
        try:
            return await modelinfo.fetch(family if "/" in family else family.lower(), db, force=refresh)
        except ValueError:
            raise HTTPException(400, "invalid model family")

    @r.post("/api/ollama/delete")
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

    @r.post("/api/ollama/unload")
    async def ollama_unload(b: ModelReq):
        name = valid_model(b.model)
        try:
            await provider().unload(name)
        except OllamaError as e:
            raise HTTPException(502, f"Could not unload {name}: {e}")
        return {"ok": True}

    return r
