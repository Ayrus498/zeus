"""Health, settings, about / device information, live metrics, opening documentation links."""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ...core import config
from ...modelconnectors.ollama.provider import OllamaProvider
from ...system import metrics
from ...system.about import about as about_info
from ...toolchain.permissions import ACTIONS, MODES
from ..schemas import SettingsUpdate, UrlReq


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, zfiles, llama, engines, provider = ctx.db, ctx.zfiles, ctx.llama, ctx.engines, ctx.provider

    @r.get("/api/health")
    async def health():
        return {"ok": True, "version": config.VERSION}

    @r.get("/api/settings")
    async def get_settings():
        return {"home": str(Path.home()), "ollama_url": provider().base_url, "engine": engines.default(), "default_model": db.get_setting("default_model"),
                "default_num_ctx": int(db.get_setting("default_num_ctx", "16384")),
                "modes": {k: v for k, v in MODES.items()}, "actions": ACTIONS}

    @r.put("/api/settings")
    async def put_settings(s: SettingsUpdate):
        if s.ollama_url:
            url = s.ollama_url.strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                raise HTTPException(400, "endpoint must start with http:// or https://")
            db.set_setting("ollama_url", url)
            engines.ollama = OllamaProvider(url)
        if s.default_model:
            db.set_setting("default_model", s.default_model)
        if s.default_num_ctx:
            db.set_setting("default_num_ctx", str(s.default_num_ctx))
        zfiles.write_home()
        return await get_settings()

    OPEN_OK = re.compile(r"https://(?:(?:www\.|docs\.)?ollama\.com|github\.com/(?:ollama|ggml-org|Ayrus498/zeus)|(?:www\.)?huggingface\.co|freellmarena\.com|zeus-agent\.web\.app)(?:[/?#][^\s]*)?")

    @r.post("/api/open-url")
    async def open_url(b: UrlReq):
        """Open an allow-listed documentation link in the user's default browser (the webview cannot open new windows)."""
        if not OPEN_OK.fullmatch(b.url or ""):
            raise HTTPException(400, "only links to Ollama, llama.cpp, Hugging Face, FreeLLMArena and the Zeus-Agent project can be opened from here")
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise HTTPException(501, "no browser opener found (install xdg-utils)")
        cmd = [opener, b.url] if opener.endswith("xdg-open") else [opener, "open", b.url]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True}

    sampler = metrics.Sampler()

    @r.get("/api/about")
    async def get_about():
        """App info plus real device readings (CPU, GPU/VRAM, RAM) for Settings → About."""
        st = await provider().status()
        return await asyncio.get_running_loop().run_in_executor(None, about_info, st.get("version") if st["connected"] else None)

    @r.get("/api/metrics")
    async def get_metrics():
        """Live CPU/RAM/GPU readings plus `ollama ps` (what is loaded, and how it is split between GPU and CPU)."""
        out = await asyncio.get_running_loop().run_in_executor(None, sampler.sample)
        st = await provider().status()
        out["llamacpp"] = llama.server_status()
        out["ollama"] = {"connected": st["connected"], "version": st.get("version"),
                         "loaded": metrics.describe_ps(await provider().running()) if st["connected"] else []}
        return out

    return r
