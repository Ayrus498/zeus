import os


def test_project_lifecycle_never_deletes_files(client, tmp_path):
    d = tmp_path / "myproj"; d.mkdir(); (d / "keep.txt").write_text("data")
    p = client.post("/api/projects", json={"path": str(d)}).json()
    assert p["name"] == "myproj" and p["writable"]
    assert client.post("/api/projects", json={"path": str(d)}).json()["id"] == p["id"]   # idempotent
    assert [t["name"] for t in client.get(f"/api/projects/{p['id']}/tree").json()] == ["keep.txt"]
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert (d / "keep.txt").read_text() == "data" and client.get("/api/projects").json() == []


def test_add_invalid(client, tmp_path):
    assert client.post("/api/projects", json={"path": str(tmp_path / "nope")}).status_code == 400
    f = tmp_path / "f"; f.write_text("x")
    assert client.post("/api/projects", json={"path": str(f)}).status_code == 400
    assert client.post("/api/projects", json={"path": "relative/p"}).status_code == 400


def test_create_project(client, tmp_path):
    p = client.post("/api/projects/create", json={"parent": str(tmp_path), "name": "zeus-notes"}).json()
    assert os.path.isdir(tmp_path / "zeus-notes") and p["path"].endswith("zeus-notes")
    assert client.post("/api/projects/create", json={"parent": str(tmp_path), "name": "zeus-notes"}).status_code == 400
    assert client.post("/api/projects/create", json={"parent": str(tmp_path), "name": "../x"}).status_code == 400


def test_tree_and_file_traversal(client, tmp_path):
    d = tmp_path / "p"; d.mkdir(); (d / "a.py").write_text("x")
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    assert client.get(f"/api/projects/{pid}/file", params={"path": "a.py"}).json()["content"] == "x"
    assert client.get(f"/api/projects/{pid}/file", params={"path": "../../etc/passwd"}).status_code == 403
    assert client.get(f"/api/projects/{pid}/tree", params={"rel": ".."}).status_code == 400


def test_sessions_crud_and_persistence(client, tmp_path):
    d = tmp_path / "p"; d.mkdir()
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    s = client.post("/api/sessions", json={"project_id": pid, "model": "m1", "num_ctx": 8192}).json()
    assert s["model"] == "m1" and s["num_ctx"] == 8192
    s = client.patch(f"/api/sessions/{s['id']}", json={"title": "Renamed", "model": "m2"}).json()
    assert s["title"] == "Renamed" and s["model"] == "m2"
    assert len(client.get("/api/sessions", params={"project_id": pid}).json()) == 1
    client.delete(f"/api/sessions/{s['id']}")
    assert client.get("/api/sessions", params={"project_id": pid}).json() == []


def test_permission_endpoint(client, tmp_path):
    d = tmp_path / "p"; d.mkdir()
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    assert client.get(f"/api/projects/{pid}/permissions").json()["mode"] == "approval"
    r = client.put(f"/api/projects/{pid}/permissions", json={"mode": "read_only"}).json()
    assert r["policy"]["create_file"] == "deny"
    r = client.put(f"/api/projects/{pid}/permissions", json={"mode": "custom", "custom": {"run_command": "allow", "bogus": "allow"}}).json()
    assert r["policy"]["run_command"] == "allow" and "bogus" not in r["custom"]
    assert client.put(f"/api/projects/{pid}/permissions", json={"mode": "yolo"}).status_code == 400


def test_terminal_ws_respects_read_only(client, tmp_path):
    d = tmp_path / "p"; d.mkdir()
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    client.put(f"/api/projects/{pid}/permissions", json={"mode": "read_only"})
    with client.websocket_connect(f"/ws/terminal/{pid}") as ws:
        ws.send_json({"type": "run", "command": "touch x"})
        assert ws.receive_json()["type"] == "command_denied"
    assert not (d / "x").exists()


def test_terminal_ws_runs(client, tmp_path):
    d = tmp_path / "p"; d.mkdir()
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    with client.websocket_connect(f"/ws/terminal/{pid}") as ws:
        ws.send_json({"type": "run", "command": "echo hi"})
        evs = []
        while not evs or evs[-1]["type"] != "command_end":
            evs.append(ws.receive_json())
    assert evs[-1]["exit_code"] == 0 and any("hi" in e.get("text", "") for e in evs)
    assert client.get("/api/executions", params={"project_id": pid}).json()[0]["command"] == "echo hi"


def test_metrics_endpoint(client):
    r = client.get("/api/metrics")
    assert r.status_code == 200
    j = r.json()
    assert {"cpu", "ram", "ollama_proc", "gpus", "ollama"} <= set(j)
    assert j["ram"]["total"] > 0 and isinstance(j["gpus"], list)
    j2 = client.get("/api/metrics").json()
    assert j2["cpu"]["percent"] is None or 0 <= j2["cpu"]["percent"] <= 100


def test_processor_label():
    from app.metrics import processor_label, describe_ps
    assert processor_label(100, 100) == "100% GPU" and processor_label(100, 0) == "100% CPU"
    assert processor_label(100, 60) == "40%/60% CPU/GPU"
    assert describe_ps([{"name": "m", "size": 200, "size_vram": 50}])[0]["gpu_pct"] == 25


def test_database_recreated_if_data_dir_deleted(tmp_path):
    import shutil
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    d = tmp_path / "data"
    c = TestClient(create_app(str(d / "zeus.db")))
    assert c.get("/api/projects").status_code == 200
    shutil.rmtree(d)                       # e.g. user wipes ~/.local/share/zeus while ZEUS is running
    assert c.get("/api/projects").json() == []
    parent = tmp_path / "ws"; parent.mkdir()
    r = c.post("/api/projects/create", json={"parent": str(parent), "name": "demo"})
    assert r.status_code == 200 and (parent / "demo").is_dir()


def test_save_code_block_as_file(client, tmp_path):
    ws = tmp_path / "site"
    ws.mkdir()
    pid = client.post("/api/projects", json={"path": str(ws)}).json()["id"]
    url = f"/api/projects/{pid}/files"
    r = client.post(url, json={"path": "index.html", "content": "<!DOCTYPE html>\n<p>hi</p>\n"})
    assert r.status_code == 200 and r.json()["path"] == "index.html" and (ws / "index.html").read_text() == "<!DOCTYPE html>\n<p>hi</p>\n"
    assert client.post(url, json={"path": "index.html", "content": "x"}).status_code == 409              # never overwritten silently
    assert (ws / "index.html").read_text().startswith("<!DOCTYPE")
    r = client.post(url, json={"path": "index.html", "content": "v2", "overwrite": True})
    assert r.status_code == 200 and r.json()["overwritten"] and (ws / "index.html").read_text() == "v2"
    r = client.post(url, json={"path": "src/deep/app.py", "content": "x=1"})
    assert r.status_code == 200 and (ws / "src/deep/app.py").exists()                                       # creates folders
    changes = {c["path"]: c["status"] for c in client.get(f"/api/projects/{pid}/changes").json()}
    assert changes["index.html"] == "created" and changes["src/deep/app.py"] == "created"                   # shows in the Changes tab


def test_save_file_is_confined_and_respects_read_only(client, tmp_path):
    ws = tmp_path / "site"
    ws.mkdir()
    pid = client.post("/api/projects", json={"path": str(ws)}).json()["id"]
    url = f"/api/projects/{pid}/files"
    for bad in ["../evil.txt", "/etc/cron.d/x", ".zeus/sessions/x.json", "a/../../b.txt", ""]:
        assert client.post(url, json={"path": bad, "content": "x"}).status_code == 400, bad
    assert not (tmp_path / "evil.txt").exists()
    (tmp_path / "outside").mkdir()
    (ws / "link").symlink_to(tmp_path / "outside")
    assert client.post(url, json={"path": "link/pwn.txt", "content": "x"}).status_code == 400              # symlink escape
    (ws / "adir").mkdir()
    assert client.post(url, json={"path": "adir", "content": "x", "overwrite": True}).status_code == 400   # is a folder
    client.put(f"/api/projects/{pid}/permissions", json={"mode": "read_only"})
    r = client.post(url, json={"path": "ro.txt", "content": "x"})
    assert r.status_code == 403 and not (ws / "ro.txt").exists()


def _mission_session(client, tmp_path, model="m"):
    ws = tmp_path / "mws"
    ws.mkdir(exist_ok=True)
    pid = client.post("/api/projects", json={"path": str(ws)}).json()["id"]
    return pid, client.post("/api/sessions", json={"project_id": pid, "model": model, "num_ctx": 8192}).json()["id"]


def _first_event(client, sid, msg, want="error"):
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json(msg)
        for _ in range(5):
            e = ws.receive_json()
            if e["type"] == want:
                return e
    return None


def test_mission_attachment_is_validated(client, tmp_path):
    _, sid = _mission_session(client, tmp_path)
    assert "Only Markdown" in _first_event(client, sid, {"type": "mission", "name": "notes.txt", "content": "# hi"})["message"]
    assert "Only Markdown" in _first_event(client, sid, {"type": "mission", "name": "../../etc/passwd", "content": "# hi"})["message"]
    assert "empty or larger" in _first_event(client, sid, {"type": "mission", "name": "a.md", "content": "   "})["message"]
    assert "empty or larger" in _first_event(client, sid, {"type": "mission", "name": "a.md", "content": "x" * 250_000})["message"]


def test_mission_needs_a_model(client, tmp_path):
    ws = tmp_path / "nomodel"
    ws.mkdir()
    pid = client.post("/api/projects", json={"path": str(ws)}).json()["id"]
    sid = client.post("/api/sessions", json={"project_id": pid}).json()["id"]
    assert "No model selected" in _first_event(client, sid, {"type": "mission", "name": "a.md", "content": "# x"})["message"]


def test_missions_endpoint_and_zeus_folder_round_trip(client, tmp_path):
    import json as _j
    pid, sid = _mission_session(client, tmp_path)
    from app.database.db import Database
    db: Database = client.app.state.db
    db.x("INSERT INTO missions(session_id,name,title,objective,criteria,status,round,max_rounds,plan,log,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
         (sid, "todo.md", "Todo", "Build it", _j.dumps([{"id": 1, "text": "works", "met": True, "evidence": "ok"}]), "accepted", 2, 5, _j.dumps({"model": "m"}), _j.dumps([{"round": 1}]), 1.0, 2.0))
    got = client.get(f"/api/sessions/{sid}/missions").json()
    assert got[0]["title"] == "Todo" and got[0]["criteria"][0]["met"] is True and got[0]["plan"] == {"model": "m"} and got[0]["log"] == [{"round": 1}]
    assert client.get("/api/sessions/9999/missions").status_code == 404
    client.app.state.zfiles.write_session(sid)
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    c2 = TestClient(create_app(str(tmp_path / "fresh.db")))                      # lost database: everything comes back from .zeus
    p2 = c2.get("/api/projects").json()[0]["id"]
    s2 = c2.get(f"/api/sessions?project_id={p2}").json()[0]["id"]
    m2 = c2.get(f"/api/sessions/{s2}/missions").json()
    assert len(m2) == 1 and m2[0]["status"] == "accepted" and m2[0]["round"] == 2 and m2[0]["criteria"][0]["text"] == "works"
