from __future__ import annotations
import json
import uuid

from ..database.db import Database, now


class SessionManager:
    def __init__(self, db: Database):
        self.db = db
        self.files = None   # optional ZeusFiles mirror, set by the app

    def create(self, project_id: int, title: str = "New session", model: str | None = None, num_ctx: int | None = None) -> dict:
        t = now()
        sid = self.db.x("INSERT INTO sessions(project_id,title,model,num_ctx,created,updated,uid) VALUES(?,?,?,?,?,?,?)",
                        (project_id, title, model, num_ctx, t, t, uuid.uuid4().hex))
        self._changed(sid)
        return self.get(sid)

    def _changed(self, sid: int):
        if self.files:
            self.files.schedule(sid)

    def get(self, sid: int) -> dict | None:
        return self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))

    def list(self, project_id: int | None = None) -> list[dict]:
        if project_id:
            return self.db.q("SELECT * FROM sessions WHERE project_id=? ORDER BY updated DESC", (project_id,))
        return self.db.q("SELECT * FROM sessions ORDER BY updated DESC LIMIT 50")

    def update(self, sid: int, **fields) -> dict:
        allowed = {k: v for k, v in fields.items() if k in ("title", "model", "num_ctx") and v is not None}
        for k, v in allowed.items():
            self.db.x(f"UPDATE sessions SET {k}=? WHERE id=?", (v, sid))
        self._changed(sid)
        return self.get(sid)

    def delete(self, sid: int):
        if self.files:
            self.files.remove_session(sid)
        self.db.x("DELETE FROM sessions WHERE id=?", (sid,))

    def messages(self, sid: int) -> list[dict]:
        rows = self.db.q("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,))
        for r in rows:
            r["tool_calls"] = json.loads(r["tool_calls"]) if r["tool_calls"] else None
        return rows

    def auto_title(self, sid: int, text: str):
        s = self.get(sid)
        if s and s["title"] == "New session":
            self.update(sid, title=" ".join(text.split())[:48] or "New session")
