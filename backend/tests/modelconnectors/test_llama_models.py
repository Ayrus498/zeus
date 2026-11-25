"""llama.cpp models: Ollama's downloaded models are listed (read only), where each model came from, and what may be deleted."""
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.api.main import create_app
from app.core.database import Database
from app.modelhub import arena
from app.modelconnectors.llamacpp import provider as llamacpp
from app.modelconnectors.llamacpp import ollama_models as ollamastore
from app.modelconnectors.llamacpp.provider import LlamaCppError, LlamaCppProvider

from ..gguf_util import make_gguf
from ..paths import FIXTURES

FAKE = FIXTURES / "fake_llama_server.py"
REAL_CLIENT = httpx.AsyncClient
HEX = "a1" * 32
MB2 = 2 * 2**20


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(llamacpp.Path, "home", classmethod(lambda cls: tmp_path / "home"))


@pytest.fixture
def prov(tmp_path):
    p = LlamaCppProvider(Database(":memory:"))
    p.save_config({"binary": str(FAKE)})
    return p


def ollama_store(tmp_path, monkeypatch, models=("qwen3.5:4b",)):
    """A real-looking Ollama store whose model layer is a genuine (padded) GGUF file."""
    root = tmp_path / "ollama-store"
    (root / "blobs").mkdir(parents=True)
    for i, name in enumerate(models):
        model, tag = name.split(":")
        hexd = f"{i + 1:x}" * 64
        make_gguf(root / "blobs" / f"sha256-{hexd}", pad=MB2)
        mf = root / "manifests" / "registry.ollama.ai" / "library" / model / tag
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"layers": [{"mediaType": ollamastore.MODEL_LAYER, "digest": f"sha256:{hexd}", "size": (root / "blobs" / f"sha256-{hexd}").stat().st_size}]}))
    monkeypatch.setattr(ollamastore, "store_dirs", lambda *a, **k: [root])
    return root


# ------------------------------------------------------------------ Ollama's models
def test_ollama_models_are_listed_in_llama_cpp_mode_without_a_copy(prov, tmp_path, monkeypatch):
    root = ollama_store(tmp_path, monkeypatch, ("qwen3.5:4b", "qwq:32b"))
    rows = {m["name"]: m for m in prov.scan()}
    assert set(rows) == {"ollama/qwen3.5:4b", "ollama/qwq:32b"}
    m = rows["ollama/qwen3.5:4b"]
    assert m["source"] == "ollama" and m["path"].startswith(str(root / "blobs")) and m["removable"] is None and m["ollama_name"] == "qwen3.5:4b"
    assert m["family"] == "qwen2" and m["quantization"] == "Q4_K_M" and m["size"] > MB2 and m["managed"] is False
    assert not list((tmp_path / "data").glob("**/*.gguf"))                                           # nothing was copied anywhere


def test_the_ollama_models_can_be_switched_off(prov, tmp_path, monkeypatch):
    ollama_store(tmp_path, monkeypatch)
    assert [m["name"] for m in prov.scan()] == ["ollama/qwen3.5:4b"]
    prov.save_config({"ollama_models": False})
    assert prov.scan() == [] and prov.config()["ollama_models"] is False
    prov.save_config({"ollama_models": True})
    assert len(prov.scan()) == 1


def test_a_model_that_is_both_in_a_folder_and_in_ollamas_store_is_listed_once(prov, tmp_path, monkeypatch):
    root = ollama_store(tmp_path, monkeypatch)
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "same.gguf").symlink_to(next((root / "blobs").glob("sha256-*")))                       # the user linked Ollama's blob into their own folder
    prov.save_config({"models_dirs": [str(folder)]})
    rows = prov.scan()
    assert [m["name"] for m in rows] == ["same"] and rows[0]["source"] == "folder"


async def test_llama_server_is_started_on_ollamas_blob_and_answers(prov, tmp_path, monkeypatch):
    root = ollama_store(tmp_path, monkeypatch)
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    chunks = [c async for c in prov.chat_stream("ollama/qwen3.5:4b", [{"role": "user", "content": "hi"}], None, 4096)]
    await prov.shutdown()
    assert "Hello from fake llama" in "".join(c["message"].get("content", "") for c in chunks)
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert argv[argv.index("-m") + 1] == str(next((root / "blobs").glob("sha256-*"))) and argv[argv.index("--alias") + 1] == "ollama/qwen3.5:4b"


async def test_ollamas_models_are_never_deleted_by_zeus_agent(prov, tmp_path, monkeypatch):
    root = ollama_store(tmp_path, monkeypatch)
    blob = next((root / "blobs").glob("sha256-*"))
    with pytest.raises(LlamaCppError, match=r"belongs to Ollama.*ollama rm qwen3\.5:4b"):
        await prov.delete("ollama/qwen3.5:4b")
    assert blob.exists() and len(list((root / "manifests").rglob("*"))) > 0


# ------------------------------------------------------------------ where a model came from
def hf_entry(home: Path, repo="owner--tiny-GGUF", rev="abc123", file="tiny-Q4_K_M.gguf", pad=100, blob_name="deadbeef" * 8) -> tuple[Path, Path]:
    base = home / ".cache/huggingface/hub" / f"models--{repo}"
    (base / "blobs").mkdir(parents=True, exist_ok=True)
    (base / "refs").mkdir(exist_ok=True)
    (base / "snapshots" / rev).mkdir(parents=True, exist_ok=True)
    blob = base / "blobs" / blob_name
    make_gguf(blob, pad=pad)
    link = base / "snapshots" / rev / file
    link.symlink_to(os.path.relpath(blob, link.parent))
    (base / "refs" / "main").write_text(rev)
    return link, blob


def test_every_model_knows_where_it_came_from(prov, tmp_path):
    home = tmp_path / "home"
    make_gguf(llamacpp.managed_dir() / "dl.gguf")
    make_gguf(home / ".cache/llama.cpp/cached.gguf")
    hf_entry(home)
    mine = tmp_path / "mine"
    make_gguf(mine / "own.gguf")
    prov.save_config({"models_dirs": [str(mine)]})
    got = {m["name"]: m["source"] for m in prov.scan()}
    assert got == {"dl": "managed", "cached": "llama_cache", "tiny-Q4_K_M": "hf_cache", "own": "folder"}
    rows = {m["name"]: m for m in prov.scan()}
    assert rows["own"]["removable"] is None and rows["dl"]["removable"]["kind"] == "managed" and rows["cached"]["removable"]["kind"] == "llama_cache"
    plan = rows["tiny-Q4_K_M"]["removable"]
    assert plan["kind"] == "hf_cache" and len(plan["files"]) == 2 and plan["bytes"] > 100 and plan["files"][1].endswith("deadbeef" * 8)


# ------------------------------------------------------------------ deleting
async def test_deleting_a_download_removes_the_file_and_a_split_model_goes_whole(prov):
    d = llamacpp.managed_dir()
    for n in (1, 2):
        make_gguf(d / f"big-0000{n}-of-00002.gguf")
    one = make_gguf(d / "one.gguf")
    r = await prov.delete("one")
    assert not one.exists() and r["removed"] == [str(one)]
    plan = next(m for m in prov.scan() if m["name"].startswith("big"))["removable"]
    assert len(plan["files"]) == 2
    await prov.delete("big")
    assert list(d.glob("*.gguf")) == []


async def test_deleting_a_hugging_face_cache_model_removes_the_link_the_blob_and_the_empty_entry(prov, tmp_path):
    home = tmp_path / "home"
    link, blob = hf_entry(home)
    repo = link.parents[2]
    r = await prov.delete("tiny-Q4_K_M")
    assert not link.exists() and not link.is_symlink() and not blob.exists() and r["removed"] == [str(link), str(blob)]
    assert not repo.exists()                                                                         # no husk of snapshots/refs left behind
    assert (home / ".cache/huggingface/hub").is_dir()


async def test_a_blob_still_used_by_another_snapshot_file_is_kept(prov, tmp_path):
    home = tmp_path / "home"
    link, blob = hf_entry(home)
    other = link.parents[1] / "rev2" / "tiny-Q4_K_M.gguf"
    other.parent.mkdir()
    other.symlink_to(os.path.relpath(blob, other.parent))
    rows = [m for m in prov.scan() if m["name"] == "tiny-Q4_K_M"]
    assert len(rows) == 1                                                                           # two links, one model file: listed once
    plan = rows[0]["removable"]
    assert str(blob) not in plan["files"] and len(plan["files"]) == 1                              # the blob is still needed by the other link
    await prov.delete("tiny-Q4_K_M")
    assert blob.exists() and sum(1 for p in (link, other) if p.is_symlink()) == 1                  # one link went, the model file and the other link stay


async def test_other_files_in_a_hugging_face_cache_entry_survive(prov, tmp_path):
    home = tmp_path / "home"
    link, blob = hf_entry(home)
    keep = link.parents[2] / "snapshots" / "abc123" / "README.md"
    keep.write_text("keep")
    (link.parents[2] / "snapshots" / "abc123" / "other.gguf").symlink_to(os.path.relpath(make_gguf(link.parents[2] / "blobs" / ("f" * 64), pad=300), link.parent))
    await prov.delete("tiny-Q4_K_M")
    assert keep.exists() and (link.parents[2] / "blobs" / ("f" * 64)).exists()                       # only the chosen model's files went; the entry stays


async def test_a_users_own_folder_is_never_touched(prov, tmp_path):
    mine = make_gguf(tmp_path / "mine" / "own.gguf")
    prov.save_config({"models_dirs": [str(tmp_path / "mine")]})
    with pytest.raises(LlamaCppError, match="your own folder"):
        await prov.delete("own")
    assert mine.exists()


async def test_a_link_that_points_outside_the_cache_is_unlinked_but_its_target_survives(prov, tmp_path):
    home = tmp_path / "home"
    target = make_gguf(tmp_path / "precious" / "model.gguf")
    snap = home / ".cache/huggingface/hub/models--evil--x/snapshots/rev"
    snap.mkdir(parents=True)
    (snap / "evil.gguf").symlink_to(target)
    row = next(m for m in prov.scan() if m["name"] == "evil")
    assert row["source"] == "hf_cache" and row["removable"]["files"] == [str(snap / "evil.gguf")]       # the target is not part of the plan
    await prov.delete("evil")
    assert not (snap / "evil.gguf").is_symlink() and target.exists()


async def test_the_loaded_model_is_unloaded_before_it_is_deleted(prov, tmp_path, monkeypatch):
    make_gguf(llamacpp.managed_dir() / "live.gguf")
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(tmp_path / "x.log"))
    [c async for c in prov.chat_stream("live", [{"role": "user", "content": "hi"}], None, 4096)]
    assert prov.server_status()["model"] == "live"
    await prov.delete("live")
    assert prov.server_status()["running"] is False and not (llamacpp.managed_dir() / "live.gguf").exists()
    await prov.shutdown()


# ------------------------------------------------------------------ Hugging Face search
def mock_hf(monkeypatch, handler):
    monkeypatch.setattr(llamacpp.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


async def test_search_sends_only_the_typed_text_and_keeps_valid_repositories(prov, monkeypatch):
    seen = []

    def h(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=[{"id": "Qwen/Qwen3-8B-GGUF", "downloads": 1200000, "likes": 321, "lastModified": "2026-10-01"}, {"id": "../evil", "downloads": 9}, {"id": "bad name"}, {"nope": 1}])
    mock_hf(monkeypatch, h)
    r = await prov.hf_search("  qwen coder  ")
    assert r["results"] == [{"repo": "Qwen/Qwen3-8B-GGUF", "downloads": 1200000, "likes": 321, "updated": "2026-10-01"}]
    assert seen[0] == {"filter": "gguf", "sort": "downloads", "direction": "-1", "limit": "20", "search": "qwen coder"}     # nothing about the user or the machine
    await prov.hf_search("qwen coder")
    assert len(seen) == 1                                                                           # cached for a few minutes
    await prov.hf_search("")
    assert "search" not in seen[1]                                                                  # no text = the most downloaded


async def test_search_reports_network_and_server_errors_plainly(prov, monkeypatch):
    mock_hf(monkeypatch, lambda req: httpx.Response(503))
    with pytest.raises(LlamaCppError, match="answered 503"):
        await prov.hf_search("x1")
    def boom(req):
        raise httpx.ConnectError("offline")
    mock_hf(monkeypatch, boom)
    with pytest.raises(LlamaCppError, match="Could not reach huggingface.co"):
        await prov.hf_search("x2")


# ------------------------------------------------------------------ what fits, and what to suggest
HW_12 = {"gpu": "RTX", "vram_gb": 11.9, "vram_free_gb": 11.9, "bandwidth": 627, "bandwidth_known": True, "ram_gb": 62.0, "ram_bandwidth": 51}
HW_NONE = {"gpu": None, "vram_gb": 0.0, "vram_free_gb": 0.0, "bandwidth": None, "bandwidth_known": False, "ram_gb": 16.0, "ram_bandwidth": 51}


def files(*specs):
    out = []
    for name, gb, quant in specs:
        f = {"file": name, "size": int(gb * 1e9), "quant": quant, "split": False}
        f["fit"] = arena.gguf_fit(f["size"], quant, HW_12)
        out.append(f)
    return out


def test_gguf_fit_follows_the_file_size_and_the_hardware():
    small, big, huge = (arena.gguf_fit(int(g * 1e9), "Q4_K_M", HW_12) for g in (4.7, 17.0, 90.0))
    assert small["where"] == "gpu" and small["tps"] > 40 and big["where"] == "split" and huge["where"] == "no"
    assert arena.gguf_fit(int(4.7e9), "Q4_K_M", HW_NONE)["where"] == "cpu" and arena.gguf_fit(int(5e9), None, HW_12)["verdict"]["label"] == "Runs on your GPU"
    assert arena.gguf_fit(int(8.5e9), "Q8_0", HW_12)["tps"] < small["tps"]                          # a bigger file is read more slowly per token


def test_the_suggested_file_is_the_preferred_quantization_that_fits_the_gpu():
    fs = files(("m-Q2_K.gguf", 2.8, "Q2_K"), ("m-Q4_K_M.gguf", 4.7, "Q4_K_M"), ("m-Q5_K_M.gguf", 5.5, "Q5_K_M"), ("m-Q8_0.gguf", 8.5, "Q8_0"), ("m-F16.gguf", 16.0, "F16"))
    assert arena.suggest_file(fs) == "m-Q5_K_M.gguf"
    only_big = files(("b-Q4_K_M.gguf", 17.0, "Q4_K_M"), ("b-Q3_K_M.gguf", 13.0, "Q3_K_M"), ("b-Q8_0.gguf", 30.0, "Q8_0"), ("b-F16.gguf", 90.0, "F16"))
    assert arena.suggest_file(only_big) == "b-Q4_K_M.gguf"                                           # nothing fits the GPU: Q4_K_M that still runs
    assert arena.suggest_file(files(("h-F16.gguf", 90.0, "F16"))) is None
    assert arena.suggest_file([]) is None


def test_split_files_are_never_suggested():
    fs = files(("a-Q4_K_M.gguf", 4.7, "Q4_K_M"))
    fs[0]["split"] = True
    assert arena.suggest_file(fs) is None


# ------------------------------------------------------------------ the API
@pytest.fixture
def api(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "z.db")) as c:
        c.put("/api/settings", json={"ollama_url": "http://127.0.0.1:9"})
        c.put("/api/llamacpp/config", json={"binary": str(FAKE)})
        c.tmp = tmp_path
        yield c


def test_the_overview_marks_ollamas_models_and_the_delete_endpoint_explains_the_refusal(api, tmp_path, monkeypatch):
    ollama_store(tmp_path, monkeypatch)
    j = api.get("/api/llamacpp").json()
    m = next(x for x in j["models"] if x["source"] == "ollama")
    assert m["name"] == "ollama/qwen3.5:4b" and m["removable"] is None and j["config"]["ollama_models"] is True
    r = api.request("DELETE", "/api/llamacpp/models", json={"model": "ollama/qwen3.5:4b"})
    assert r.status_code == 400 and "ollama rm qwen3.5:4b" in r.json()["detail"]
    assert api.put("/api/llamacpp/config", json={"ollama_models": False}).json()["models"] == []


def test_search_and_the_file_list_with_fit_and_a_suggestion(api, monkeypatch):
    payload = make_gguf(api.tmp / "src.gguf", pad=2000).read_bytes()

    def h(req):
        if req.url.path == "/api/models":
            return httpx.Response(200, json=[{"id": "owner/tiny-GGUF", "downloads": 5, "likes": 1}])
        return httpx.Response(200, json={"siblings": [{"rfilename": "tiny-Q4_K_M.gguf", "size": 4_700_000_000}, {"rfilename": "tiny-Q8_0.gguf", "size": 8_500_000_000},
                                                      {"rfilename": "tiny-F16.gguf", "size": 90_000_000_000}, {"rfilename": "tiny-00001-of-00002.gguf", "size": 1000}, {"rfilename": "tiny-00002-of-00002.gguf", "size": 1000}]})
    monkeypatch.setattr(llamacpp.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(h), **kw))
    s = api.get("/api/llamacpp/search", params={"q": "tiny"}).json()
    assert s["results"][0]["repo"] == "owner/tiny-GGUF"
    j = api.get("/api/llamacpp/hf", params={"repo": "owner/tiny-GGUF", "ctx": 4096}).json()
    by = {f["file"]: f for f in j["files"]}
    assert all(f["fit"]["verdict"]["label"] for f in j["files"]) and by["tiny-F16.gguf"]["fit"]["where"] in ("no", "split", "cpu")
    assert j["suggested"] in by and j["suggested"] != "tiny-00001-of-00002.gguf" and j["hardware"]["ram_gb"] >= 0
    assert api.get("/api/llamacpp/hf", params={"repo": "bad"}).status_code == 400


def test_the_delete_time_check_refuses_anything_outside_the_cache_it_belongs_to(prov, tmp_path):
    home = tmp_path / "home"
    link, blob = hf_entry(home)
    hub = home / ".cache/huggingface/hub"
    check = prov._deletable_file
    assert check(link, "hf_cache", hub) and check(blob, "hf_cache", hub)                              # the cache entry's own link and blob
    outside = make_gguf(tmp_path / "elsewhere" / "x.gguf")
    assert not check(outside, "hf_cache", hub) and not check(outside, "managed", llamacpp.managed_dir())
    notes = hub / "models--owner--tiny-GGUF" / "snapshots" / "abc123" / "README.md"
    notes.write_text("x")
    assert not check(notes, "hf_cache", hub)                                                           # not a model file
    assert not check(hub / "missing.gguf", "hf_cache", hub)
    sneaky = hub / "models--owner--tiny-GGUF" / "blobs" / "link-blob"
    sneaky.symlink_to(outside)
    assert not check(sneaky, "hf_cache", hub)                                                          # a blob that is itself a link is never removed
    assert not check(blob, "hf_cache", tmp_path / "some-other-root")
