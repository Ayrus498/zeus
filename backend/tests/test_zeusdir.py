import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.database.db import Database
from app.persistence.zeusdir import ZeusFiles


def make_client(tmp_path, name="a.db"):
    app = create_app(str(tmp_path / name))
    return TestClient(app), app




@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    return d


def seed(app, client, proj):
    """A project + a session with a realistic tool-using conversation, flushed to .zeus/."""
    p = client.post("/api/projects", json={"path": str(proj)}).json()
    db: Database = app.state.db
    sm_row = db.q("SELECT 1")  # noqa: F841
    sid = app.state.zfiles.db.x("INSERT INTO sessions(project_id,title,model,num_ctx,created,updated,input_tokens,output_tokens,uid) VALUES(?,?,?,?,?,?,?,?,?)",
                                (p["id"], "Build a linked list", "qwen3:8b", 32768, 1.0, 2.0, 111, 22, uuid.uuid4().hex))
    for role, content, tc, tn in [("user", "make linkedlist.c", None, None),
                                  ("assistant", "", [{"function": {"name": "create_file", "arguments": {"path": "l.c", "content": "int x;"}}}], None),
                                  ("tool", "Created l.c", None, "create_file"),
                                  ("assistant", "Done.", None, None)]:
        db.x("INSERT INTO messages(session_id,agent,role,content,tool_calls,tool_name,created) VALUES(?,?,?,?,?,?,?)",
             (sid, "coder", role, content, json.dumps(tc) if tc else None, tn, 3.0))
    app.state.zfiles.write_session(sid)
    return p, sid


def test_session_is_written_to_project_zeus_folder(tmp_path, proj):
    client, app = make_client(tmp_path)
    p, sid = seed(app, client, proj)
    uid = app.state.db.one("SELECT uid FROM sessions WHERE id=?", (sid,))["uid"]
    f = proj / ".zeus" / "sessions" / f"{uid}.json"
    doc = json.loads(f.read_text())
    assert doc["title"] == "Build a linked list" and doc["model"] == "qwen3:8b" and doc["num_ctx"] == 32768
    assert [m["role"] for m in doc["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert doc["messages"][1]["tool_calls"][0]["function"]["name"] == "create_file"
    assert (proj / ".zeus" / ".gitignore").read_text().strip() == "*"          # never committed by accident
    assert (proj / ".zeus" / "README.md").exists() and (proj / ".zeus" / "project.json").exists()
    assert oct(f.stat().st_mode & 0o777) == "0o600"


def test_full_restore_after_losing_the_database(tmp_path, proj):
    client, app = make_client(tmp_path, "first.db")
    client.put("/api/settings", json={"ollama_url": "http://10.0.0.5:11434", "default_model": "qwen3:8b", "default_num_ctx": 32768})
    p, sid = seed(app, client, proj)
    # the user wipes ~/.local/share/zeus (the database) - .zeus folders and ~/.zeus remain
    client2, app2 = make_client(tmp_path, "brand-new.db")
    projects = client2.get("/api/projects").json()
    assert [x["path"] for x in projects] == [str(proj)]
    sessions = client2.get(f"/api/sessions?project_id={projects[0]['id']}").json()
    assert len(sessions) == 1 and sessions[0]["title"] == "Build a linked list" and sessions[0]["model"] == "qwen3:8b"
    assert (sessions[0]["input_tokens"], sessions[0]["output_tokens"]) == (111, 22)
    msgs = client2.get(f"/api/sessions/{sessions[0]['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"] and msgs[2]["tool_name"] == "create_file"
    s = client2.get("/api/settings").json()
    assert s["ollama_url"] == "http://10.0.0.5:11434" and s["default_model"] == "qwen3:8b" and s["default_num_ctx"] == 32768


def test_restore_is_idempotent(tmp_path, proj):
    client, app = make_client(tmp_path, "one.db")
    seed(app, client, proj)
    for n in range(3):
        c, a = make_client(tmp_path, "two.db")
    pid = c.get("/api/projects").json()[0]["id"]
    assert len(c.get(f"/api/sessions?project_id={pid}").json()) == 1


def test_deleting_a_session_removes_its_file_for_good(tmp_path, proj):
    client, app = make_client(tmp_path)
    p, sid = seed(app, client, proj)
    assert len(list((proj / ".zeus" / "sessions").glob("*.json"))) == 1
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert list((proj / ".zeus" / "sessions").glob("*.json")) == []
    c2, _ = make_client(tmp_path, "later.db")
    assert c2.get(f"/api/sessions?project_id={c2.get('/api/projects').json()[0]['id']}").json() == []   # not resurrected


def test_removing_a_project_keeps_its_history_and_readding_restores_it(tmp_path, proj):
    client, app = make_client(tmp_path)
    p, sid = seed(app, client, proj)
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert (proj / ".zeus" / "sessions").is_dir() and any((proj / ".zeus" / "sessions").glob("*.json"))
    p2 = client.post("/api/projects", json={"path": str(proj)}).json()
    sessions = client.get(f"/api/sessions?project_id={p2['id']}").json()
    assert len(sessions) == 1 and sessions[0]["title"] == "Build a linked list"


def test_hostile_zeus_folder_cannot_grant_permissions_or_inject_garbage(tmp_path, proj):
    z = proj / ".zeus"
    (z / "sessions").mkdir(parents=True)
    (z / "project.json").write_text(json.dumps({"version": 1, "permission_mode": "trusted", "custom_permissions": {"run_command": "allow"}}))
    good = uuid.uuid4().hex
    (z / "sessions" / f"{good}.json").write_text(json.dumps({
        "version": 1, "uid": good, "title": "x" * 500, "model": 123, "num_ctx": "big",
        "messages": [{"role": "bogus", "content": "evil"}, {"role": "user", "content": "hi"}, "not-a-dict",
                     {"role": "assistant", "content": ["not", "a", "str"]}, {"role": "tool", "content": "ok", "tool_calls": "nope"}]}))
    (z / "sessions" / "not-a-uid.json").write_text("{}")
    (z / "sessions" / f"{uuid.uuid4().hex}.json").write_text("{ this is not json")
    (z / "sessions" / f"{uuid.uuid4().hex}.json").write_text(json.dumps({"version": 99, "messages": []}))
    client, app = make_client(tmp_path)
    p = client.post("/api/projects", json={"path": str(proj)}).json()
    assert p["permission_mode"] == "approval"                                  # project files never change the mode
    sessions = client.get(f"/api/sessions?project_id={p['id']}").json()
    assert len(sessions) == 1 and len(sessions[0]["title"]) <= 120 and sessions[0]["model"] is None and sessions[0]["num_ctx"] is None
    msgs = client.get(f"/api/sessions/{sessions[0]['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "tool"] and msgs[1]["tool_calls"] is None


def test_agent_tools_cannot_write_into_zeus_folder(tmp_path, db, ws_dir):
    from tests.conftest import Harness
    import asyncio
    h = Harness(db, ws_dir, mode="trusted")
    for tool, args in [("create_file", {"path": ".zeus/sessions/x.json", "content": "{}"}),
                       ("create_file", {"path": "./.zeus/evil.txt", "content": "x"})]:
        r = asyncio.run(h.call(tool, **args))
        assert r["ok"] is False and ".zeus" in r["output"]
    assert not (ws_dir / ".zeus").exists()
    (ws_dir / ".zeus").mkdir()
    (ws_dir / ".zeus" / "a.txt").write_text("keep")
    for tool, args in [("edit_file", {"path": ".zeus/a.txt", "old_str": "keep", "new_str": "gone"}), ("delete_file", {"path": ".zeus/a.txt"})]:
        r = asyncio.run(h.call(tool, **args))
        assert r["ok"] is False
    assert (ws_dir / ".zeus" / "a.txt").read_text() == "keep"


def test_zeus_folder_hidden_from_listing_and_search(db, ws_dir):
    from tests.conftest import Harness
    import asyncio
    (ws_dir / ".zeus").mkdir()
    (ws_dir / ".zeus" / "s.json").write_text("needle")
    (ws_dir / "a.txt").write_text("needle")
    h = Harness(db, ws_dir, mode="trusted")
    assert ".zeus" not in asyncio.run(h.call("list_files", path=".", recursive=True))["output"]
    out = asyncio.run(h.call("search_files", pattern="needle"))["output"]
    assert "a.txt" in out and "s.json" not in out


def test_old_database_is_migrated_with_session_uids(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, last_opened REAL NOT NULL,
      permission_mode TEXT NOT NULL DEFAULT 'approval', custom_permissions TEXT NOT NULL DEFAULT '{}');
      CREATE TABLE sessions(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, title TEXT NOT NULL, model TEXT, num_ctx INTEGER,
      created REAL NOT NULL, updated REAL NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0);
      INSERT INTO projects(name,path,last_opened) VALUES('p','/tmp/p',0);
      INSERT INTO sessions(project_id,title,created,updated) VALUES(1,'a',0,0),(1,'b',0,0);""")
    c.commit(); c.close()
    db = Database(path)
    uids = [r["uid"] for r in db.q("SELECT uid FROM sessions")]
    assert len(set(uids)) == 2 and all(len(u) == 32 for u in uids)


def test_writes_are_debounced_and_flushed(tmp_path, proj):
    client, app = make_client(tmp_path)
    p, sid = seed(app, client, proj)
    z: ZeusFiles = app.state.zfiles
    uid = app.state.db.one("SELECT uid FROM sessions WHERE id=?", (sid,))["uid"]
    f = proj / ".zeus" / "sessions" / f"{uid}.json"
    f.unlink()
    for _ in range(5):
        z.schedule(sid)
    assert not f.exists()                 # debounced, not written per call
    z.flush()
    assert f.exists()


def test_database_deleted_while_running_is_repopulated_from_zeus_files(tmp_path, proj):
    client, app = make_client(tmp_path, "live.db")
    p, sid = seed(app, client, proj)
    (tmp_path / "live.db").unlink()                       # data folder wiped while ZEUS is open
    projects = client.get("/api/projects").json()
    assert [x["path"] for x in projects] == [str(proj)]
    sessions = client.get(f"/api/sessions?project_id={projects[0]['id']}").json()
    assert len(sessions) == 1 and client.get(f"/api/sessions/{sessions[0]['id']}/messages").json()[3]["content"] == "Done."
