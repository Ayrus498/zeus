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
