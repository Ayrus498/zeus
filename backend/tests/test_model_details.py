"""Live model details (ollama.com page parsing + cache/offline) and uninstall."""
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.providers import catalog, modelinfo
from app.providers.ollama import OllamaError

FIX = Path(__file__).parent / "fixtures" / "ollama_library_qwen3_coder.html"


def test_parse_real_page():
    d = modelinfo.parse(FIX.read_text(), "qwen3-coder")
    assert d["summary"].startswith("Alibaba") and "agentic" in d["summary"]
    assert d["capabilities"] == ["tools"]
    assert {t["tag"] for t in d["tags"]} >= {"qwen3-coder:30b", "qwen3-coder:480b"}
    t30 = next(t for t in d["tags"] if t["tag"] == "qwen3-coder:30b")
    assert t30["size"] == "19GB" and t30["context"] == "256K"
    assert "**Qwen3-Coder**" in d["readme"] and "#### Overview" in d["readme"] and "- Exceptional agentic" in d["readme"]
    assert "<" not in d["readme"] and "```" in d["readme"]


def test_parse_empty_page_gives_empty_details():
    d = modelinfo.parse("<html><body>nothing</body></html>", "x")
    assert d["summary"] == "" and d["readme"] == "" and d["tags"] == []


def test_catalog_has_about_and_examples_for_every_model():
    for m in catalog.CATALOG:
        assert len(m["about"]) > 60, m["name"]
        assert m["examples"] and all(e["prompt"] and e["shows"] for e in m["examples"]), m["name"]


class FakeDB:
    def __init__(self):
        self.s = {}

    def set_setting(self, k, v):
        self.s[k] = v

    def get_setting(self, k, d=None):
        return self.s.get(k, d)


REAL_CLIENT = httpx.AsyncClient


def _mock(monkeypatch, handler):
    real = REAL_CLIENT
    monkeypatch.setattr(modelinfo.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    modelinfo._mem.clear()


async def test_fetch_live_then_cache_then_offline_fallback(monkeypatch):
    calls = []

    def ok(req):
        calls.append(str(req.url))
        return httpx.Response(200, text=FIX.read_text())
    db = FakeDB()
    _mock(monkeypatch, ok)
    a = await modelinfo.fetch("qwen3-coder", db)
    assert a["source"] == "live" and a["summary"]
    b = await modelinfo.fetch("qwen3-coder", db)
    assert b["source"] == "cache" and len(calls) == 1
    c = await modelinfo.fetch("qwen3-coder", db, force=True)
    assert c["source"] == "live" and len(calls) == 2

    def down(req):
        raise httpx.ConnectError("no network")
    _mock(monkeypatch, down)
    d = await modelinfo.fetch("qwen3-coder", db, force=True)
    assert d["source"] == "offline-cache" and d["summary"] and "ollama.com" in d["error"]
    e = await modelinfo.fetch("llama3.1", FakeDB(), force=True)
    assert e["source"] == "offline" and e["error"] and e["tags"] == []


async def test_fetch_404_and_bad_family(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(404, text="nope"))
    r = await modelinfo.fetch("hf-only-model", FakeDB(), force=True)
    assert r["source"] == "offline" and "not in the public" in r["error"]
    for bad in ("../etc", "a/b/c", "", "Q W", "x" * 200, "/a", "a/"):
        with pytest.raises(ValueError):
            await modelinfo.fetch(bad)


def test_library_endpoint_validates(client, monkeypatch):
    assert client.get("/api/ollama/library/bad%20name").status_code == 400
    _mock(monkeypatch, lambda r: httpx.Response(200, text=FIX.read_text()))
    j = client.get("/api/ollama/library/qwen3-coder").json()
    assert j["source"] == "live" and j["tags"]


def test_delete_endpoint(client, monkeypatch):
    calls = []

    async def fake_delete(self, model):
        calls.append(("delete", model))

    async def fake_unload(self, model):
        calls.append(("unload", model))
    from app.providers.ollama import OllamaProvider
    monkeypatch.setattr(OllamaProvider, "delete", fake_delete)
    monkeypatch.setattr(OllamaProvider, "unload", fake_unload)
    assert client.post("/api/ollama/delete", json={"model": "qwen3:8b"}).json() == {"ok": True}
    assert calls == [("unload", "qwen3:8b"), ("delete", "qwen3:8b")]
    assert client.post("/api/ollama/delete", json={"model": "bad name!"}).status_code == 400

    async def boom(self, model):
        raise OllamaError("model 'x' not found")
    monkeypatch.setattr(OllamaProvider, "delete", boom)
    r = client.post("/api/ollama/delete", json={"model": "x:1"})
    assert r.status_code == 502 and "not found" in r.json()["detail"]


def test_delete_refused_while_downloading(client, monkeypatch):
    from app.providers.ollama import OllamaProvider
    called = []

    async def fake_delete(self, model):
        called.append(model)
    monkeypatch.setattr(OllamaProvider, "delete", fake_delete)
    monkeypatch.setattr("app.providers.downloads.DownloadManager.list",
                        lambda self: [{"model": "big:70b", "status": "running"}])
    r = client.post("/api/ollama/delete", json={"model": "big:70b"})
    assert r.status_code == 409 and not called
