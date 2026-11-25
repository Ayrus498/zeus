"""llama.cpp engine: GGUF metadata, command line, message conversion, the provider against a fake llama-server, downloads."""
import json
import os
import stat
import sys
from pathlib import Path

import httpx
import pytest

from app.core.database import Database
from app.modelconnectors.llamacpp import gguf, provider as llamacpp
from app.modelconnectors.llamacpp.provider import LlamaCppError, LlamaCppProvider

from ..gguf_util import make_gguf
from ..paths import FIXTURES

FAKE = FIXTURES / "fake_llama_server.py"
REAL_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """Model downloads, logs and caches go to a temp folder, never to the real ~/.local/share/zeus or ~/.cache."""
    from app.core import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(llamacpp.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    gguf._cache.clear()


@pytest.fixture
def models_dir(tmp_path):
    d = tmp_path / "models"
    make_gguf(d / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf")
    return d


@pytest.fixture
def prov(models_dir):
    db = Database(":memory:")
    p = LlamaCppProvider(db)
    p.save_config({"binary": str(FAKE), "models_dirs": [str(models_dir)]})
    return p


# ---------------------------------------------------------------- GGUF header
def test_gguf_describe_reads_the_metadata_we_need(tmp_path):
    f = make_gguf(tmp_path / "m.gguf")
    d = gguf.describe(f)
    assert d["architecture"] == "qwen2" and d["size_label"] == "1.5B" and d["quantization"] == "Q4_K_M" and d["context_length"] == 32768
    assert d["block_count"] == 28 and d["kv_heads"] == 2 and d["head_dim"] == 128 and d["tools"] is True and d["thinking"] is False and d["embedding"] is False
    assert d["size"] == f.stat().st_size


def test_gguf_capabilities_come_from_the_chat_template(tmp_path):
    assert gguf.describe(make_gguf(tmp_path / "a.gguf", template="plain chat template"))["tools"] is False
    assert gguf.describe(make_gguf(tmp_path / "b.gguf", template="x <think> y {{ tools }}"))["thinking"] is True
    assert gguf.describe(make_gguf(tmp_path / "c.gguf", template=None))["tools"] is False
    assert gguf.describe(make_gguf(tmp_path / "d.gguf", arch="bert", template=None))["embedding"] is True


def test_gguf_quantization_from_file_type_or_name(tmp_path):
    assert gguf.describe(make_gguf(tmp_path / "x.gguf", file_type=7))["quantization"] == "Q8_0"
    assert gguf.describe(make_gguf(tmp_path / "y-Q5_K_M.gguf", file_type=9999))["quantization"] == "Q5_K_M"
    assert gguf.quantization_of({}, "model-IQ3_XS.gguf") == "IQ3_XS" and gguf.quantization_of({}, "model.gguf") is None


def test_gguf_rejects_files_that_are_not_gguf_without_crashing(tmp_path):
    (tmp_path / "t.gguf").write_bytes(b"not a model at all")
    assert "not a GGUF" in gguf.describe(tmp_path / "t.gguf")["error"]
    good = make_gguf(tmp_path / "cut.gguf")
    good.write_bytes(good.read_bytes()[:60])                                      # ends inside the header
    assert "error" in gguf.describe(good)
    assert "error" in gguf.describe(tmp_path / "missing.gguf")
    bad = tmp_path / "ver.gguf"
    bad.write_bytes(b"GGUF" + (99).to_bytes(4, "little") + b"\0" * 20)
    assert "unsupported GGUF version" in gguf.describe(bad)["error"]


def test_gguf_describe_is_cached_per_file_state(tmp_path, monkeypatch):
    f = make_gguf(tmp_path / "c.gguf")
    gguf.describe(f)
    calls = []
    monkeypatch.setattr(gguf, "read_header", lambda p: calls.append(p) or {})
    gguf.describe(f)
    assert calls == []                                                             # served from the cache


# ---------------------------------------------------------------- configuration, command line, messages
def test_clean_settings_keeps_only_valid_values():
    s = llamacpp.clean_settings({"ctx": "8192", "ngl": "99", "threads": -4, "flash_attn": "on", "cache_type_k": "q8_0", "cache_type_v": "bogus",
                                 "extra_args": "--x 1", "evil": "rm -rf", "batch": "abc", "ngl2": 1})
    assert s == {"ctx": 8192, "ngl": "99", "threads": 0, "flash_attn": "on", "cache_type_k": "q8_0", "extra_args": "--x 1"}
    assert llamacpp.clean_settings({"ngl": "all"}) == {"ngl": "all"} and llamacpp.clean_settings({"ngl": "lots"}) == {}


def test_merged_config_survives_garbage():
    assert llamacpp.merged_config("not json")["defaults"]["ngl"] == "auto"
    c = llamacpp.merged_config(json.dumps({"binary": "/x/llama-server", "models_dirs": ["/a", 3, ""], "defaults": {"ctx": 4096}, "models": {"m": {"ngl": "10"}, "bad": 5}}))
    assert c["binary"] == "/x/llama-server" and c["models_dirs"] == ["/a"] and c["defaults"]["ctx"] == 4096 and c["models"] == {"m": {"ngl": "10"}}


def test_build_args_for_a_gpu_and_a_cpu_machine():
    caps = {"jinja": True, "fit": False}
    base = {"ngl": "auto", "threads": 0}
    gpu = llamacpp.build_args("/b/llama-server", "/m/x.gguf", 8123, 16384, base, caps, True, "x")
    assert gpu[:3] == ["/b/llama-server", "-m", "/m/x.gguf"] and ["--port", "8123"] == gpu[gpu.index("--port"):gpu.index("--port") + 2]
    assert "--jinja" in gpu and gpu[gpu.index("-ngl") + 1] == "99" and gpu[gpu.index("-np") + 1] == "1" and gpu[gpu.index("-c") + 1] == "16384"
    assert gpu[gpu.index("--host") + 1] == "127.0.0.1"                              # never listens on the network
    assert llamacpp.build_args("/b", "/m", 1, 2048, base, caps, False, "x")[llamacpp.build_args("/b", "/m", 1, 2048, base, caps, False, "x").index("-ngl") + 1] == "0"
    fit = llamacpp.build_args("/b", "/m", 1, 2048, base, {"fit": True, "jinja": True}, True, "x")
    assert "-ngl" not in fit                                                        # newer builds fit the model themselves


def test_build_args_user_settings_and_extra_args():
    s = {"ngl": "20", "threads": 6, "batch": 512, "ubatch": 128, "flash_attn": "on", "cache_type_k": "q8_0", "cache_type_v": "q8_0", "extra_args": "--no-mmap --temp 0.3"}
    a = llamacpp.build_args("/b", "/m", 1, 4096, s, {"jinja": True}, True, "x")
    for pair in (["-ngl", "20"], ["-t", "6"], ["-b", "512"], ["-ub", "128"], ["--flash-attn", "on"], ["--cache-type-k", "q8_0"], ["--cache-type-v", "q8_0"]):
        assert a[a.index(pair[0]):a.index(pair[0]) + 2] == pair, pair
    assert a[-3:] == ["--no-mmap", "--temp", "0.3"]
    assert llamacpp.build_args("/b", "/m", 1, 1, {"ngl": "all"}, {}, False, "x")[-2:] == ["-ngl", "999"] or "999" in llamacpp.build_args("/b", "/m", 1, 1, {"ngl": "all"}, {}, False, "x")


def test_messages_become_openai_messages_with_matching_tool_call_ids():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_files", "arguments": {"path": "."}}},
                                                                 {"function": {"name": "read_file", "arguments": {"path": "a"}}}]},
            {"role": "tool", "content": "a b", "tool_name": "read_file"}, {"role": "tool", "content": "listing", "tool_name": "list_files"},
            {"role": "assistant", "content": "done"}]
    out = llamacpp.to_openai_messages(msgs)
    calls = out[2]["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["list_files", "read_file"] and all(c["type"] == "function" and c["id"] for c in calls)
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "."}                 # arguments travel as JSON text
    by_id = {m["tool_call_id"]: m["content"] for m in out if m["role"] == "tool"}
    assert by_id[calls[1]["id"]] == "a b" and by_id[calls[0]["id"]] == "listing"          # matched by tool name, not by position
    assert out[-1] == {"role": "assistant", "content": "done"}


def test_parse_arguments():
    assert llamacpp.parse_arguments('{"a": 1}') == {"a": 1} and llamacpp.parse_arguments("") == {}
    assert "_unparsed_arguments" in llamacpp.parse_arguments('{"a": ') and "_unparsed_arguments" in llamacpp.parse_arguments("[1]")


# ---------------------------------------------------------------- finding the program and the models
async def test_status_reports_version_devices_and_missing_binary(prov):
    st = await prov.status()
    assert st["connected"] and st["version"] == "b12345" and st["binary"] == str(FAKE) and st["devices"][0]["name"] == "NVIDIA Fake GPU"
    assert st["devices"][0]["memory_mib"] == 12000 and st["running"] is False
    p2 = LlamaCppProvider(Database(":memory:"))
    p2.save_config({"binary": "/nonexistent/llama-server"})
    os.environ.pop("LLAMA_SERVER", None)
    st2 = await p2.status()
    if not st2["connected"]:                                                         # (a real llama-server on PATH would make this True)
        assert "llama-server was not found" in st2["error"]


def test_binary_must_be_an_absolute_path(prov):
    with pytest.raises(LlamaCppError, match="absolute"):
        prov.save_config({"binary": "llama-server"})
    with pytest.raises(LlamaCppError, match="absolute"):
        prov.save_config({"models_dirs": ["relative/dir"]})


async def test_scan_lists_models_and_skips_what_cannot_be_run(prov, models_dir, tmp_path):
    make_gguf(models_dir / "mmproj-vision.gguf")                                      # projector files are not models
    make_gguf(models_dir / "big-00001-of-00002.gguf"); make_gguf(models_dir / "big-00002-of-00002.gguf")
    (models_dir / "broken.gguf").write_bytes(b"nope")
    make_gguf(models_dir / "sub" / "dup.gguf"); make_gguf(models_dir / "other" / "dup.gguf")
    (models_dir / "notes.txt").write_text("x")
    rows = await prov.list_models()
    names = [r["name"] for r in rows]
    assert "qwen2.5-coder-1.5b-instruct-q4_k_m" in names and "big" in names and "mmproj-vision" not in names and "broken" not in names
    assert sorted(n for n in names if "dup" in n) == ["other/dup", "sub/dup"]        # same file name in two folders: the folder is added
    q = next(r for r in rows if r["name"].startswith("qwen2.5"))
    assert q["family"] == "qwen2" and q["parameter_size"] == "1.5B" and q["quantization"] == "Q4_K_M" and q["tools"] is True and q["managed"] is False


async def test_scan_also_looks_in_zeus_own_folder_and_the_llama_cache(prov, tmp_path):
    make_gguf(llamacpp.managed_dir() / "downloaded.gguf")
    make_gguf(tmp_path / "home" / ".cache" / "llama.cpp" / "cached.gguf")
    rows = {r["name"]: r for r in await prov.list_models()}
    assert rows["downloaded"]["managed"] is True and "cached" in rows


async def test_show_gives_the_agent_what_it_needs(prov):
    meta = await prov.show("qwen2.5-coder-1.5b-instruct-q4_k_m")
    assert meta["capabilities"] == ["completion", "tools"] and meta["context_length"] == 32768 and meta["kv_heads"] == 2 and meta["block_count"] == 28
    with pytest.raises(LlamaCppError, match="not found"):
        await prov.show("nope")


# ---------------------------------------------------------------- running a model through the fake server
def _requests(log: Path):
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


async def test_chat_stream_starts_the_server_and_streams_text(prov, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    name = "qwen2.5-coder-1.5b-instruct-q4_k_m"
    chunks = [c async for c in prov.chat_stream(name, [{"role": "user", "content": "hi"}], None, 4096, {"temperature": 0.1, "top_p": 0.9})]
    text = "".join(c["message"].get("content", "") for c in chunks)
    assert text == "Hello from fake llama"
    done = chunks[-1]
    assert done["done"] and done["prompt_eval_count"] == 11 and done["eval_count"] == 7 and done["eval_duration"] == 70_000_000 and done["done_reason"] == "stop"
    start = _requests(log)[0]["argv"]
    assert start[start.index("-m") + 1].endswith(".gguf") and start[start.index("-c") + 1] == "4096" and "--jinja" in start
    assert "-ngl" not in start                                                       # this build can fit the model itself (--fit)
    body = next(r for r in _requests(log) if r["event"] == "chat")["body"]
    assert body["temperature"] == 0.1 and body["top_p"] == 0.9 and body["stream"] is True and body["stream_options"] == {"include_usage": True}
    st = prov.server_status()
    assert st["running"] and st["model"] == name and st["ctx"] == 4096 and st["busy"] == 0
    await prov.shutdown()
    assert prov.server_status()["running"] is False


async def test_older_builds_get_gpu_layers_from_zeus(prov, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    monkeypatch.setenv("FAKE_LLAMA_NOFIT", "1")                                        # no --fit in --help: an older llama.cpp
    await prov.ensure("qwen2.5-coder-1.5b-instruct-q4_k_m", 2048)
    argv = _requests(log)[0]["argv"]
    assert argv[argv.index("-ngl") + 1] == "99"                                        # a GPU was found, so offload everything
    await prov.shutdown()


async def test_native_tool_calls_are_assembled_from_the_stream(prov):
    name = "qwen2.5-coder-1.5b-instruct-q4_k_m"
    tools = [{"type": "function", "function": {"name": "create_file", "parameters": {"type": "object"}}}]
    chunks = [c async for c in prov.chat_stream(name, [{"role": "user", "content": "TOOL please"}], tools, 4096)]
    calls = chunks[-1]["message"]["tool_calls"]
    assert calls == [{"function": {"name": "create_file", "arguments": {"path": "hello.py", "content": "print(1)"}}}]
    await prov.shutdown()


async def test_reasoning_text_is_reported_as_thinking(prov):
    chunks = [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", [{"role": "user", "content": "THINK"}], None, 2048, None, False)]
    assert "".join(c["message"].get("thinking", "") for c in chunks) == "Let me think."
    await prov.shutdown()


async def test_thinking_can_be_switched_off_for_models_that_support_it(prov, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    async for _ in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", [{"role": "user", "content": "hi"}], None, 2048, None, False):
        pass
    assert next(r for r in _requests(log) if r["event"] == "chat")["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    await prov.shutdown()


async def test_server_errors_become_readable_errors(prov):
    with pytest.raises(LlamaCppError, match="the model exploded"):
        async for _ in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", [{"role": "user", "content": "ERROR"}], None, 2048):
            pass
    await prov.shutdown()


async def test_unknown_model_and_missing_binary_give_actionable_errors(prov, models_dir):
    with pytest.raises(LlamaCppError, match="not found in your model folders"):
        async for _ in prov.chat_stream("ghost", [{"role": "user", "content": "x"}], None, 2048):
            pass
    p2 = LlamaCppProvider(Database(":memory:"))
    p2.save_config({"binary": "/nonexistent/x", "models_dirs": [str(models_dir)]})
    if not p2.binary():
        with pytest.raises(LlamaCppError, match="llama-server was not found"):
            await p2.ensure("anything")


async def test_server_is_reused_restarted_for_more_context_and_swapped_for_another_model(prov, models_dir, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    make_gguf(models_dir / "second.gguf")
    a = "qwen2.5-coder-1.5b-instruct-q4_k_m"
    await prov.ensure(a, 4096); pid1 = prov.proc.pid
    await prov.ensure(a, 2048)                                                       # less context is fine: no restart
    assert prov.proc.pid == pid1
    await prov.ensure(a, 8192)                                                       # more context needs a bigger server
    assert prov.proc.pid != pid1 and prov.ctx == 8192
    pid2 = prov.proc.pid
    await prov.ensure("second", 4096)                                                # another model replaces it
    assert prov.proc.pid != pid2 and prov.model == "second"
    starts = [r for r in _requests(log) if r["event"] == "start"]
    assert len(starts) == 3
    await prov.shutdown()


async def test_a_model_is_not_swapped_out_from_under_a_running_answer(prov, models_dir):
    import asyncio
    make_gguf(models_dir / "second.gguf")
    a = "qwen2.5-coder-1.5b-instruct-q4_k_m"

    async def slow():
        return "".join([c["message"].get("content", "") async for c in prov.chat_stream(a, [{"role": "user", "content": "SLOW"}], None, 2048)])

    t = asyncio.create_task(slow())
    await asyncio.sleep(0.9)                                                         # the answer is mid-stream
    swap = asyncio.create_task(prov.ensure("second", 2048))
    await asyncio.sleep(0.3)
    assert not swap.done()                                                           # waiting for the answer to finish
    assert await t == "Hello from fake llama"                                        # the running answer completed intact
    await asyncio.wait_for(swap, 30)
    assert prov.model == "second"
    await prov.shutdown()


async def test_unload_stops_the_server_and_it_restarts_on_demand(prov):
    a = "qwen2.5-coder-1.5b-instruct-q4_k_m"
    await prov.ensure(a, 2048)
    assert prov.server_status()["running"]
    await prov.unload(a)
    assert not prov.server_status()["running"] and prov.base_url == ""
    assert [c async for c in prov.chat_stream(a, [{"role": "user", "content": "hi"}], None, 2048)]
    await prov.shutdown()


async def test_a_dead_server_is_restarted(prov):
    import signal
    a = "qwen2.5-coder-1.5b-instruct-q4_k_m"
    await prov.ensure(a, 2048)
    os.killpg(prov.proc.pid, signal.SIGKILL); await prov.proc.wait()
    await prov.ensure(a, 2048)
    assert prov.server_status()["running"]
    await prov.shutdown()


async def test_startup_failure_reports_the_server_log(models_dir, tmp_path):
    bad = tmp_path / "bad-llama-server"
    bad.write_text("#!/bin/sh\nif [ \"$1\" = \"--version\" ] || [ \"$1\" = \"--help\" ] || [ \"$1\" = \"--list-devices\" ]; then echo version: 1 >&2; exit 0; fi\necho 'error: failed to load model: out of memory' >&2\nexit 3\n")
    bad.chmod(bad.stat().st_mode | stat.S_IXUSR)
    p = LlamaCppProvider(Database(":memory:"))
    p.save_config({"binary": str(bad), "models_dirs": [str(models_dir)]})
    with pytest.raises(LlamaCppError, match=r"(?s)stopped while loading the model.*out of memory"):
        await p.ensure("qwen2.5-coder-1.5b-instruct-q4_k_m", 2048)
    assert not p.server_status()["running"]


async def test_per_model_settings_override_the_defaults(prov, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    a = "qwen2.5-coder-1.5b-instruct-q4_k_m"
    prov.save_config({"defaults": {"threads": 3}, "model": {"name": a, "settings": {"ngl": "7", "ctx": 3000}}})
    await prov.ensure(a, 9999)
    argv = _requests(log)[0]["argv"]
    assert argv[argv.index("-ngl") + 1] == "7" and argv[argv.index("-t") + 1] == "3" and argv[argv.index("-c") + 1] == "3000"   # a fixed ctx wins over the request
    prov.save_config({"model": {"name": a, "settings": None}})
    assert prov.effective(a)["ngl"] == "auto"
    await prov.shutdown()


async def test_delete_only_removes_files_zeus_downloaded(prov, models_dir):
    mine = make_gguf(llamacpp.managed_dir() / "mine.gguf")
    await prov.delete("mine")
    assert not mine.exists()
    with pytest.raises(LlamaCppError, match="only deletes models it downloaded"):
        await prov.delete("qwen2.5-coder-1.5b-instruct-q4_k_m")
    assert (models_dir / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf").exists()


# ---------------------------------------------------------------- Hugging Face
def _hf_mock(monkeypatch, handler):
    monkeypatch.setattr(llamacpp.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


async def test_hf_files_lists_gguf_files_and_groups_shards(prov, monkeypatch):
    seen = []

    def h(req):
        seen.append((req.url.host, req.url.path, dict(req.url.params), req.headers.get("cookie"), req.headers.get("authorization")))
        return httpx.Response(200, json={"siblings": [
            {"rfilename": "README.md", "size": 10}, {"rfilename": "m-Q4_K_M.gguf", "size": 1_000_000}, {"rfilename": "m-Q8_0.gguf", "size": 2_000_000},
            {"rfilename": "mmproj-f16.gguf", "size": 5}, {"rfilename": "big-00001-of-00002.gguf", "size": 7}, {"rfilename": "big-00002-of-00002.gguf", "size": 8}]})

    _hf_mock(monkeypatch, h)
    r = await prov.hf_files("Qwen/Some-GGUF")
    assert seen == [("huggingface.co", "/api/models/Qwen/Some-GGUF", {"blobs": "true"}, None, None)]       # only the repository name is sent
    assert [(f["file"], f["quant"], f["split"]) for f in r["files"]] == [("m-Q4_K_M.gguf", "Q4_K_M", False), ("m-Q8_0.gguf", "Q8_0", False), ("big.gguf", None, True)]
    assert r["files"][2]["size"] == 15
    for bad in ("nonsense", "a/b/c", "../x", ""):
        with pytest.raises(LlamaCppError):
            await prov.hf_files(bad)


async def test_hf_errors_are_readable(prov, monkeypatch):
    _hf_mock(monkeypatch, lambda req: httpx.Response(404, json={"error": "x"}))
    with pytest.raises(LlamaCppError, match="not found"):
        await prov.hf_files("a/b")

    def down(req):
        raise httpx.ConnectError("no network")

    _hf_mock(monkeypatch, down)
    with pytest.raises(LlamaCppError, match="Could not reach huggingface.co"):
        await prov.hf_files("a/b")


async def test_pull_stream_downloads_resumes_and_validates(prov, tmp_path, monkeypatch):
    payload = make_gguf(tmp_path / "src.gguf", pad=3000).read_bytes()
    ranges = []

    def h(req):
        ranges.append(req.headers.get("range"))
        start = int(req.headers["range"].split("=")[1].split("-")[0]) if req.headers.get("range") else 0
        return httpx.Response(206 if start else 200, content=payload[start:])

    _hf_mock(monkeypatch, h)
    events = [e async for e in prov.pull_stream("hf:owner/repo/dir/model-Q4_K_M.gguf")]
    dest = llamacpp.managed_dir() / "model-Q4_K_M.gguf"
    assert events[-1] == {"status": "success"} and dest.read_bytes() == payload and not dest.with_name(dest.name + ".part").exists()
    assert events[0]["status"] == "downloading" and events[-2]["completed"] == events[-2]["total"] == len(payload) and ranges == [None]
    assert [e async for e in prov.pull_stream("hf:owner/repo/dir/model-Q4_K_M.gguf")] == [{"status": "success"}]      # already there: nothing to fetch
    assert len(ranges) == 1
    dest.unlink()
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(payload[:1000])
    ranges.clear()
    assert [e async for e in prov.pull_stream("hf:owner/repo/dir/model-Q4_K_M.gguf")][-1] == {"status": "success"}
    assert ranges == ["bytes=1000-"] and dest.read_bytes() == payload                                               # resumed, not restarted


async def test_pull_stream_rejects_bad_names_and_non_gguf_content(prov, monkeypatch):
    for bad in ("qwen3:8b", "hf:onlyrepo", "hf:a/b/../../etc/x.gguf", "hf:a/b/file.txt", "hf:a b/c/file.gguf"):
        with pytest.raises(LlamaCppError):
            [e async for e in prov.pull_stream(bad)]
    _hf_mock(monkeypatch, lambda req: httpx.Response(200, content=b"<html>login required</html>" * 50))
    with pytest.raises(LlamaCppError, match="not a valid GGUF"):
        [e async for e in prov.pull_stream("hf:a/b/file.gguf")]
    assert not list(llamacpp.managed_dir().glob("*"))                                                                # nothing left behind
    _hf_mock(monkeypatch, lambda req: httpx.Response(401, text="no"))
    with pytest.raises(LlamaCppError, match="licence or log in"):
        [e async for e in prov.pull_stream("hf:a/b/file.gguf")]


# ---------------------------------------------------------------- strict chat templates (Gemma and friends)
from app.modelconnectors.llamacpp.provider import flatten_messages  # noqa: E402


def read_chats(log):
    return [json.loads(l)["body"]["messages"] for l in log.read_text().splitlines() if json.loads(l)["event"] == "chat"]


def tool_history():
    """What the agent sends on its second step with a model that uses the text tool protocol."""
    return [{"role": "system", "content": "You are an agent."},
            {"role": "user", "content": "list the files"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_files", "arguments": {"path": "."}}}]},
            {"role": "tool", "tool_name": "list_files", "content": "a.py\nb.py"}]


def test_flatten_turns_a_tool_conversation_into_alternating_plain_turns():
    out = flatten_messages(tool_history()[1:])
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert json.loads(out[1]["content"]) == {"name": "list_files", "arguments": {"path": "."}}              # the JSON the model was taught to write
    assert out[2]["content"] == "Tool result (list_files):\na.py\nb.py"
    assert all("tool_calls" not in m and "tool_call_id" not in m for m in out)


def test_flatten_merges_neighbours_names_empty_turns_and_keeps_or_folds_the_system_prompt():
    h = [{"role": "system", "content": "S"}, {"role": "user", "content": "q"},
         {"role": "assistant", "content": "ok", "tool_calls": [{"function": {"name": "a", "arguments": '{"x": 1}'}}, {"function": {"name": "b", "arguments": {}}}]},
         {"role": "tool", "tool_name": "a", "content": "ra"}, {"role": "tool", "tool_name": "b", "content": "rb"}, {"role": "assistant", "content": "  "}]
    out = flatten_messages(h)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user", "assistant"]
    assert out[2]["content"].startswith("ok\n") and '"name": "a"' in out[2]["content"] and '"x": 1' in out[2]["content"] and '"name": "b"' in out[2]["content"]
    assert "Tool result (a):\nra" in out[3]["content"] and "Tool result (b):\nrb" in out[3]["content"]       # two results in a row become one user turn
    assert out[4]["content"] == "(no output)"
    folded = flatten_messages(h, fold_system=True)
    assert [m["role"] for m in folded] == ["user", "assistant", "user", "assistant"] and folded[0]["content"].startswith("S\n\n---\n\nq")


def test_flatten_handles_a_conversation_that_starts_with_a_system_prompt_only():
    assert flatten_messages([{"role": "system", "content": "S"}], fold_system=True) == [{"role": "user", "content": "S"}]
    assert flatten_messages([]) == []


async def test_a_text_protocol_model_with_a_strict_template_completes_a_tool_conversation(prov, tmp_path, monkeypatch):
    log = tmp_path / "strict.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    monkeypatch.setenv("FAKE_LLAMA_STRICT", "1")
    history = tool_history()[1:]                                                                        # (the agent already folded the system prompt into the user turn for Gemma)
    chunks = [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", history, None, 4096)]
    await prov.shutdown()
    text = "".join(c["message"].get("content", "") for c in chunks)
    assert "Hello from fake llama" in text and chunks[-1]["done"]
    sent = read_chats(log)
    assert len(sent) == 1 and [m["role"] for m in sent[0]] == ["user", "assistant", "user"]               # accepted at the first try: plain alternating turns


async def test_a_template_that_rejects_the_system_role_is_retried_once_with_it_folded_in(prov, tmp_path, monkeypatch):
    log = tmp_path / "retry.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    monkeypatch.setenv("FAKE_LLAMA_STRICT", "1")
    chunks = [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", tool_history(), None, 4096)]       # system prompt present: the strict server refuses at first
    await prov.shutdown()
    assert "Hello from fake llama" in "".join(c["message"].get("content", "") for c in chunks)
    sent = read_chats(log)
    assert len(sent) == 2 and sent[0][0]["role"] == "system"
    assert [m["role"] for m in sent[1]] == ["user", "assistant", "user"] and sent[1][0]["content"].startswith("You are an agent.\n\n---\n\nlist the files")


async def test_native_tool_calling_keeps_the_openai_shape(prov, tmp_path, monkeypatch):
    log = tmp_path / "native.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    tools = [{"type": "function", "function": {"name": "list_files", "description": "x", "parameters": {"type": "object", "properties": {}}}}]
    [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", tool_history(), tools, 4096)]
    await prov.shutdown()
    sent = read_chats(log)[0]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"] and sent[2]["tool_calls"][0]["function"]["name"] == "list_files" and sent[3]["tool_call_id"] == sent[2]["tool_calls"][0]["id"]


async def test_a_second_rejection_is_reported_not_retried_forever(prov, tmp_path, monkeypatch):
    log = tmp_path / "twice.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    monkeypatch.setenv("FAKE_LLAMA_STRICT", "1")
    with pytest.raises(LlamaCppError, match="roles must alternate"):
        [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", [{"role": "assistant", "content": "I speak first"}], None, 4096)]   # nothing flattening can fix
    await prov.shutdown()
    assert len(read_chats(log)) == 2


async def test_an_error_that_is_not_about_the_template_is_not_retried(prov, tmp_path, monkeypatch):
    log = tmp_path / "err.log"
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(log))
    with pytest.raises(LlamaCppError, match="exploded"):
        [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", [{"role": "user", "content": "ERROR please"}], None, 4096)]
    await prov.shutdown()
    assert len(read_chats(log)) == 1


async def test_a_response_ends_with_exactly_one_final_chunk_and_each_tool_call_appears_once(prov, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_LOG", str(tmp_path / "once.log"))
    tools = [{"type": "function", "function": {"name": "create_file", "description": "x", "parameters": {"type": "object", "properties": {}}}}]
    chunks = [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", [{"role": "user", "content": "TOOL please"}], tools, 4096)]
    await prov.shutdown()
    assert sum(1 for c in chunks if c.get("done")) == 1 and chunks[-1]["done"]
    calls = [tc for c in chunks for tc in c["message"].get("tool_calls") or []]
    assert len(calls) == 1 and calls[0]["function"]["name"] == "create_file" and calls[0]["function"]["arguments"] == {"path": "hello.py", "content": "print(1)"}
    text = [c for c in chunks if c["message"].get("content")]
    plain = [c async for c in prov.chat_stream("qwen2.5-coder-1.5b-instruct-q4_k_m", [{"role": "user", "content": "hi"}], None, 4096)]
    await prov.shutdown()
    assert sum(1 for c in plain if c.get("done")) == 1 and "".join(c["message"].get("content", "") for c in plain) == "Hello from fake llama"
