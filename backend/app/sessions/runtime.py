"""Live (in-memory) state of a session: websocket subscribers, running task, pending approvals."""
from __future__ import annotations

import asyncio

from ..permissions.manager import PermissionManager


class SessionRuntime:
    def __init__(self, session_id: int, perms: PermissionManager):
        self.session_id = session_id
        self.perms = perms
        self.subscribers: set = set()
        self.task: asyncio.Task | None = None
        self.pending: dict[str, tuple[asyncio.Future, dict]] = {}

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def emit(self, event: dict):
        event = {**event, "session_id": self.session_id}
        for ws in list(self.subscribers):
            try:
                await ws.send_json(event)
            except Exception:
                self.subscribers.discard(ws)

    async def request_permission(self, req: dict) -> str:
        fut = asyncio.get_running_loop().create_future()
        self.pending[req["id"]] = (fut, req)
        await self.emit({"type": "permission_request", **req})
        try:
            return await fut
        finally:
            self.pending.pop(req["id"], None)

    def respond(self, req_id: str, decision: str) -> bool:
        item = self.pending.get(req_id)
        if not item or item[0].done():
            return False
        item[0].set_result(decision if decision in ("once", "session") else "reject")
        return True

    def reject_all(self):
        for fut, _ in list(self.pending.values()):
            if not fut.done():
                fut.set_result("reject")
