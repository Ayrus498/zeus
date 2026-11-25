"""Engines (Ollama / llama.cpp) through the API: switching, per-engine models, running the agent on llama.cpp, the state file."""
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.api.main import create_app
from app.workspace.state import StateStore, sanitize
from app.workspace.zeusdir import zeus_home
from app.modelconnectors import registry as eng
from app.modelconnectors.llamacpp import provider as llamacpp

from ..gguf_util import make_gguf
from ..paths import FIXTURES

FAKE = FIXTURES / "fake_llama_server.py"
MODEL = "qwen2.5-coder-1.5b-instruct-q4_k_m"
REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(llamacpp.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    models = tmp_path / "models"
    make_gguf(models / f"{MODEL}.gguf")
    with TestClient(create_app(tmp_path / "z.db")) as c:
        c.put("/api/settings", json={"ollama_url": "http://127.0.0.1:9"})              # no Ollama here: tests must not depend on the real one
        c.put("/api/llamacpp/config", json={"binary": str(FAKE), "models_dirs": [str(models)]})
        c.tmp = tmp_path
        yield c


def project(api, name="proj"):
    d = api.tmp / name
    d.mkdir(exist_ok=True)
    return api.post("/api/projects", json={"path": str(d)}).json()


def run_prompt(api, sid, text, timeout=60):
    evs = []
    with api.websocket_connect(f"/ws/sessions/{sid}") as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"type": "prompt", "content": text})
        t0 = time.time()
        while time.time() - t0 < timeout:
            e = ws.receive_json()
            evs.append(e)
            if e["type"] == "run_end":
                return evs
    raise AssertionError("no run_end: " + str([e["type"] for e in evs]))


# ---------------------------------------------------------------- registry and recommendation
def test_proxy_follows_the_engine_of_the_running_conversation():
    class P:
        def __init__(self, n): self.n = n

    class DB:
        def __init__(self): self.s = {}
        def get_setting(self, k, d=None): return self.s.get(k, d)
        def set_setting(self, k, v): self.s[k] = v

    e = eng.Engines(DB(), P("ollama"), P("llama"))
    proxy = eng.ProviderProxy(e)
    assert proxy.n == "ollama" and e.default() == "ollama"
    e.set_default("llamacpp")
    assert proxy.n == "llama"
    token = eng.current_engine.set("ollama")                                             # a conversation started on Ollama keeps using it
    try:
        assert proxy.n == "ollama" and e.get().n == "ollama"
    finally:
        eng.current_engine.reset(token)
    assert e.get("ollama").n == "ollama" and e.name_of({"engine": "ollama"}) == "ollama" and e.name_of({"engine": "bogus"}) == "llamacpp"
    with pytest.raises(ValueError):
        e.set_default("bogus")
    e.remember_model("llamacpp", "m.gguf"); e.remember_model("bogus", "x")
    assert e.last_model("llamacpp") == "m.gguf" and e.last_model("bogus") is None


def test_recommend_explains_its_choice():
    up, down = {"connected": True}, {"connected": False}
    assert eng.recommend(up, down, {"ollama": 3})["engine"] == "ollama"
    assert eng.recommend(down, up, {"llamacpp": 1})["engine"] == "llamacpp"
    assert "Neither engine" in eng.recommend(down, down, {})["reason"]
    r = eng.recommend(up, up, {"ollama": 0, "llamacpp": 2})
    assert r["engine"] == "llamacpp" and "2 GGUF models" in r["reason"]
    assert eng.recommend(up, up, {"ollama": 4, "llamacpp": 0})["engine"] == "ollama"
    assert "Both engines are ready" in eng.recommend(up, up, {"ollama": 1, "llamacpp": 1})["reason"]


# ---------------------------------------------------------------- the engines endpoint and models per engine
def test_engines_endpoint_describes_both(api):
    j = api.get("/api/engines").json()
    by = {e["id"]: e for e in j["engines"]}
    assert j["default"] == "ollama" and by["ollama"]["available"] is False and by["ollama"]["error"]
    assert by["llamacpp"]["available"] is True and by["llamacpp"]["version"] == "b12345" and by["llamacpp"]["models"] == 1
    assert by["llamacpp"]["devices"][0]["name"] == "NVIDIA Fake GPU" and by["llamacpp"]["server"]["running"] is False
    assert j["recommendation"]["engine"] == "llamacpp"


def test_models_and_info_depend_on_the_engine(api):
    assert api.get("/api/models", params={"engine": "ollama"}).status_code == 503              # Ollama is unreachable here
    rows = api.get("/api/models", params={"engine": "llamacpp"}).json()
    assert [r["name"] for r in rows] == [MODEL] and rows[0]["quantization"] == "Q4_K_M"
    info = api.get("/api/models/info", params={"engine": "llamacpp", "name": MODEL, "num_ctx": 8192}).json()
    assert info["tools_supported"] is True and info["context_length"] == 32768 and info["size"] == rows[0]["size"] and "context_check" in info
    assert api.get("/api/models/info", params={"engine": "llamacpp", "name": "ghost"}).status_code == 404
    assert api.get("/api/models", params={"engine": "tensorflow"}).status_code == 400
    api.put("/api/engine", json={"engine": "llamacpp"})
    assert [r["name"] for r in api.get("/api/models").json()] == [MODEL]                      # no parameter: the default engine


def test_switching_engine_moves_the_session_and_remembers_models_per_engine(api):
    p = project(api)
    s = api.post("/api/sessions", json={"project_id": p["id"], "model": "qwen3:8b"}).json()
    assert s["engine"] == "ollama" and s["model"] == "qwen3:8b"
    r = api.put("/api/engine", json={"engine": "llamacpp", "session_id": s["id"]}).json()
    now = next(x for x in api.get("/api/sessions", params={"project_id": p["id"]}).json() if x["id"] == s["id"])
    assert r["default"] == "llamacpp" and now["engine"] == "llamacpp" and now["model"] is None      # nothing used on llama.cpp yet
    api.patch(f"/api/sessions/{s['id']}", json={"model": MODEL})
    api.put("/api/engine", json={"engine": "ollama", "session_id": s["id"]})
    back = api.get("/api/sessions", params={"project_id": p["id"]}).json()[0]
    assert back["engine"] == "ollama" and back["model"] == "qwen3:8b"                                # restored
    api.put("/api/engine", json={"engine": "llamacpp", "session_id": s["id"]})
    again = api.get("/api/sessions", params={"project_id": p["id"]}).json()[0]
    assert again["engine"] == "llamacpp" and again["model"] == MODEL
    assert api.put("/api/engine", json={"engine": "nope"}).status_code == 400
    assert api.put("/api/engine", json={"engine": "ollama", "session_id": 999}).status_code == 404


def test_new_sessions_use_the_default_engine_and_its_last_model(api):
    p = project(api)
    api.put("/api/engine", json={"engine": "llamacpp"})
    s1 = api.post("/api/sessions", json={"project_id": p["id"]}).json()
    assert s1["engine"] == "llamacpp" and s1["model"] is None
    api.patch(f"/api/sessions/{s1['id']}", json={"model": MODEL})
    s2 = api.post("/api/sessions", json={"project_id": p["id"]}).json()
    assert s2["engine"] == "llamacpp" and s2["model"] == MODEL
    s3 = api.post("/api/sessions", json={"project_id": p["id"], "engine": "ollama", "model": "m"}).json()
    assert s3["engine"] == "ollama"
    assert api.patch(f"/api/sessions/{s1['id']}", json={"engine": "bogus"}).status_code == 400


# ---------------------------------------------------------------- running the agent on llama.cpp
def test_a_prompt_runs_on_llama_cpp_and_streams_the_answer(api):
    p = project(api)
    api.put("/api/engine", json={"engine": "llamacpp"})
    s = api.post("/api/sessions", json={"project_id": p["id"], "model": MODEL}).json()
    evs = run_prompt(api, s["id"], "say hello")
    types = [e["type"] for e in evs]
    assert "".join(e["text"] for e in evs if e["type"] == "token") == "Hello from fake llama" and types[-1] == "run_end" and evs[-1]["status"] == "completed"
    assert "model_loading" in types and "model_ready" in types
    u = next(e for e in evs if e["type"] == "usage")
    assert u["prompt_tokens"] == 11 and u["output_tokens"] == 7
    assert api.get("/api/llamacpp").json()["server"]["running"] is True
    assert [m["role"] for m in api.get(f"/api/sessions/{s['id']}/messages").json()] == ["user", "assistant"]


def test_the_agent_creates_a_file_through_a_native_llama_cpp_tool_call(api):
    p = project(api)
    api.put(f"/api/projects/{p['id']}/permissions", json={"mode": "trusted"})
    api.put("/api/engine", json={"engine": "llamacpp"})
    s = api.post("/api/sessions", json={"project_id": p["id"], "model": MODEL}).json()
    evs = run_prompt(api, s["id"], "TOOL please create hello.py")
    assert (Path(p["path"]) / "hello.py").read_text() == "print(1)"
    assert [e["name"] for e in evs if e["type"] == "tool_call"] == ["create_file"]
    assert evs[-1]["status"] == "completed" and any(e["type"] == "file_change" for e in evs)
    assert "Done" not in "" and "".join(e["text"] for e in evs if e["type"] == "token").endswith("Hello from fake llama")      # answered after the tool result
    msgs = api.get(f"/api/sessions/{s['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"] and msgs[1]["tool_calls"][0]["function"]["name"] == "create_file"


def test_auto_mode_is_not_available_with_llama_cpp(api):
    p = project(api)
    s = api.post("/api/sessions", json={"project_id": p["id"], "engine": "llamacpp", "model": "auto"}).json()
    with api.websocket_connect(f"/ws/sessions/{s['id']}") as ws:
        ws.receive_json()
        ws.send_json({"type": "prompt", "content": "hi"})
        e = ws.receive_json()
    assert e["type"] == "error" and "Auto mode picks among Ollama models" in e["message"]


def test_two_sessions_can_use_different_engines(api):
    p = project(api)
    a = api.post("/api/sessions", json={"project_id": p["id"], "engine": "llamacpp", "model": MODEL}).json()
    b = api.post("/api/sessions", json={"project_id": p["id"], "engine": "ollama", "model": "qwen3:8b"}).json()
    ok = run_prompt(api, a["id"], "hi")
    assert ok[-1]["status"] == "completed"                                                           # llama.cpp works...
    bad = run_prompt(api, b["id"], "hi")
    assert bad[-1]["status"] == "error" and any(e["type"] == "error" for e in bad)                   # ...while the Ollama one reports Ollama's error


# ---------------------------------------------------------------- the llama.cpp window's endpoints
def test_llamacpp_overview_config_and_validation(api):
    j = api.get("/api/llamacpp").json()
    assert j["status"]["connected"] and [m["name"] for m in j["models"]] == [MODEL] and j["config"]["binary"] == str(FAKE) and j["managed_dir"].endswith("models")
    r = api.put("/api/llamacpp/config", json={"defaults": {"ctx": 4096, "flash_attn": "on"}, "model": {"name": MODEL, "settings": {"ngl": "12"}}}).json()
    assert r["config"]["defaults"]["ctx"] == 4096 and r["config"]["models"][MODEL] == {"ngl": "12"}
    assert api.put("/api/llamacpp/config", json={"binary": "relative/llama-server"}).status_code == 400
    assert api.put("/api/llamacpp/config", json={"models_dirs": ["rel"]}).status_code == 400
    assert api.put("/api/llamacpp/config", json={"model": {"name": MODEL, "settings": None}}).json()["config"]["models"] == {}


def test_start_and_stop_the_server_from_the_window(api):
    assert api.post("/api/llamacpp/server/start", json={"model": "ghost"}).status_code == 404
    assert api.post("/api/llamacpp/server/start", json={"model": MODEL}).json() == {"starting": MODEL}
    for _ in range(100):
        o = api.get("/api/llamacpp").json()
        if o["server"]["running"]:
            break
        time.sleep(0.1)
    assert o["server"]["running"] and o["server"]["model"] == MODEL and o["config"]["last_model"] == MODEL
    assert api.get("/api/metrics").json()["llamacpp"]["running"] is True
    after = api.post("/api/llamacpp/server/stop").json()
    assert after["server"]["running"] is False


def test_start_failure_is_shown_with_the_log(api):
    bad = api.tmp / "bad-server"
    bad.write_text("#!/bin/sh\nif [ \"$1\" = \"--version\" ] || [ \"$1\" = \"--help\" ] || [ \"$1\" = \"--list-devices\" ]; then echo version: 1 >&2; exit 0; fi\necho 'boom: cannot allocate' >&2\nexit 1\n")
    bad.chmod(0o755)
    api.put("/api/llamacpp/config", json={"binary": str(bad)})
    api.post("/api/llamacpp/server/start", json={"model": MODEL})
    for _ in range(100):
        o = api.get("/api/llamacpp").json()
        if o["error"]:
            break
        time.sleep(0.1)
    assert "stopped while loading the model" in o["error"] and "cannot allocate" in o["log"]


def test_hugging_face_listing_and_download_endpoints(api, monkeypatch):
    payload = make_gguf(api.tmp / "src.gguf", pad=2000).read_bytes()

    def h(req):
        if req.url.path.startswith("/api/models/"):
            return httpx.Response(200, json={"siblings": [{"rfilename": "tiny-Q4_K_M.gguf", "size": len(payload)}]})
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(llamacpp.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(h), **kw))
    j = api.get("/api/llamacpp/hf", params={"repo": "owner/tiny-GGUF"}).json()
    assert j["files"][0]["file"] == "tiny-Q4_K_M.gguf" and j["files"][0]["installed"] is False
    assert api.get("/api/llamacpp/hf", params={"repo": "bad"}).status_code == 400
    for bad in ({"repo": "owner/x", "file": "../evil.gguf"}, {"repo": "nope", "file": "a.gguf"}, {"repo": "o/x", "file": "a.txt"}):
        assert api.post("/api/llamacpp/download", json=bad).status_code == 400
    d = api.post("/api/llamacpp/download", json={"repo": "owner/tiny-GGUF", "file": "tiny-Q4_K_M.gguf"}).json()
    assert d["model"] == "hf:owner/tiny-GGUF/tiny-Q4_K_M.gguf"
    for _ in range(100):
        o = api.get("/api/llamacpp").json()
        if "tiny-Q4_K_M" in [m["name"] for m in o["models"]]:
            break
        time.sleep(0.1)
    assert "tiny-Q4_K_M" in [m["name"] for m in o["models"]] and next(m for m in o["models"] if m["name"] == "tiny-Q4_K_M")["managed"] is True
    assert o["downloads"][0]["status"] == "completed"
    assert api.get("/api/ollama/overview").json()["downloads"] == []                                   # the Ollama window does not list llama.cpp downloads
    assert api.request("DELETE", "/api/llamacpp/models", json={"model": MODEL}).status_code == 400                # not downloaded by Zeus-Agent: refused
    assert "tiny-Q4_K_M" not in [m["name"] for m in api.request("DELETE", "/api/llamacpp/models", json={"model": "tiny-Q4_K_M"}).json()["models"]]


# ---------------------------------------------------------------- the state file and settings mirror
def test_state_json_remembers_the_last_conversation(api):
    p = project(api)
    api.put("/api/engine", json={"engine": "llamacpp"})
    s = api.post("/api/sessions", json={"project_id": p["id"], "model": MODEL, "num_ctx": 12288}).json()
    api.patch(f"/api/sessions/{s['id']}", json={"model": MODEL, "title": "Add a login page"})
    st = json.loads((zeus_home() / "state.json").read_text())
    assert st["engine"] == "llamacpp" and st["model"] == MODEL and st["num_ctx"] == 12288 and st["permission_mode"] == "approval"
    assert st["project"]["path"] == p["path"] and st["session"]["uid"] == s["uid"] and st["session"]["title"] == "Add a login page"
    assert st["client"] == "gui" and st["models"]["llamacpp"] == MODEL and (zeus_home() / "state.json").stat().st_mode & 0o777 == 0o600


def test_the_cli_marks_itself_and_open_session_updates_the_pointer(api):
    p = project(api)
    a = api.post("/api/sessions", json={"project_id": p["id"], "model": "m1"}).json()
    b = api.post("/api/sessions", json={"project_id": p["id"], "model": "m2"}).json()
    assert json.loads((zeus_home() / "state.json").read_text())["session"]["uid"] == b["uid"]
    assert api.post(f"/api/sessions/{a['id']}/open", headers={"X-Zeus-Client": "cli"}).json() == {"ok": True}
    st = json.loads((zeus_home() / "state.json").read_text())
    assert st["session"]["uid"] == a["uid"] and st["client"] == "cli" and st["model"] == "m1"
    assert api.post("/api/sessions/999/open").status_code == 404


def test_state_endpoint_resolves_what_can_be_resumed(api):
    assert api.get("/api/state").json()["resolved"] is None
    p = project(api)
    api.put("/api/engine", json={"engine": "llamacpp"})
    s = api.post("/api/sessions", json={"project_id": p["id"], "model": MODEL}).json()
    j = api.get("/api/state").json()
    r = j["resolved"]
    assert j["path"].endswith("state.json") and r["session"]["id"] == s["id"] and r["project"]["path"] == p["path"] and r["engine"] == "llamacpp" and r["model"] == MODEL
    api.delete(f"/api/sessions/{s['id']}")
    r2 = api.get("/api/state").json()["resolved"]
    assert r2["session"] is None and r2["project"]["path"] == p["path"]                                  # the folder can still be opened
    Path(p["path"]).rmdir()
    assert api.get("/api/state").json()["resolved"] is None                                              # nothing left to resume


def test_state_file_is_treated_as_untrusted_input(tmp_path):
    f = tmp_path / "state.json"
    store = StateStore(tmp_path)
    f.write_text("not json")
    assert store.read() == {}
    f.write_text(json.dumps({"engine": ["x"], "model": 5, "num_ctx": -1, "project": {"path": 3}, "session": {"uid": "u" * 500, "id": True}, "models": {"a": 1, "b": "ok"}, "evil": "x",
                             "client": "cli"}))
    cur = store.read()
    assert cur == {"client": "cli", "models": {"b": "ok"}, "session": {"uid": "u" * 64, "id": None, "title": ""}}
    f.write_text("x" * 200_000)
    assert store.read() == {}
    out = store.write({"engine": "ollama", "models": {"ollama": "m"}})
    out = store.write({"models": {"llamacpp": "g"}, "engine": None})
    assert out["models"] == {"ollama": "m", "llamacpp": "g"} and "engine" not in out and out["version"] == config.VERSION
    assert sanitize("nope") == {}


def test_engine_settings_are_mirrored_to_settings_json_and_restored(api, tmp_path):
    api.put("/api/engine", json={"engine": "llamacpp"})
    api.put("/api/llamacpp/config", json={"defaults": {"ngl": "20"}})
    doc = json.loads((zeus_home() / "settings.json").read_text())
    assert doc["engine"] == "llamacpp" and isinstance(doc["llamacpp"], dict) and doc["llamacpp"]["defaults"]["ngl"] == "20" and doc["llamacpp"]["binary"] == str(FAKE)
    with TestClient(create_app(tmp_path / "other.db")) as fresh:                                         # a new database restores them from the file
        assert fresh.get("/api/settings").json()["engine"] == "llamacpp"
        assert fresh.get("/api/llamacpp").json()["config"]["defaults"]["ngl"] == "20"


def test_session_files_keep_their_engine(api):
    p = project(api)
    s = api.post("/api/sessions", json={"project_id": p["id"], "engine": "llamacpp", "model": MODEL}).json()
    api.app.state.zfiles.flush()
    f = Path(p["path"]) / ".zeus" / "sessions" / f"{s['uid']}.json"
    assert json.loads(f.read_text())["engine"] == "llamacpp"
