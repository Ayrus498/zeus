"""The "where was I" file: ~/.zeus/state.json.

Whichever client you used last (the desktop app or the terminal app) records the project, conversation, engine, model, context
window and permission mode you were working with. `zeus --cli --continue` reads it to resume exactly that, and the desktop app
can do the same. The file is plain JSON so it can be read or edited by hand:

    {"version": "0.2.2", "updated": 1795500000.0, "client": "gui",
     "engine": "llamacpp", "model": "qwen2.5-coder-1.5b-instruct-q4_k_m", "num_ctx": 16384, "permission_mode": "approval",
     "project": {"path": "/home/me/app", "name": "app"},
     "session": {"uid": "3f2c...", "id": 12, "title": "Add a login page"},
     "models": {"ollama": "qwen3:8b", "llamacpp": "qwen2.5-coder-1.5b-instruct-q4_k_m"}}

It is a pointer, not a copy: the conversation itself stays in the database and in <project>/.zeus/sessions/. It is treated as
untrusted input when read back (types and sizes are checked, the project must still be an existing folder).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from ..core.config import VERSION
from .zeusdir import _atomic_write, zeus_home

log = logging.getLogger("zeus.state")
MAX_BYTES = 100_000


def _text(v, limit=500):
    return v[:limit] if isinstance(v, str) else None


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 2**31 else None


def sanitize(doc) -> dict:
    """Only the fields we know, with the right types. Anything else in the file is ignored."""
    if not isinstance(doc, dict):
        return {}
    out: dict = {}
    for k in ("client", "engine", "model", "permission_mode"):
        t = _text(doc.get(k), 300)
        if t:
            out[k] = t
    if isinstance(doc.get("updated"), (int, float)) and not isinstance(doc.get("updated"), bool):
        out["updated"] = float(doc["updated"])
    n = _int(doc.get("num_ctx"))
    if n:
        out["num_ctx"] = n
    p = doc.get("project")
    if isinstance(p, dict) and _text(p.get("path")):
        out["project"] = {"path": _text(p["path"], 1000), "name": _text(p.get("name"), 200) or ""}
    s = doc.get("session")
    if isinstance(s, dict) and (_text(s.get("uid"), 64) or _int(s.get("id"))):
        out["session"] = {"uid": _text(s.get("uid"), 64), "id": _int(s.get("id")), "title": _text(s.get("title"), 200) or ""}
    m = doc.get("models")
    if isinstance(m, dict):
        out["models"] = {str(k)[:30]: v[:300] for k, v in m.items() if isinstance(v, str) and v}
    return out


class StateStore:
    def __init__(self, home: Path | None = None):
        self._home = home
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return (self._home or zeus_home()) / "state.json"

    def read(self) -> dict:
        f = self.path
        try:
            if not f.is_file() or f.stat().st_size > MAX_BYTES:
                return {}
            return sanitize(json.loads(f.read_text("utf-8")))
        except Exception as e:  # noqa: BLE001
            log.warning("ignoring unreadable %s: %s", f, e)
            return {}

    def write(self, doc: dict) -> dict:
        """Merge `doc` into the stored state (None removes a field) and save atomically with private permissions."""
        with self._lock:
            cur = self.read()
            for k, v in doc.items():
                if v is None:
                    cur.pop(k, None)
                elif k == "models" and isinstance(v, dict):
                    cur["models"] = {**cur.get("models", {}), **v}
                else:
                    cur[k] = v
            cur = sanitize(cur)
            cur["version"] = VERSION
            cur["updated"] = time.time()
            try:
                f = self.path
                f.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(f.parent, 0o700)
                _atomic_write(f, json.dumps(cur, indent=1))
                os.chmod(f, 0o600)
            except OSError as e:
                log.warning("could not write %s: %s", self.path, e)
            return cur
