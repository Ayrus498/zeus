"""Model download (ollama pull) manager. Progress and errors are persisted in SQLite so they survive restarts."""
from __future__ import annotations

import asyncio
import time

from ..core.database import Database
from .base import Connector, ConnectorError

ACTIVE = ("queued", "running")
KEEP_FINISHED = 20


class DownloadManager:
    def __init__(self, db: Database):
        self.db = db
        self.tasks: dict[int, asyncio.Task] = {}
        # A download cannot outlive the process: mark leftovers so the UI tells the truth. Ollama resumes partial pulls.
        self.db.x("UPDATE downloads SET status='interrupted', error='Zeus-Agent was closed during this download. '"
                  " || 'Press Retry to resume - Ollama keeps the layers already downloaded.', updated=? "
                  "WHERE status IN ('queued','running')", (time.time(),))

    def list(self) -> list[dict]:
        rows = self.db.q("SELECT * FROM downloads ORDER BY id DESC LIMIT 50")
        for r in rows:
            r["percent"] = round(100 * r["completed"] / r["total"], 1) if r["total"] else None
        return rows

    def active_for(self, model: str) -> dict | None:
        return self.db.one("SELECT * FROM downloads WHERE model=? AND status IN ('queued','running')", (model,))

    def start(self, provider: Connector, model: str, on_done=None) -> dict:
        existing = self.active_for(model)
        if existing:
            return existing
        now = time.time()
        did = self.db.x("INSERT INTO downloads(model,status,detail,started,updated) VALUES(?,?,?,?,?)",
                        (model, "running", "starting", now, now))
        self.tasks[did] = asyncio.create_task(self._run(did, provider, model, on_done))
        self._trim()
        return self.db.one("SELECT * FROM downloads WHERE id=?", (did,))

    async def _run(self, did: int, provider: Connector, model: str, on_done) -> None:
        layers: dict[str, list[int]] = {}      # digest -> [completed, total]
        last_write = 0.0

        def save(**kw):
            kw["updated"] = time.time()
            cols = ", ".join(f"{k}=?" for k in kw)
            self.db.x(f"UPDATE downloads SET {cols} WHERE id=?", (*kw.values(), did))

        try:
            async for ev in provider.pull_stream(model):
                status = str(ev.get("status", ""))
                if ev.get("digest") and ev.get("total"):
                    layers[ev["digest"]] = [int(ev.get("completed") or 0), int(ev["total"])]
                now = time.monotonic()
                if status == "success" or now - last_write >= 0.7:
                    last_write = now
                    comp = sum(v[0] for v in layers.values())
                    tot = sum(v[1] for v in layers.values())
                    save(detail=status[:120], completed=comp, total=tot)
            comp = sum(v[1] for v in layers.values())
            save(status="completed", detail="success", completed=comp, total=comp, error=None)
        except asyncio.CancelledError:
            save(status="cancelled", detail="cancelled", error=None)
            raise
        except ConnectorError as e:
            save(status="failed", detail="failed", error=str(e) or "download failed")
        except Exception as e:  # never let a background task die silently
            save(status="failed", detail="failed", error=f"{type(e).__name__}: {e}")
        finally:
            self.tasks.pop(did, None)
            if on_done:
                try:
                    await on_done()
                except Exception:
                    pass

    async def cancel(self, did: int) -> bool:
        t = self.tasks.get(did)
        if not t:
            return False
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
        return True

    def dismiss(self, did: int) -> None:
        if did in self.tasks:
            return
        self.db.x("DELETE FROM downloads WHERE id=? AND status NOT IN ('queued','running')", (did,))

    def _trim(self) -> None:
        self.db.x("DELETE FROM downloads WHERE status NOT IN ('queued','running') AND id NOT IN "
                  f"(SELECT id FROM downloads ORDER BY id DESC LIMIT {KEEP_FINISHED})")
