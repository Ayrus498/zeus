"""FastAPI app: the composition root. It builds the shared context and mounts one router per feature; the code of each feature is in `routes/`.

`python -m app` runs `main()`. Tests and tools use `create_app(db_path)`.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..core import config
from .context import ApiContext
from .routes import auto, benchmarks, documents, engines, live, maintenance, memory, ollama, sessions, system, workspace

ROUTERS = (system, ollama, auto, benchmarks, engines, workspace, sessions, memory, documents, maintenance, live)


def create_app(db_path=None) -> FastAPI:
    app = FastAPI(title="Zeus-Agent backend", version=config.VERSION)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    ctx = ApiContext(app, db_path)

    @app.on_event("shutdown")
    async def _flush_zeus_files():
        ctx.zfiles.flush()
        ctx.zfiles.write_home()
        await ctx.llama.shutdown()                                     # never leave a llama-server behind

    for feature in ROUTERS:
        app.include_router(feature.router(ctx))
    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
