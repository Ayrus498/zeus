"""SQLite access. One connection per call keeps threading simple."""
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
  last_opened REAL NOT NULL, permission_mode TEXT NOT NULL DEFAULT 'approval',
  custom_permissions TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL, model TEXT, num_ctx INTEGER, created REAL NOT NULL, updated REAL NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, uid TEXT, last_model TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  agent TEXT NOT NULL DEFAULT 'coder', role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
  tool_calls TEXT, tool_name TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS executions(
  id INTEGER PRIMARY KEY, project_id INTEGER, session_id INTEGER, command TEXT NOT NULL,
  started REAL NOT NULL, finished REAL, exit_code INTEGER, status TEXT NOT NULL, output TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS file_changes(
  id INTEGER PRIMARY KEY, session_id INTEGER, project_id INTEGER NOT NULL, path TEXT NOT NULL,
  op TEXT NOT NULL, before TEXT, after TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS missions(
  id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, name TEXT NOT NULL,
  title TEXT NOT NULL, objective TEXT NOT NULL DEFAULT '', criteria TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,
  round INTEGER NOT NULL DEFAULT 0, max_rounds INTEGER NOT NULL DEFAULT 5, plan TEXT NOT NULL DEFAULT '{}', log TEXT NOT NULL DEFAULT '[]',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS downloads(
  id INTEGER PRIMARY KEY, model TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0, error TEXT,
  started REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

log = logging.getLogger("zeus.db")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        self._ready = False
        self.on_recreated = None   # optional callback: repopulate from .zeus/ files after a mid-run wipe
        with self.conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)
        self._ready = True

    @staticmethod
    def _migrate(c) -> None:
        """Forward-only schema upgrades for databases created by older versions."""
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        if "uid" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN uid TEXT")
        if "last_model" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN last_model TEXT")
        for (sid,) in c.execute("SELECT id FROM sessions WHERE uid IS NULL").fetchall():
            c.execute("UPDATE sessions SET uid=? WHERE id=?", (uuid.uuid4().hex, sid))
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_uid ON sessions(uid)")

    def _ensure(self):
        """If the data folder/file was deleted while the app is running, recreate an empty database
        instead of failing every request with "unable to open database file"."""
        if self._mem or not self._ready or os.path.exists(self.path):
            return
        log.warning("database file %s disappeared; recreating an empty database", self.path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=10)
        try:
            c.executescript(SCHEMA)
            self._migrate(c)
            c.commit()
        finally:
            c.close()
        if self.on_recreated:
            try:
                self.on_recreated()
            except Exception:  # noqa: BLE001
                log.exception("restore after database recreation failed")

    @contextmanager
    def conn(self):
        self._ensure()
        c = self._mem or sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            if not self._mem:
                c.close()

    def q(self, sql, args=()):
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.conn() as c:
            return c.execute(sql, args).lastrowid

    def get_setting(self, key, default=None):
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key, value):
        self.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now() -> float:
    return time.time()
