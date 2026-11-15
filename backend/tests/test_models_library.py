import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.database.db import Database
from app.providers import catalog
from app.providers.downloads import DownloadManager
from app.providers.ollama import OllamaError


class FakeProvider:
    base_url = "http://fake"

    def __init__(self, events=None, error=None, hang=False):
        self.events, self.error, self.hang = events or [], error, hang

    async def pull_stream(self, model):
        for e in self.events:
            yield e
            await asyncio.sleep(0)
        if self.hang:
            await asyncio.sleep(30)
        if self.error:
            raise OllamaError(self.error)


async def settle(dm, did, timeout=3):
    t = dm.tasks.get(did)
    if t:
        await asyncio.wait_for(asyncio.shield(t), timeout)


def row(dm, did):
    return next(r for r in dm.list() if r["id"] == did)


async def test_download_success_aggregates_layers(db):
    dm = DownloadManager(db)
    ev = [{"status": "pulling manifest"},
          {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 40},
          {"status": "pulling bbb", "digest": "bbb", "total": 300, "completed": 0},
          {"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 100},
          {"status": "pulling bbb", "digest": "bbb", "total": 300, "completed": 300},
          {"status": "success"}]
    d = dm.start(FakeProvider(ev), "m:1b")
    await settle(dm, d["id"])
    r = row(dm, d["id"])
    assert r["status"] == "completed" and r["total"] == 400 and r["completed"] == 400 and r["percent"] == 100.0 and r["error"] is None


async def test_download_error_is_stored(db):
    dm = DownloadManager(db)
    d = dm.start(FakeProvider([{"status": "pulling manifest"}], error="pull model manifest: file does not exist"), "nope:1b")
    await settle(dm, d["id"])
    r = row(dm, d["id"])
    assert r["status"] == "failed" and "does not exist" in r["error"]


async def test_download_cancel_and_dedupe(db):
    dm = DownloadManager(db)
    p = FakeProvider([{"status": "pulling x", "digest": "x", "total": 10, "completed": 1}], hang=True)
    d = dm.start(p, "big:70b")
    assert dm.start(p, "big:70b")["id"] == d["id"]          # same model while active -> same download
    await asyncio.sleep(0.05)
    assert await dm.cancel(d["id"]) is True
    assert row(dm, d["id"])["status"] == "cancelled"
    dm.dismiss(d["id"])
    assert not dm.list()


def test_interrupted_downloads_marked_on_startup(tmp_path):
    db = Database(tmp_path / "z.db")
    db.x("INSERT INTO downloads(model,status,started,updated) VALUES('a','running',0,0)")
    dm = DownloadManager(db)                                  # simulates a restart
    r = dm.list()[0]
    assert r["status"] == "interrupted" and "Retry" in r["error"]


def test_catalog_is_well_formed():
    names = [m["name"] for m in catalog.CATALOG]
    assert len(names) == len(set(names)) and len(names) >= 25
    for m in catalog.CATALOG:
        assert m["fit"] in ("best", "good", "limited", "no") and m["tags"] and m["description"] and m["best_for"]
        assert set(m["purposes"]) <= set(catalog.PURPOSES)
        for t in m["tags"]:
            assert t["tag"].startswith(m["name"] + ":") and t["size_gb"] > 0
    assert catalog.tag_installed({"tag": "qwen3:8b", "latest": True}, "qwen3", {"qwen3:latest"})
    assert not catalog.tag_installed({"tag": "qwen3:14b", "latest": False}, "qwen3", {"qwen3:latest"})


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "z.db")))


def test_catalog_and_overview_endpoints(client):
    j = client.get("/api/ollama/catalog").json()
    assert j["models"] and "coding" in j["purposes"] and j["library_url"].startswith("https://ollama.com")
    o = client.get("/api/ollama/overview").json()
    assert {"connected", "installed", "loaded", "downloads", "hardware"} <= set(o)


def test_pull_validates_and_requires_ollama(client):
    assert client.post("/api/ollama/pull", json={"model": "bad name; rm -rf /"}).status_code == 400
    assert client.post("/api/ollama/pull", json={"model": ""}).status_code == 400
    # nothing listens on the default Ollama port in the test sandbox -> clear 503, not a hang or a crash
    client.put("/api/settings", json={"ollama_url": "http://127.0.0.1:9"})
    r = client.post("/api/ollama/pull", json={"model": "qwen3:8b"})
    assert r.status_code == 503 and "ollama serve" in r.json()["detail"]


def test_unload_reports_error(client):
    client.put("/api/settings", json={"ollama_url": "http://127.0.0.1:9"})
    r = client.post("/api/ollama/unload", json={"model": "qwen3:8b"})
    assert r.status_code == 502 and "Could not unload" in r.json()["detail"]


@pytest.mark.parametrize("url,ok", [
    ("https://ollama.com/download", True), ("https://ollama.com/library/qwen3", True), ("https://docs.ollama.com/", True),
    ("https://github.com/ollama/ollama/blob/main/docs/linux.md", True),
    ("https://ollama.com.evil.com/x", False), ("https://evil.com/?https://ollama.com", False), ("http://ollama.com", False),
    ("https://github.com/ollamaevil/x", False), ("file:///etc/passwd", False), ("https://ollama.com/a b", False),
    ("https://ollama.com/x\n--help", False), ("javascript:alert(1)", False),
])
def test_open_url_allowlist(client, monkeypatch, url, ok):
    import app.api.main as m
    opened = []
    monkeypatch.setattr(m.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    monkeypatch.setattr(m.shutil, "which", lambda n: "/usr/bin/" + n)
    r = client.post("/api/open-url", json={"url": url})
    assert (r.status_code == 200) is ok and bool(opened) is ok
