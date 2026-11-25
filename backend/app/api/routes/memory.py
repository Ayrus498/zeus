"""Project memory, the context meter and the rolling summary."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...memory import project_memory
from ...modelconnectors import registry as eng
from ..schemas import MemoryBody, NoteBody


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, sessions, zfiles, engines, agent, runtime, proj_or_404 = ctx.db, ctx.sessions, ctx.zfiles, ctx.engines, ctx.agent, ctx.runtime, ctx.proj_or_404

    @r.get("/api/projects/{pid}/memory")
    async def get_memory(pid: int):
        return project_memory.info(proj_or_404(pid)["path"])

    @r.put("/api/projects/{pid}/memory")
    async def put_memory(pid: int, b: MemoryBody):
        path = proj_or_404(pid)["path"]
        try:
            project_memory.write(path, b.text)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return project_memory.info(path)

    @r.post("/api/projects/{pid}/memory/note")
    async def add_memory_note(pid: int, b: NoteBody):
        path = proj_or_404(pid)["path"]
        try:
            line, added = project_memory.append(path, b.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {**project_memory.info(path), "line": line, "added": added}

    def session_and_project(sid: int) -> tuple[dict, dict]:
        s = sessions.get(sid)
        if not s:
            raise HTTPException(404, "unknown session")
        return s, proj_or_404(s["project_id"])

    @r.get("/api/sessions/{sid}/context")
    async def session_context(sid: int):
        """How full the context window is, what has been summarised, and the project memory in use."""
        s, p = session_and_project(sid)
        return agent.context_info(s, p)

    @r.post("/api/sessions/{sid}/compact")
    async def compact_session(sid: int):
        """Summarise the older part of the conversation now (the same thing that happens automatically at 75% of the window)."""
        s, p = session_and_project(sid)
        if runtime(sid).task and not runtime(sid).task.done():
            raise HTTPException(409, "Zeus-Agent is working on this conversation; wait for it to finish or stop it first.")
        model = s["model"] if s["model"] and s["model"] != "auto" else s.get("last_model")
        if not model:
            raise HTTPException(400, "Choose a model first: the summary is written by the conversation's model.")
        tok = eng.current_engine.set(engines.name_of(s))
        try:
            r = await agent.compact(s, p, model, force=True)
        finally:
            eng.current_engine.reset(tok)
        return {**r, "context": agent.context_info(sessions.get(sid), p)}

    @r.delete("/api/sessions/{sid}/summary")
    async def reset_summary(sid: int):
        """Forget the summary: the whole conversation is sent to the model again (until it fills the window)."""
        s, p = session_and_project(sid)
        db.x("UPDATE sessions SET summary=NULL, summary_msgs=0, summary_updated=NULL WHERE id=?", (sid,))
        zfiles.schedule(sid)
        return agent.context_info(sessions.get(sid), p)

    return r
