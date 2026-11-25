"""Auto mode: which model it would pick, its plan and pins."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...agents.router import classify, decide
from ...modelconnectors.ollama.provider import OllamaError
from ..schemas import PinsUpdate, PreviewReq


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, zfiles, auto, provider = ctx.db, ctx.zfiles, ctx.auto, ctx.provider

    @r.get("/api/auto/plan")
    async def auto_plan():
        """What Auto mode would use for coding vs thinking right now, with warnings and download suggestions."""
        try:
            return await auto.plan()
        except OllamaError as e:
            raise HTTPException(503, str(e))

    @r.post("/api/auto/preview")
    async def auto_preview(b: PreviewReq):
        """Dry run: which model would Auto pick for this prompt, and why. Nothing is loaded or changed."""
        try:
            profiles, gpu, ram = await auto.gather()
        except OllamaError as e:
            raise HTTPException(503, str(e))
        c = decide(b.prompt, profiles, gpu, ram, b.last_model, auto.pins())
        i = classify(b.prompt)
        return {"intent": i.role, "choice": None if not c else {"model": c.model, "role": c.role, "tools": c.tools, "reason": c.reason}}

    @r.put("/api/auto/pins")
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

    return r
