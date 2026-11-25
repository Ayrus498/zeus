"""The engines (Ollama / llama.cpp): choosing one, any engine's models and model details, the llama.cpp server, Hugging Face search and downloads."""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request

from ...modelconnectors import registry as eng
from ...modelconnectors.base import ConnectorError
from ...modelconnectors.llamacpp.provider import HF_FILE_RE, HF_RE, LlamaCppError, managed_dir
from ...modelconnectors.ollama.provider import OllamaError
from ...modelhub import arena
from ...system.hardware import context_warning, system_memory
from ..schemas import EngineReq, LlamaDownload, LlamaModelReq


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, sessions, downloads, zfiles, llama, engines, llama_run, provider, client_of, record_state, arena_context = ctx.db, ctx.sessions, ctx.downloads, ctx.zfiles, ctx.llama, ctx.engines, ctx.llama_run, ctx.provider, ctx.client_of, ctx.record_state, ctx.arena_context

    async def engine_status() -> dict:
        o_st = await provider().status()
        o_models = 0
        if o_st["connected"]:
            try:
                o_models = len(await provider().list_models())
            except OllamaError:
                pass
        l_st = await llama.status()
        l_models = len(await llama.list_models())
        mem = await asyncio.get_running_loop().run_in_executor(None, system_memory)
        rec = eng.recommend(o_st, l_st, {"ollama": o_models, "llamacpp": l_models}, mem)
        return {"default": engines.default(), "recommendation": rec, "help": eng.help_info(),
                "hardware": {"gpu": mem["gpu"], "ram_available": mem["ram_available"]},
                "engines": [
                    {"id": "ollama", "label": "Ollama", "available": o_st["connected"], "version": o_st.get("version"), "endpoint": o_st.get("endpoint"),
                     "error": o_st.get("error"), "models": o_models, "last_model": engines.last_model("ollama")},
                    {"id": "llamacpp", "label": "llama.cpp", "available": l_st["connected"], "version": l_st.get("version"), "binary": l_st.get("binary"),
                     "error": l_st.get("error"), "models": l_models, "devices": l_st.get("devices", []), "server": llama.server_status(),
                     "last_model": engines.last_model("llamacpp")}]}

    @r.get("/api/engines")
    async def get_engines():
        return await engine_status()

    @r.put("/api/engine")
    async def set_engine(b: EngineReq, request: Request):
        """Switch engine. With `session_id` the conversation moves to that engine (its model resets to the one last used there);
        either way the choice becomes the default for new conversations."""
        name = eng.valid_engine(b.engine)
        if not name:
            raise HTTPException(400, "unknown engine (use ollama or llamacpp)")
        engines.set_default(name)
        if b.session_id is not None:
            sess = sessions.get(b.session_id)
            if not sess:
                raise HTTPException(404, "unknown session")
            old = engines.name_of(sess)
            if old != name:
                engines.remember_model(old, sess["model"])
                db.x("UPDATE sessions SET engine=?, model=? WHERE id=?", (name, engines.last_model(name), b.session_id))
                sessions._changed(b.session_id)
            else:
                db.x("UPDATE sessions SET engine=? WHERE id=?", (name, b.session_id))
        zfiles.write_home()
        record_state(b.session_id, client_of(request))
        return await engine_status()

    async def llama_overview() -> dict:
        st = await llama.status()
        loop = asyncio.get_running_loop()
        found = await loop.run_in_executor(None, llama.candidates)
        cfg = llama.config()
        return {"config": cfg, "status": st, "found": found, "binary_auto": not (cfg["binary"] and os.path.isfile(cfg["binary"])), "binary_problem": llama.configured_problem(), "server": llama.server_status(), "error": llama_run["error"],
                "models": await llama.list_models(), "folders": [str(r) for r in llama._roots()], "managed_dir": str(managed_dir()),
                "log": llama.log_tail(15) if llama_run["error"] else "", "downloads": [d for d in downloads.list() if d["model"].startswith("hf:")]}

    @r.get("/api/llamacpp")
    async def llamacpp_overview():
        return await llama_overview()

    @r.post("/api/llamacpp/rescan")
    async def llamacpp_rescan():
        """Search the computer for llama-server again (after installing or building llama.cpp)."""
        await asyncio.get_running_loop().run_in_executor(None, llama.rescan)
        return await llama_overview()

    @r.put("/api/llamacpp/config")
    async def llamacpp_config(patch: dict):
        try:
            llama.save_config(patch)
        except LlamaCppError as e:
            raise HTTPException(400, str(e))
        zfiles.write_home()
        return await llama_overview()

    @r.post("/api/llamacpp/server/start")
    async def llamacpp_start(b: LlamaModelReq):
        """Load a model now (instead of on the first message). Returns at once; the overview shows `starting` and then `running`."""
        if not llama.resolve(b.model):
            raise HTTPException(404, f"model '{b.model}' was not found in your model folders")
        t = llama_run["task"]
        if t and not t.done():
            raise HTTPException(409, "a model is already loading")
        llama_run["error"] = None

        async def go():
            try:
                await llama.ensure(b.model)
                llama.save_config({"last_model": b.model})
            except LlamaCppError as e:
                llama_run["error"] = str(e)
            except Exception as e:  # noqa: BLE001
                llama_run["error"] = f"{type(e).__name__}: {e}"

        llama_run["task"] = asyncio.create_task(go())
        return {"starting": b.model}

    @r.post("/api/llamacpp/server/stop")
    async def llamacpp_stop():
        t = llama_run["task"]
        if t and not t.done():
            t.cancel()
        await llama.unload()
        llama_run["error"] = None
        return await llama_overview()

    @r.get("/api/llamacpp/log")
    async def llamacpp_log(lines: int = 80):
        return {"log": llama.log_tail(max(1, min(lines, 500)))}

    @r.get("/api/llamacpp/search")
    async def llamacpp_search(q: str = ""):
        """GGUF repositories on Hugging Face (most downloaded first, or matching what was typed). Only the typed text is sent to huggingface.co."""
        try:
            return await llama.hf_search(q)
        except LlamaCppError as e:
            raise HTTPException(400, str(e))

    @r.get("/api/llamacpp/hf")
    async def llamacpp_hf(repo: str, ctx: int = 8192):
        """The GGUF files of a repository, each with how it would run on this machine, and the file Zeus-Agent suggests."""
        try:
            info = await llama.hf_files(repo.strip())
        except LlamaCppError as e:
            raise HTTPException(400, str(e))
        _, hw = await arena_context()
        for f in info["files"]:
            f["fit"] = arena.gguf_fit(f["size"], f.get("quant"), hw, min(max(ctx, 2048), 262144))
        info["suggested"] = arena.suggest_file(info["files"])
        info["hardware"] = hw
        return info

    @r.post("/api/llamacpp/download")
    async def llamacpp_download(b: LlamaDownload):
        repo, file = b.repo.strip(), b.file.strip()
        if not HF_RE.match(repo) or not HF_FILE_RE.match(file) or ".." in file:
            raise HTTPException(400, "invalid repository or file name")
        return downloads.start(llama, f"hf:{repo}/{file}")

    @r.delete("/api/llamacpp/models")
    async def llamacpp_delete(b: LlamaModelReq):
        try:
            res = await llama.delete(b.model)
        except LlamaCppError as e:
            raise HTTPException(400, str(e))
        return {**await llama_overview(), "deleted": res}

    def engine_or_400(name: str | None):
        n = eng.valid_engine(name) if name else engines.default()
        if not n:
            raise HTTPException(400, "unknown engine (use ollama or llamacpp)")
        return n, engines.get(n)

    @r.get("/api/models")
    async def models(engine: str | None = None):
        """Models of an engine (default: the current default engine): Ollama's installed models or the GGUF files llama.cpp can run."""
        _, prov = engine_or_400(engine)
        try:
            return await prov.list_models()
        except ConnectorError as e:
            raise HTTPException(503, str(e))

    @r.get("/api/models/info")
    async def model_info(name: str, num_ctx: int | None = None, engine: str | None = None):
        _, prov = engine_or_400(engine)
        try:
            meta = await prov.show(name)
            size = next((m["size"] for m in await prov.list_models() if m["name"] == name), 0)
        except ConnectorError as e:
            raise HTTPException(404 if ("not installed" in str(e) or "not found" in str(e)) else 503, str(e))
        meta["size"] = size
        meta["tools_supported"] = ("tools" in meta["capabilities"]) if meta["capabilities"] else None
        if num_ctx:
            meta["context_check"] = context_warning(meta, size, num_ctx)
        return meta

    return r
