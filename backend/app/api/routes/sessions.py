"""Conversations: list / create / patch / delete, messages, executions, missions, and 'where was I' (state.json)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...modelconnectors import registry as eng
from ..schemas import SessionCreate, SessionPatch


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, sessions, engines, state_store, runtimes, client_of, record_state, proj_or_404 = ctx.db, ctx.sessions, ctx.engines, ctx.state_store, ctx.runtimes, ctx.client_of, ctx.record_state, ctx.proj_or_404

    @r.get("/api/state")
    async def get_state():
        """The remembered last conversation (see persistence/state.py) and whether it can still be resumed."""
        doc = state_store.read()
        out = {"state": doc, "path": str(state_store.path), "resolved": None}
        proj, sess = None, None
        pr = doc.get("project")
        if pr and Path(pr["path"]).is_dir():
            proj = db.one("SELECT * FROM projects WHERE path=?", (pr["path"],))
        sd = doc.get("session") or {}
        if sd.get("uid"):
            sess = db.one("SELECT * FROM sessions WHERE uid=?", (sd["uid"],))
        if not sess and sd.get("id"):
            sess = db.one("SELECT * FROM sessions WHERE id=?", (sd["id"],))
        if sess and not proj:
            proj = db.one("SELECT * FROM projects WHERE id=?", (sess["project_id"],))
            if proj and not Path(proj["path"]).is_dir():
                proj = None
        if doc and (proj or sess):
            out["resolved"] = {"project": proj, "session": sess, "engine": doc.get("engine") or (sess or {}).get("engine"),
                               "model": doc.get("model"), "num_ctx": doc.get("num_ctx"), "permission_mode": doc.get("permission_mode")}
        return out

    @r.post("/api/sessions/{sid}/open")
    async def open_session(sid: int, request: Request):
        if not sessions.get(sid):
            raise HTTPException(404, "unknown session")
        record_state(sid, client_of(request))
        return {"ok": True}

    @r.get("/api/sessions")
    async def list_sessions(project_id: int | None = None):
        return sessions.list(project_id)

    @r.post("/api/sessions")
    async def create_session(b: SessionCreate, request: Request):
        proj_or_404(b.project_id)
        name = eng.valid_engine(b.engine) or engines.default()
        model = b.model or engines.last_model(name) or (db.get_setting("default_model") if name == "ollama" else None)
        ctx = b.num_ctx or int(db.get_setting("default_num_ctx", "16384"))
        sess = sessions.create(b.project_id, b.title or "New session", model, ctx, name)
        record_state(sess["id"], client_of(request))
        return sess

    @r.patch("/api/sessions/{sid}")
    async def patch_session(sid: int, b: SessionPatch, request: Request):
        sess = sessions.get(sid)
        if not sess:
            raise HTTPException(404, "unknown session")
        fields = b.model_dump()
        if fields.get("engine") is not None and not eng.valid_engine(fields["engine"]):
            raise HTTPException(400, "unknown engine (use ollama or llamacpp)")
        out = sessions.update(sid, **fields)
        if fields.get("model"):
            engines.remember_model(engines.name_of(out), fields["model"])
        record_state(sid, client_of(request))
        return out

    @r.delete("/api/sessions/{sid}")
    async def delete_session(sid: int):
        rt = runtimes.pop(sid, None)
        if rt and rt.task:
            rt.reject_all(); rt.task.cancel()
        sessions.delete(sid)
        return {"ok": True}

    @r.get("/api/sessions/{sid}/messages")
    async def session_messages(sid: int):
        if not sessions.get(sid):
            raise HTTPException(404, "unknown session")
        return sessions.messages(sid)

    @r.get("/api/executions")
    async def executions(project_id: int, session_id: int | None = None):
        q = "SELECT id,session_id,command,started,finished,exit_code,status,output FROM executions WHERE project_id=?"
        a: list = [project_id]
        if session_id:
            q += " AND session_id=?"; a.append(session_id)
        return db.q(q + " ORDER BY id DESC LIMIT 100", a)

    @r.get("/api/sessions/{sid}/missions")
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

    return r
