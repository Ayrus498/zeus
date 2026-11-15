"""Durable, human-readable copy of ZEUS state in `.zeus/` folders.

  <project>/.zeus/            per-project: conversation sessions (the context sent to Ollama), project.json
  ~/.zeus/settings.json       per-user: Ollama endpoint, default model/context, the list of known projects

SQLite stays the live store; these files mirror it so context survives a lost database and travels with a project.
On startup (and when a project is added/opened) anything found here that the database lacks is restored.

Security: permission modes are NEVER read from these files (a cloned repo must not be able to pre-grant itself
"trusted" mode); imported sessions are schema-validated and size-limited; the agent's file tools cannot write here.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path

from ..database.db import Database, now

log = logging.getLogger("zeus.persist")
VERSION = 1
UID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_SESSION_BYTES = 64 * 1024 * 1024
ROLES = {"user", "assistant", "tool", "system"}
SETTING_KEYS = ("ollama_url", "default_model", "default_num_ctx", "auto_code_model", "auto_think_model")
DEBOUNCE_S = 1.0

PROJECT_README = """# .zeus

This folder is written by the ZEUS desktop app. It keeps this project's conversation history (the context ZEUS
sends to Ollama) so it can be reloaded the next time you open the project.

- `sessions/<id>.json` - one file per conversation (messages, tool calls, model, context size, token counts)
- `project.json` - project preferences (never permissions)

It is git-ignored by default (`.gitignore` inside contains `*`) because sessions may contain code or secrets you pasted.
Delete this folder to forget the history for this project. Do not edit session files while ZEUS is running.
"""


def zeus_home() -> Path:
    return Path(os.environ.get("ZEUS_HOME") or (Path.home() / ".zeus"))


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ZeusFiles:
    def __init__(self, db: Database, home: Path | None = None):
        self.db = db
        self.home = Path(home) if home else zeus_home()
        self._timers: dict[int, threading.Timer] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ paths
    @staticmethod
    def project_dir(project_path: str | Path) -> Path:
        return Path(project_path) / ".zeus"

    def _prepare_project_dir(self, project_path: str) -> Path:
        d = self.project_dir(project_path)
        (d / "sessions").mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        if not (d / ".gitignore").exists():
            _atomic_write(d / ".gitignore", "*\n")
        if not (d / "README.md").exists():
            _atomic_write(d / "README.md", PROJECT_README)
        return d

    # ------------------------------------------------------------ writing
    def write_session(self, sid: int) -> bool:
        """Write one session snapshot. Never raises: persistence must not break the agent."""
        try:
            s = self.db.one("SELECT * FROM sessions WHERE id=?", (sid,))
            if not s:
                return False
            p = self.db.one("SELECT * FROM projects WHERE id=?", (s["project_id"],))
            if not p or not Path(p["path"]).is_dir():
                return False
            d = self._prepare_project_dir(p["path"])
            msgs = self.db.q("SELECT agent,role,content,tool_calls,tool_name,created FROM messages WHERE session_id=? ORDER BY id", (sid,))
            for m in msgs:
                m["tool_calls"] = json.loads(m["tool_calls"]) if m["tool_calls"] else None
            missions = self.db.q("SELECT name,title,objective,criteria,status,round,max_rounds,plan,log,created,updated FROM missions WHERE session_id=? ORDER BY id", (sid,))
            for ms in missions:
                for k in ("criteria", "plan", "log"):
                    try:
                        ms[k] = json.loads(ms[k])
                    except Exception:
                        ms[k] = [] if k != "plan" else {}
            doc = {"version": VERSION, "missions": missions, "uid": s["uid"], "title": s["title"], "model": s["model"], "last_model": s["last_model"], "num_ctx": s["num_ctx"],
                   "created": s["created"], "updated": s["updated"], "input_tokens": s["input_tokens"],
                   "output_tokens": s["output_tokens"], "messages": msgs}
            _atomic_write(d / "sessions" / f"{s['uid']}.json", json.dumps(doc, ensure_ascii=False, indent=1))
            self._write_project_json(p, d)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("could not write session %s to .zeus: %s", sid, e)
            return False

    def _write_project_json(self, p: dict, d: Path) -> None:
        s = self.db.one("SELECT model,num_ctx FROM sessions WHERE project_id=? ORDER BY updated DESC LIMIT 1", (p["id"],)) or {}
        _atomic_write(d / "project.json", json.dumps({"version": VERSION, "name": p["name"], "last_model": s.get("model"),
                                                      "last_num_ctx": s.get("num_ctx")}, indent=1))

    def schedule(self, sid: int) -> None:
        """Debounced write: bursts of messages during a run produce one file write per second."""
        with self._lock:
            t = self._timers.pop(sid, None)
            if t:
                t.cancel()
            t = threading.Timer(DEBOUNCE_S, self._fire, args=(sid,))
            t.daemon = True
            self._timers[sid] = t
            t.start()

    def _fire(self, sid: int) -> None:
        with self._lock:
            self._timers.pop(sid, None)
        self.write_session(sid)

    def flush(self) -> None:
        """Write everything that is pending now (run end, shutdown)."""
        with self._lock:
            pending = list(self._timers.items())
            self._timers.clear()
        for sid, t in pending:
            t.cancel()
            self.write_session(sid)

    def remove_session(self, sid: int) -> None:
        """Delete the file of a session (call BEFORE deleting the DB row)."""
        try:
            with self._lock:
                t = self._timers.pop(sid, None)
                if t:
                    t.cancel()
            row = self.db.one("SELECT s.uid, p.path FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.id=?", (sid,))
            if row and row["uid"]:
                (self.project_dir(row["path"]) / "sessions" / f"{row['uid']}.json").unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            log.warning("could not remove session file: %s", e)

    # ------------------------------------------------------------ user-level settings
    def write_home(self) -> None:
        try:
            doc = {"version": VERSION, **{k: self.db.get_setting(k) for k in SETTING_KEYS},
                   "projects": [{"path": r["path"], "name": r["name"], "last_opened": r["last_opened"]}
                                for r in self.db.q("SELECT path,name,last_opened FROM projects ORDER BY last_opened DESC")]}
            self.home.mkdir(parents=True, exist_ok=True)
            os.chmod(self.home, 0o700)
            _atomic_write(self.home / "settings.json", json.dumps(doc, indent=1))
        except Exception as e:  # noqa: BLE001
            log.warning("could not write %s: %s", self.home / "settings.json", e)

    def restore_home(self) -> dict:
        """Fill in settings and projects the database lacks from ~/.zeus/settings.json, then restore their sessions."""
        out = {"settings": 0, "projects": 0, "sessions": 0}
        f = self.home / "settings.json"
        try:
            doc = json.loads(f.read_text("utf-8")) if f.is_file() and f.stat().st_size < 1_000_000 else None
        except Exception as e:  # noqa: BLE001
            log.warning("ignoring unreadable %s: %s", f, e)
            doc = None
        if isinstance(doc, dict):
            for k in SETTING_KEYS:
                v = doc.get(k)
                if v not in (None, "") and self.db.get_setting(k) is None and isinstance(v, (str, int)):
                    if k == "ollama_url" and not str(v).startswith(("http://", "https://")):
                        continue
                    self.db.set_setting(k, str(v))
                    out["settings"] += 1
            for pr in doc.get("projects", []) if isinstance(doc.get("projects"), list) else []:
                path = pr.get("path") if isinstance(pr, dict) else None
                if not isinstance(path, str) or not os.path.isabs(path) or not Path(path).is_dir():
                    continue
                if not self.db.one("SELECT id FROM projects WHERE path=?", (path,)):
                    lo = pr.get("last_opened") if isinstance(pr.get("last_opened"), (int, float)) else now()
                    self.db.x("INSERT INTO projects(name,path,last_opened) VALUES(?,?,?)", (Path(path).name, path, lo))
                    out["projects"] += 1
        for p in self.db.q("SELECT * FROM projects"):
            out["sessions"] += self.restore_project(p)
        return out

    # ------------------------------------------------------------ restoring sessions
    def restore_project(self, project: dict) -> int:
        """Import sessions found in <project>/.zeus/sessions that the database does not have yet."""
        sdir = self.project_dir(project["path"]) / "sessions"
        if not sdir.is_dir():
            return 0
        n = 0
        known = {r["uid"] for r in self.db.q("SELECT uid FROM sessions WHERE project_id=?", (project["id"],))}
        for f in sorted(sdir.glob("*.json")):
            try:
                if f.stat().st_size > MAX_SESSION_BYTES or not UID_RE.match(f.stem) or f.stem in known:
                    continue
                if self.db.one("SELECT id FROM sessions WHERE uid=?", (f.stem,)):
                    continue                      # same conversation already attached to another project row
                doc = json.loads(f.read_text("utf-8"))
                if self._import(project["id"], f.stem, doc):
                    n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("skipping unreadable session file %s: %s", f, e)
        return n

    def _import(self, project_id: int, uid: str, doc) -> bool:
        if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("messages"), list):
            return False

        def num(v, default=0):
            return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default

        def txt(v, default=None, limit=200):
            return v[:limit] if isinstance(v, str) else default
        title = txt(doc.get("title"), "Restored session", 120) or "Restored session"
        created, updated = num(doc.get("created"), now()), num(doc.get("updated"), now())
        sid = self.db.x("INSERT INTO sessions(project_id,title,model,num_ctx,created,updated,input_tokens,output_tokens,uid,last_model) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (project_id, title, txt(doc.get("model")), int(num(doc.get("num_ctx"), 0)) or None, created, updated,
                         int(num(doc.get("input_tokens"))), int(num(doc.get("output_tokens"))), uid, txt(doc.get("last_model"))))
        for m in doc["messages"]:
            if not isinstance(m, dict) or m.get("role") not in ROLES or not isinstance(m.get("content", ""), str):
                continue
            tc = m.get("tool_calls")
            tc = json.dumps(tc) if isinstance(tc, list) else None
            self.db.x("INSERT INTO messages(session_id,agent,role,content,tool_calls,tool_name,created) VALUES(?,?,?,?,?,?,?)",
                      (sid, txt(m.get("agent"), "coder", 40) or "coder", m["role"], m.get("content", ""), tc, txt(m.get("tool_name"), None, 80), num(m.get("created"), now())))
        for ms in doc.get("missions", []) if isinstance(doc.get("missions"), list) else []:
            if not isinstance(ms, dict):
                continue
            self.db.x("INSERT INTO missions(session_id,name,title,objective,criteria,status,round,max_rounds,plan,log,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (sid, txt(ms.get("name"), "mission.md", 120), txt(ms.get("title"), "Mission", 120), txt(ms.get("objective"), "", 1500),
                       json.dumps(ms.get("criteria") if isinstance(ms.get("criteria"), list) else []), txt(ms.get("status"), "stopped", 20),
                       int(num(ms.get("round"))), int(num(ms.get("max_rounds"), 5)) or 5,
                       json.dumps(ms.get("plan") if isinstance(ms.get("plan"), dict) else {}), json.dumps(ms.get("log") if isinstance(ms.get("log"), list) else []),
                       num(ms.get("created"), now()), num(ms.get("updated"), now())))
        return True
