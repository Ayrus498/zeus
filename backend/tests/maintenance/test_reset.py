import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.database import Database
from app.memory import project_memory as memory
from app.maintenance import reset


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A Zeus-Agent installation in a temp dir: home, data dir, models dir, one project with history, memory and its own files."""
    home, data, models, proj = tmp_path / "home", tmp_path / "data", tmp_path / "data" / "models", tmp_path / "myproject"
    for d in (home, data, models, proj / "src"):
        d.mkdir(parents=True)
    monkeypatch.setenv("ZEUS_HOME", str(home))
    monkeypatch.setattr("app.core.config.DATA_DIR", data)
    monkeypatch.setattr("app.modelconnectors.llamacpp.provider.managed_dir", lambda: models)
    for router in ("maintenance", "engines"):                                        # the routers that import it by name
        monkeypatch.setattr(f"app.api.routes.{router}.managed_dir", lambda: models)
    (proj / "src" / "main.py").write_text("print('mine')\n")
    (proj / "README.md").write_text("# my own readme\n")
    return {"home": home, "data": data, "models": models, "proj": proj, "tmp": tmp_path}


def populate(env):
    z = env["proj"] / ".zeus"
    (z / "sessions").mkdir(parents=True)
    (z / "sessions" / "abc.json").write_text("{}")
    (z / "README.md").write_text("# .zeus")
    (z / "project.json").write_text("{}")
    (z / ".gitignore").write_text("*\n")
    memory.append(str(env["proj"]), "Tests run with pytest -q")
    (env["home"] / "settings.json").write_text(json.dumps({"projects": [{"path": str(env["proj"]), "name": "myproject"}]}))
    (env["home"] / "state.json").write_text("{}")
    for n in ("zeus.log", "cli-backend.log", "llama-server.log", "cli_history"):
        (env["data"] / n).write_text("log\n")
    (env["data"] / "shims").mkdir()
    (env["data"] / "shims" / "python").symlink_to("/usr/bin/python3")
    (env["models"] / "m1.gguf").write_bytes(b"G" * 5000)
    (env["models"] / "m2.gguf.part").write_bytes(b"P" * 100)
    (env["models"] / "notes.txt").write_text("keep me")


def make_client(env):
    c = TestClient(create_app(env["data"] / "zeus.db"), base_url="http://127.0.0.1:8765")
    c.__enter__()
    return c


def add_project_with_session(c, env):
    p = c.post("/api/projects", json={"path": str(env["proj"])}).json()
    s = c.post("/api/sessions", json={"project_id": p["id"], "model": "m"}).json()
    c.app.state.db.x("INSERT INTO messages(session_id,agent,role,content,created) VALUES(?,?,?,?,0)", (s["id"], "coder", "user", "hello"))
    c.app.state.db.set_setting("default_model", "m")
    c.app.state.zfiles.write_session(s["id"])
    c.app.state.zfiles.write_home()
    return p, s


# ------------------------------------------------------------------ the module
def test_plan_describes_every_item_with_counts_sizes_and_defaults(env):
    populate(env)
    db = Database(":memory:")
    plan = {i["id"]: i for i in reset.plan(db, ":memory:", env["home"], env["data"], env["models"], [str(env["proj"])])}
    assert list(plan) == ["conversations", "project_data", "home", "logs", "models"]
    assert [plan[k]["default"] for k in plan] == [True, True, True, True, False]            # models are large: never chosen by default
    assert plan["project_data"]["counts"]["projects"] == 1 and plan["project_data"]["counts"]["files"] >= 5 and plan["project_data"]["bytes"] > 0
    assert plan["home"]["counts"]["files"] == 2 and plan["logs"]["counts"]["files"] == 5      # 4 logs/history + the shim link
    assert plan["models"]["counts"]["files"] == 2 and plan["models"]["bytes"] == 5100
    assert all(i["label"] and i["detail"] for i in plan.values())


def test_run_removes_only_zeus_files_and_keeps_everything_of_the_user(env):
    populate(env)
    db = Database(":memory:")
    r = reset.run(db, ":memory:", ["project_data", "home", "logs", "models"], env["home"], env["data"], env["models"], [str(env["proj"])])
    assert all(i["ok"] for i in r["items"]) and r["removed_bytes"] > 5000
    assert not (env["proj"] / ".zeus").exists()                                              # emptied, then removed
    assert (env["proj"] / "src" / "main.py").read_text() == "print('mine')\n" and (env["proj"] / "README.md").read_text() == "# my own readme\n"
    assert not (env["home"] / "settings.json").exists() and not (env["data"] / "zeus.log").exists() and not (env["data"] / "shims").exists()
    assert Path("/usr/bin/python3").exists()                                                  # the shim link went, not its target
    assert not (env["models"] / "m1.gguf").exists() and (env["models"] / "notes.txt").read_text() == "keep me"


def test_unselected_items_are_left_alone(env):
    populate(env)
    reset.run(Database(":memory:"), ":memory:", ["logs"], env["home"], env["data"], env["models"], [str(env["proj"])])
    assert (env["proj"] / ".zeus" / "sessions" / "abc.json").exists() and (env["home"] / "state.json").exists() and (env["models"] / "m1.gguf").exists()


def test_unknown_files_in_dot_zeus_are_never_removed(env):
    populate(env)
    (env["proj"] / ".zeus" / "my-notes.txt").write_text("not zeus")
    (env["proj"] / ".zeus" / "sessions" / "keep.txt").write_text("not zeus")
    reset.run(Database(":memory:"), ":memory:", ["project_data"], env["home"], env["data"], env["models"], [str(env["proj"])])
    assert (env["proj"] / ".zeus" / "my-notes.txt").exists() and (env["proj"] / ".zeus" / "sessions" / "keep.txt").exists()
    assert not (env["proj"] / ".zeus" / "memory.md").exists() and not (env["proj"] / ".zeus" / "sessions" / "abc.json").exists()


def test_a_gitignore_that_is_not_ours_is_kept(env):
    populate(env)
    (env["proj"] / ".zeus" / ".gitignore").write_text("*\nsomething-else\n")
    reset.run(Database(":memory:"), ":memory:", ["project_data"], env["home"], env["data"], env["models"], [str(env["proj"])])
    assert (env["proj"] / ".zeus" / ".gitignore").exists()


def test_symlinks_are_never_followed(env):
    outside = env["tmp"] / "outside"
    outside.mkdir()
    (outside / "memory.md").write_text("precious")
    (outside / "sessions").mkdir()
    (outside / "sessions" / "x.json").write_text("precious")
    (env["proj"] / ".zeus").symlink_to(outside)                                               # .zeus itself is a link to somewhere else
    assert reset.project_files(str(env["proj"])) == []
    reset.run(Database(":memory:"), ":memory:", ["project_data"], env["home"], env["data"], env["models"], [str(env["proj"])])
    assert (outside / "memory.md").read_text() == "precious" and (outside / "sessions" / "x.json").exists()
    (env["proj"] / ".zeus").unlink()
    z = env["proj"] / ".zeus"
    z.mkdir()
    (z / "sessions").mkdir()
    (z / "sessions" / "link.json").symlink_to(outside / "memory.md")                          # a session file that is a link
    reset.run(Database(":memory:"), ":memory:", ["project_data"], env["home"], env["data"], env["models"], [str(env["proj"])])
    assert (outside / "memory.md").read_text() == "precious"


def test_a_missing_or_foreign_project_path_is_harmless(env):
    r = reset.run(Database(":memory:"), ":memory:", ["project_data"], env["home"], env["data"], env["models"], ["/nonexistent/project", "/", str(env["tmp"])])
    assert r["items"][0]["ok"] and r["items"][0]["files"] == 0


def test_wipe_database_empties_every_table_and_resets_ids():
    db = Database(":memory:")
    pid = db.x("INSERT INTO projects(name,path,last_opened) VALUES('p','/p',0)")
    sid = db.x("INSERT INTO sessions(project_id,title,created,updated) VALUES(?,?,0,0)", (pid, "t"))
    db.x("INSERT INTO messages(session_id,agent,role,content,created) VALUES(?,?,?,?,0)", (sid, "c", "user", "x"))
    db.set_setting("engine", "llamacpp")
    out = reset.wipe_database(db, ":memory:")
    assert out["tables"] >= 7
    for t in ("projects", "sessions", "messages", "missions", "downloads", "settings", "executions", "file_changes"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] == 0
    assert db.x("INSERT INTO projects(name,path,last_opened) VALUES('q','/q',0)") == 1       # numbering starts again, as on a fresh install


# ------------------------------------------------------------------ the API
def test_preview_changes_nothing_and_needs_a_local_caller(env):
    populate(env)
    c = make_client(env)
    add_project_with_session(c, env)
    before = sorted(str(p) for p in env["tmp"].rglob("*"))
    j = c.get("/api/reset").json()
    assert j["confirm"] == "RESET" and len(j["items"]) == 5 and j["keeps"]
    assert next(i for i in j["items"] if i["id"] == "conversations")["counts"]["sessions"] == 1
    assert sorted(str(p) for p in env["tmp"].rglob("*")) == before
    assert c.get("/api/reset", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/reset", headers={"Origin": "http://localhost:1420"}).status_code == 200
    assert c.get("/api/reset", headers={"Origin": "tauri://localhost"}).status_code == 200
    assert c.get("/api/reset", headers={"Host": "evil.example"}).status_code == 403          # DNS rebinding


def test_reset_needs_the_typed_word_and_known_items_and_a_local_origin(env):
    populate(env)
    c = make_client(env)
    add_project_with_session(c, env)
    assert c.post("/api/reset", json={"items": ["conversations"]}).status_code == 400
    assert c.post("/api/reset", json={"confirm": "reset", "items": ["conversations"]}).status_code == 400
    assert c.post("/api/reset", json={"confirm": "RESET", "items": []}).status_code == 400
    assert c.post("/api/reset", json={"confirm": "RESET", "items": ["everything"]}).status_code == 400
    assert c.post("/api/reset", json={"confirm": "RESET", "items": ["conversations"]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert len(c.get("/api/projects").json()) == 1 and (env["proj"] / ".zeus").exists()       # every refusal left everything in place


def test_reset_everything_returns_a_fresh_installation_that_still_works(env):
    populate(env)
    c = make_client(env)
    p, s = add_project_with_session(c, env)
    assert (env["proj"] / ".zeus" / "sessions").exists()
    r = c.post("/api/reset", json={"confirm": "RESET", "items": ["conversations", "project_data", "home", "logs"]})
    assert r.status_code == 200 and r.json()["reload"] and all(i["ok"] for i in r.json()["items"])
    assert c.get("/api/projects").json() == []
    assert c.get("/api/settings").json()["default_model"] in (None, "")
    assert not (env["proj"] / ".zeus").exists() and not (env["home"] / "settings.json").exists()
    assert (env["proj"] / "src" / "main.py").exists() and (env["models"] / "m1.gguf").exists()   # models were not chosen
    # the running app is usable again, as after a first start
    p2 = c.post("/api/projects", json={"path": str(env["proj"])}).json()
    assert p2["id"] == 1
    s2 = c.post("/api/sessions", json={"project_id": p2["id"], "model": "m"}).json()
    assert s2["id"] == 1 and c.get(f"/api/sessions/{s2['id']}/context").json()["messages_total"] == 0
    assert c.get(f"/api/projects/{p2['id']}/memory").json()["exists"] is False


def test_reset_can_include_the_downloaded_models(env):
    populate(env)
    c = make_client(env)
    r = c.post("/api/reset", json={"confirm": "RESET", "items": ["models"]})
    assert r.status_code == 200 and not (env["models"] / "m1.gguf").exists() and (env["models"] / "notes.txt").exists()
    assert (env["home"] / "state.json").exists()


def test_reset_stops_a_running_conversation_and_downloads_first(env):
    populate(env)
    c = make_client(env)
    p, s = add_project_with_session(c, env)

    class Task:
        cancelled = False
        def done(self): return False
        def cancel(self): self.cancelled = True

    class Runtime:
        rejected = False
        task = Task()
        def reject_all(self): self.rejected = True
    rt, dl = Runtime(), Task()
    c.app.state.runtimes[s["id"]] = rt
    c.app.state.downloads.tasks[99] = dl
    r = c.post("/api/reset", json={"confirm": "RESET", "items": ["conversations"]})
    assert r.status_code == 200
    assert rt.task.cancelled and rt.rejected and dl.cancelled                                 # pending permission requests are rejected, the run and the download stopped
    assert c.app.state.runtimes == {} and c.app.state.downloads.tasks == {}                   # and forgotten
