"""The Ollama library list and search (so models outside the built-in guide, like `ornith`, can be found and pulled)."""
from ..paths import FIXTURES
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.modelconnectors.ollama import library
from app.modelconnectors import modelinfo

FIX = FIXTURES
LIST = (FIX / "ollama_library_list.html").read_text()
SEARCH = (FIX / "ollama_search_ornith.html").read_text()
REAL_CLIENT = httpx.AsyncClient


def test_count_understands_pull_counts():
    assert library.count("44.4M") == 44_400_000 and library.count("797.4K") == 797_400 and library.count("1,234") == 1234
    assert library.count("2B") == 2_000_000_000 and library.count("") == 0 and library.count("n/a") == 0


def test_parse_library_reads_every_field():
    es = {e["name"]: e for e in library.parse_library(LIST)}
    assert set(es) == {"llama3.1", "qwen3", "ornith", "ornith-1.5", "nomic-embed-text"}
    o = es["ornith"]
    assert o["description"] == "A self-improving family of open-source models for agentic coding"
    assert o["capabilities"] == ["tools", "thinking"] and o["sizes"] == ["9b", "35b"]
    assert o["pulls"] == "797.4K" and o["pulls_n"] == 797_400 and o["tags"] == 9 and o["updated"] == "4 months ago"
    assert o["community"] is False and o["url"] == "https://ollama.com/library/ornith"
    assert es["ornith-1.5"]["capabilities"] == ["vision", "tools", "thinking"] and "122b" in es["ornith-1.5"]["sizes"]
    assert es["qwen3"]["sizes"][0] == "0.6b" and es["qwen3"]["pulls_n"] > 1_000_000


def test_parse_search_separates_official_and_community_models():
    es = library.parse_search(SEARCH)
    names = [e["name"] for e in es]
    assert "ornith" in names and "ornith-1.5" in names and "jetelain/Ornith-1.5-122b" in names and "s1gnature/ornith" in names
    o = next(e for e in es if e["name"] == "ornith")
    assert o["community"] is False and o["capabilities"] == ["tools", "thinking"] and o["sizes"] == ["9b", "35b"] and o["tags"] == 9 and o["pulls_n"] == 797_438 or o["pulls_n"] == 797_400
    c = next(e for e in es if e["name"] == "jetelain/Ornith-1.5-122b")
    assert c["community"] is True and c["url"] == "https://ollama.com/jetelain/Ornith-1.5-122b" and c["capabilities"] == ["vision"] and c["description"] == ""


def test_parse_of_an_unreadable_page_is_empty():
    assert library.parse_library("<html>nothing</html>") == [] and library.parse_search("<html>nothing</html>") == []


class FakeDB:
    def __init__(self):
        self.s = {}

    def set_setting(self, k, v):
        self.s[k] = v

    def get_setting(self, k, d=None):
        return self.s.get(k, d)


def _mock(monkeypatch, handler):
    monkeypatch.setattr(library.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    library._mem.clear()


def _big_list(n=25):
    items = "".join(f'<li class="x"><a href="/library/m{i}" class="g"><p class="max-w-lg x">model {i}</p></a></li>' for i in range(n))
    return f"<html><body><ul>{items}</ul></body></html>"


async def test_fetch_library_live_then_cache_then_offline_copy(monkeypatch):
    calls, db = [], FakeDB()

    def ok(req):
        calls.append(str(req.url))
        return httpx.Response(200, text=_big_list())

    _mock(monkeypatch, ok)
    a = await library.fetch_library(db)
    assert a["source"] == "live" and len(a["models"]) == 25 and calls == ["https://ollama.com/library"]
    b = await library.fetch_library(db)
    assert b["source"] == "cache" and len(calls) == 1                      # served from memory
    c = await library.fetch_library(db, force=True)
    assert c["source"] == "live" and len(calls) == 2                       # the refresh button asks again

    def down(req):
        raise httpx.ConnectError("no network")

    _mock(monkeypatch, down)
    d = await library.fetch_library(db)
    assert d["source"] == "offline-cache" and len(d["models"]) == 25 and "Could not reach" in d["error"]
    e = await library.fetch_library(FakeDB())
    assert e["source"] == "offline" and e["models"] == [] and e["error"]


async def test_fetch_library_rejects_a_page_it_cannot_read(monkeypatch):
    _mock(monkeypatch, lambda req: httpx.Response(200, text="<html>maintenance</html>"))
    r = await library.fetch_library(FakeDB())
    assert r["models"] == [] and "could not read" in r["error"]


async def test_search_sends_only_the_query_and_caches(monkeypatch):
    seen = []

    def ok(req):
        seen.append((req.url.host, req.url.path, dict(req.url.params), req.headers.get("cookie"), req.headers.get("authorization")))
        return httpx.Response(200, text=SEARCH)

    _mock(monkeypatch, ok)
    r = await library.search("  ornith  ")
    assert r["source"] == "live" and r["query"] == "ornith" and any(m["name"] == "ornith" for m in r["models"])
    assert seen == [("ollama.com", "/search", {"q": "ornith"}, None, None)]       # nothing but the typed text
    assert (await library.search("ORNITH"))["source"] == "cache" and len(seen) == 1
    assert (await library.search("a"))["source"] == "none" and len(seen) == 1       # too short: no request at all


async def test_search_offline_is_reported(monkeypatch):
    def down(req):
        raise httpx.ConnectError("no network")

    _mock(monkeypatch, down)
    r = await library.search("ornith")
    assert r["models"] == [] and r["source"] == "offline" and "Could not reach" in r["error"]


def test_api_endpoints_and_namespaced_details(tmp_path, monkeypatch):
    async def fake_list(db=None, force=False):
        return {"models": [{"name": "ornith", "description": "d", "capabilities": ["tools"], "sizes": ["9b"], "pulls": "1K", "pulls_n": 1000,
                            "tags": 9, "updated": "1d", "community": False, "url": "u"}], "fetched_at": 1, "source": "live"}

    async def fake_search(q):
        return {"query": q, "models": [], "source": "live", "error": None}

    asked = []

    async def fake_fetch(family, db=None, force=False):
        asked.append(family)
        return {"family": family, "url": modelinfo.page_url(family), "summary": "s", "readme": "", "capabilities": [], "tags": [], "updated": None, "source": "live"}

    monkeypatch.setattr(library, "fetch_library", fake_list)
    monkeypatch.setattr(library, "search", fake_search)
    monkeypatch.setattr(modelinfo, "fetch", fake_fetch)
    with TestClient(create_app(tmp_path / "z.db")) as c:
        assert c.get("/api/ollama/library").json()["models"][0]["name"] == "ornith"
        assert c.get("/api/ollama/search", params={"q": "orn"}).json()["query"] == "orn"
        assert c.get("/api/ollama/library/Ornith").json()["family"] == "ornith"             # library names are lower-cased
        r = c.get("/api/ollama/library/Maxwell1500/Ornith-9b").json()
        assert r["family"] == "Maxwell1500/Ornith-9b" and r["url"] == "https://ollama.com/Maxwell1500/Ornith-9b"   # community names keep their case
    assert asked == ["ornith", "Maxwell1500/Ornith-9b"]


def test_details_page_url_for_library_and_community_models():
    assert modelinfo.page_url("ornith") == "https://ollama.com/library/ornith"
    assert modelinfo.page_url("maxwell1500/ornith-9b") == "https://ollama.com/maxwell1500/ornith-9b"
    assert modelinfo.FAMILY_RE.match("maxwell1500/ornith-9b") and not modelinfo.FAMILY_RE.match("../etc") and not modelinfo.FAMILY_RE.match("a/b/c")
