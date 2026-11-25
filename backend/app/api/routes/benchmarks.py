"""Which model is best for which job (Arena scores) and what this machine can run."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...modelconnectors.ollama import library as model_library
from ...modelhub import arena


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, arena_context = ctx.db, ctx.arena_context

    @r.get("/api/arena")
    async def arena_overview(refresh: bool = False, ctx: int = 8192):
        """Open models ranked by Arena votes for each activity, with this machine's verdict and the matching Ollama tag."""
        ctx = min(max(ctx, 2048), 262144)
        data, hw = await arena_context(refresh)
        lib = (await model_library.fetch_library(db)).get("models", [])
        return arena.overview(data, hw, lib, ctx)

    @r.get("/api/arena/family/{family:path}")
    async def arena_family(family: str, ctx: int = 8192):
        """Arena results for an Ollama family (name, rank, score per activity) and the verdict for every size on this machine."""
        data, hw = await arena_context()
        return {"license": data["license"], "updated": data["updated"], "hardware": hw, "results": arena.for_family(data, hw, family, min(max(ctx, 2048), 262144))}

    @r.get("/api/arena/detail")
    async def arena_detail(repo: str, ctx: int = 8192):
        """Every quantization level for one model: size, where it would run, speed and quality loss on this machine."""
        data, hw = await arena_context()
        for c in data["categories"]:
            for m in c["models"]:
                if m["hf"] and m["hf"]["repo"] == repo:
                    return {"hardware": hw, "fit": arena.assess(m["hf"], hw, min(max(ctx, 2048), 262144), detail=True)}
        for m in data["small"]:
            if m["hf"]["repo"] == repo:
                return {"hardware": hw, "fit": arena.assess(m["hf"], hw, min(max(ctx, 2048), 262144), detail=True)}
        raise HTTPException(404, "model not in the Arena data")

    return r
