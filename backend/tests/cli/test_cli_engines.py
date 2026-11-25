"""Terminal app: engine choice (/engine, /llamacpp), the help screen, and `zeus --cli --continue` resuming a desktop session."""
import io
import os
from pathlib import Path

import pytest
from rich.console import Console

from app.cli.ui import banner as bn
from app.cli.commands import engines as en
from app.cli.app import ALIAS, Cli, build_parser
from app.cli.client import ApiError


class FakeApi:
    """Answers the endpoints the terminal app uses from canned data and records every write."""

    def __init__(self, project_path, *, state=None, sessions=None, engines=None):
        self.port = 1
        self.calls = []
        self.project = {"id": 7, "name": "proj", "path": str(project_path), "permission_mode": "trusted"}
        self.state = state
        self.sessions = sessions or []
        self.engine_status = engines or self.default_engines()
        self.default = "ollama"

    @staticmethod
    def default_engines(llama_ok=True):
        return [{"id": "ollama", "label": "Ollama", "available": True, "version": "0.34", "models": 2, "last_model": "qwen3:8b", "error": None},
                {"id": "llamacpp", "label": "llama.cpp", "available": llama_ok, "version": "b12345", "models": 1, "last_model": "gguf-a", "error": None if llama_ok else "llama-server was not found",
                 "server": {"running": False}, "devices": []}]

    async def get(self, path, **q):
        self.calls.append(("GET", path, q))
        if path == "/api/health":
            return {"ok": True}
        if path == "/api/settings":
            return {"engine": self.default, "default_model": "qwen3:8b", "default_num_ctx": 16384}
        if path == "/api/ollama/status":
            return {"connected": True, "version": "0.34", "endpoint": "http://localhost:11434"}
        if path == "/api/engines":
            return {"default": self.default, "engines": self.engine_status, "recommendation": {"engine": "ollama", "reason": "because"},
                    "help": {"rows": [{"topic": "Setup", "ollama": "easy", "llamacpp": "manual"}], "when": {"ollama": ["new"], "llamacpp": ["tune"]}, "install": ["Download a build."]}}
        if path == "/api/models":
            return [{"name": "gguf-a", "size": 10**9, "quantization": "Q4_K_M", "tools": True}] if q.get("engine") == "llamacpp" else [{"name": "qwen3:8b", "size": 5 * 10**9}]
        if path == "/api/about":
            return None
        if path == "/api/state":
            return self.state or {"state": {}, "resolved": None}
        if path == "/api/sessions":
            return self.sessions
        if path.endswith("/messages"):
            return []
        raise AssertionError(path)

    async def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        if path == "/api/projects":
            return self.project
        if path == "/api/sessions":
            return {"id": 99, "title": "New session", "model": body["model"], "engine": body["engine"], "num_ctx": body["num_ctx"]}
        return {"ok": True}

    async def put(self, path, body=None):
        self.calls.append(("PUT", path, body))
        if path == "/api/engine":
            self.default = body["engine"]
            return {"default": body["engine"], "engines": self.engine_status, "recommendation": {"engine": "ollama", "reason": "r"}}
        return {}

    async def patch(self, path, body=None):
        self.calls.append(("PATCH", path, body))
        s = next((x for x in self.sessions if path.endswith(f"/{x['id']}")), {"id": 1})
        s.update(body)
        return s


def make(tmp_path, **kw):
    path = tmp_path / "proj"
    path.mkdir(exist_ok=True)
    resume = kw.pop("resume", False)
    api = FakeApi(path, **kw)
    cli = Cli(api, Console(file=io.StringIO(), width=100, force_terminal=False), path, resume=resume)
    return api, cli, path


def sess(**o):
    return {"id": 5, "title": "Add a login page", "model": "gguf-a", "num_ctx": 12288, "engine": "llamacpp", "uid": "u5", **o}


async def test_continue_resumes_the_conversation_recorded_by_the_desktop_app(tmp_path, monkeypatch):
    other = tmp_path / "gui-project"
    other.mkdir()
    state = {"state": {"client": "gui", "engine": "llamacpp"}, "resolved": {"project": {"id": 7, "name": "gui-project", "path": str(other), "permission_mode": "trusted"},
                                                                         "session": sess(), "engine": "llamacpp", "model": "gguf-a", "num_ctx": 12288}}
    api, cli, here = make(tmp_path, state=state, resume=True)
    monkeypatch.chdir(here)
    await cli.start()
    assert cli.engine == "llamacpp" and cli.model == "gguf-a" and cli.ctx == 12288 and cli.session["id"] == 5
    assert Path(os.getcwd()) == other and str(cli.cwd) == str(other)                           # moved into the project you were in
    assert cli.resumed == {"client": "gui", "title": "Add a login page", "path": str(other)}
    assert ("POST", "/api/sessions/5/open", None) in api.calls                                 # the terminal is now the last client
    assert ("GET", "/api/models", {"engine": "llamacpp"}) in api.calls                           # models listed for the resumed engine


async def test_continue_with_an_explicit_folder_ignores_the_state_file(tmp_path, monkeypatch):
    api, cli, here = make(tmp_path, state={"state": {}, "resolved": {"project": {"path": "/elsewhere"}}}, sessions=[sess(engine="ollama", model="qwen3:8b", id=3)], resume=True)
    cli.explicit_path = True
    await cli.start()
    assert ("GET", "/api/state", {}) not in api.calls
    assert cli.session["id"] == 3 and cli.engine == "ollama" and cli.model == "qwen3:8b"          # the folder's latest conversation, on its own engine


async def test_continue_falls_back_to_this_folders_latest_conversation(tmp_path):
    api, cli, here = make(tmp_path, state=None, sessions=[sess(id=9)], resume=True)
    await cli.start()
    assert cli.session["id"] == 9 and cli.engine == "llamacpp" and cli.model == "gguf-a" and cli.resumed["client"] is None


async def test_without_continue_it_starts_fresh_on_the_default_engine(tmp_path):
    api, cli, here = make(tmp_path, state={"state": {}, "resolved": {"project": {"path": str(tmp_path)}, "session": sess()}})
    await cli.start()
    assert cli.session is None and cli.engine == "ollama" and cli.model == "qwen3:8b" and cli.resumed is None
    assert all(c[1] != "/api/state" for c in api.calls)


async def test_engine_option_and_a_default_of_llamacpp(tmp_path):
    api, cli, _ = make(tmp_path)
    api.default = "llamacpp"
    await cli.start()
    assert cli.engine == "llamacpp" and cli.model == "gguf-a"                                    # the model last used with llama.cpp
    api2, cli2, _ = make(tmp_path)
    cli2.opt_engine = "llama.cpp"
    await cli2.start()
    assert cli2.engine == "llamacpp"


async def test_slash_engine_switches_and_keeps_the_session_model(tmp_path):
    api, cli, _ = make(tmp_path)
    await cli.start()
    cli.session = {"id": 4, "model": "qwen3:8b", "engine": "ollama", "title": "t"}
    api.sessions = [{"id": 4, "model": "gguf-a", "engine": "llamacpp", "title": "t"}]           # what the backend does on a switch
    await cli.run_command("/engine llama")
    assert ("PUT", "/api/engine", {"engine": "llamacpp", "session_id": 4}) in api.calls
    assert cli.engine == "llamacpp" and cli.model == "gguf-a"
    out = cli.c.file.getvalue()
    assert "engine: llama.cpp" in out and "gguf-a" in out
    await cli.run_command("/engine nonsense")
    assert "Use /engine ollama or /engine llamacpp" in cli.c.file.getvalue()


async def test_slash_engine_without_llama_cpp_explains_what_to_do(tmp_path):
    api, cli, _ = make(tmp_path, engines=FakeApi.default_engines(llama_ok=False))
    await cli.start()
    await cli.run_command("/engine llamacpp")
    out = cli.c.file.getvalue()
    assert "llama.cpp is not available yet" in out


async def test_model_command_lists_gguf_files_for_llama_cpp(tmp_path):
    api, cli, _ = make(tmp_path)
    api.default = "llamacpp"
    await cli.start()
    chosen = []

    async def pick(title, opts, **k):
        chosen.append((title, [o.label for o in opts], [o.hint for o in opts]))
        return "gguf-a"

    cli._choose = pick
    await cli.cmd_ollama_model("")
    assert chosen[0][0].startswith("Choose a model  (GGUF files llama.cpp can run)") and chosen[0][1] == ["gguf-a"] and "Q4_K_M" in chosen[0][2][0] and "tools" in chosen[0][2][0]
    assert cli.model == "gguf-a"
    assert not any(c[0] == "PUT" and c[1] == "/api/settings" for c in api.calls)                  # default_model is an Ollama setting
    cli.session = {"id": 2, "model": None, "engine": "llamacpp", "title": "t"}
    await cli.cmd_ollama_model("gguf")                                                           # a prefix is enough
    assert ("PATCH", "/api/sessions/2", {"model": "gguf-a"}) in api.calls


async def test_new_sessions_carry_the_engine(tmp_path):
    api, cli, _ = make(tmp_path)
    api.default = "llamacpp"
    await cli.start()
    s = await cli.ensure_session()
    assert s["engine"] == "llamacpp" and ("POST", "/api/sessions", {"project_id": 7, "model": "gguf-a", "num_ctx": 16384, "engine": "llamacpp"}) in api.calls


def test_engine_helpers_and_help_screen():
    assert en.engine_of("llama.cpp") == "llamacpp" and en.engine_of("Ollama") == "ollama" and en.engine_of("x") is None and en.engine_of("") is None
    engines = {e["id"]: e for e in FakeApi.default_engines()}
    opts = en.options(engines, "ollama")
    assert [o.value for o in opts] == ["ollama", "llamacpp", "help"] and "recommended" in opts[0].label and "1 GGUF model" in opts[1].hint
    down = {e["id"]: e for e in FakeApi.default_engines(llama_ok=False)}
    assert "llama-server not found" in en.options(down, "ollama")[1].hint
    buf = io.StringIO()
    Console(file=buf, width=110, force_terminal=False).print(en.help_screen({"rows": [{"topic": "Setup", "ollama": "easy", "llamacpp": "manual"}], "when": {"ollama": ["new"], "llamacpp": ["tune"]},
                                                                           "install": ["Download a build.", "Set the path."]}, {"engine": "ollama", "reason": "because"}, "ollama", down))
    t = buf.getvalue()
    assert "Setup" in t and "easy" in t and "manual" in t and "For this machine: Ollama" in t and "To use llama.cpp" in t and "Download a build." in t and "Choose llama.cpp if" in t


def test_banner_shows_the_engine():
    rows = dict((k, str(v)) for k, v in bn.session_rows({"engine": "llamacpp", "model": "gguf-a", "ctx": 8192, "perm": "approval", "project_path": "/x", "ollama": {},
                                                          "engines": {"llamacpp": {"available": True, "version": "b12345", "server": {"running": True, "model": "gguf-a"}}}}))
    assert rows["Engine"] == "llama.cpp" and rows["Model"] == "gguf-a" and rows["llama.cpp"] == "loaded: gguf-a"
    rows = dict((k, str(v)) for k, v in bn.session_rows({"engine": "ollama", "model": "m", "ctx": 1, "perm": "approval", "project_path": "/x", "ollama": {"connected": True, "version": "0.34"}}))
    assert rows["Engine"] == "Ollama" and rows["Ollama"].startswith("connected")


def test_new_commands_and_options_exist():
    assert ALIAS["engine"] == "engine" and ALIAS["llama"] == "llamacpp" and ALIAS["model"] == "ollama-model"
    a = build_parser().parse_args(["--continue", "--engine", "llamacpp", "-m", "gguf-a"])
    assert a.resume is True and a.engine == "llamacpp" and a.model == "gguf-a" and a.path is None
    assert build_parser().parse_args(["-c"]).resume is True


# ---- /best
def test_best_activity_aliases_and_options():
    from app.cli.commands import best
    assert best.activity_of("Coding") == "code" and best.activity_of("agentic") == "agentcode" and best.activity_of("nonsense") is None
    from app.modelhub import arena
    hw = {"gpu": "RTX", "vram_gb": 12.0, "vram_free_gb": 12.0, "bandwidth": 600, "bandwidth_known": True, "ram_gb": 32.0, "ram_bandwidth": 51}
    ov = arena.overview(arena.load_snapshot(), hw, [])
    opts = best.options(ov)
    assert [o.value for o in opts] == [a["id"] for a in ov["activities"]]


def test_best_render_shows_hardware_best_model_and_attribution():
    from io import StringIO
    from rich.console import Console
    from app.cli.commands import best
    from app.modelhub import arena
    hw = {"gpu": "NVIDIA RTX 5070 Ti", "vram_gb": 12.0, "vram_free_gb": 12.0, "bandwidth": 600, "bandwidth_known": True, "ram_gb": 32.0, "ram_bandwidth": 51}
    ov = arena.overview(arena.load_snapshot(), hw, [{"name": "granite4.1", "sizes": ["3b", "8b", "30b"], "community": False}])
    buf = StringIO()
    Console(file=buf, width=140, force_terminal=False).print(best.render(ov, "chat"))
    out = buf.getvalue()
    assert "RTX 5070 Ti" in out and "Best for you" in out and "CC BY 4.0" in out and "tok/s" in out
    cpu = {**hw, "gpu": None, "vram_gb": 0.0, "vram_free_gb": 0.0, "bandwidth": None, "bandwidth_known": False}
    buf2 = StringIO()
    Console(file=buf2, width=140, force_terminal=False).print(best.render(arena.overview(arena.load_snapshot(), cpu, []), "code"))
    assert "no GPU detected" in buf2.getvalue()


# ---- memory, context and summary views
def _text(renderable, width=120):
    from io import StringIO
    from rich.console import Console
    buf = StringIO()
    Console(file=buf, width=width, force_terminal=False).print(renderable)
    return buf.getvalue()


def test_context_view_shows_meter_messages_summary_and_memory():
    from app.cli.commands import memory as m
    base = {"tokens": 12000, "window": 16384, "percent": 73, "trigger_percent": 75, "messages_total": 30, "messages_in_context": 8,
            "summary": {"text": "Goal: x", "covers": 22, "updated": 1}, "memory": {"exists": True, "bytes": 321, "path": "/p/.zeus/memory.md", "text": "- a", "max_bytes": 16000}}
    out = _text(m.context_view(base))
    assert "73%" in out and "12,000" in out and "8" in out and "30" in out and "first 22" in out and "321 bytes" in out
    none = _text(m.context_view({**base, "summary": None, "memory": {**base["memory"], "exists": False}}))
    assert "No project memory yet" in none and "first" not in none


def test_memory_summary_and_compact_views():
    from app.cli.commands import memory as m
    assert "No project memory yet" in _text(m.memory_view({"text": "  ", "path": "/p/.zeus/memory.md", "bytes": 0, "max_bytes": 16000}))
    assert "Tests run" in _text(m.memory_view({"text": "- Tests run with pytest", "path": "/p", "bytes": 24, "max_bytes": 16000}))
    ok = _text(m.compact_result({"ok": True, "covers": 12, "tokens_before": 9000, "tokens_after": 2100, "summary": "Goal: ship"}))
    assert "12" in ok and "9,000" in ok and "2,100" in ok and "Goal: ship" in ok
    assert "Nothing to compact" in _text(m.compact_result({"ok": False, "reason": "Nothing to compact yet."}))


def test_new_commands_are_registered_with_handlers():
    from app.cli import app as cli
    for name in ("context", "compact", "summary", "memory", "remember"):
        assert name in cli.COMMANDS and hasattr(cli.Cli, f"cmd_{name}")
    assert cli.ALIAS["ctx"] == "ollama-ctx"                              # the older alias is untouched


# ---- /reset
def test_reset_view_marks_defaults_and_lists_what_is_kept():
    from app.cli.commands import reset as rs
    plan = {"confirm": "RESET", "keeps": ["Your project files", "Ollama and its models"], "items": [
        {"id": "conversations", "label": "Conversations", "detail": "d1", "default": True, "summary": "2 projects", "bytes": 4096},
        {"id": "models", "label": "Downloaded llama.cpp models", "detail": "d2", "default": False, "summary": "1 file", "bytes": 5 * 2 ** 30}]}
    out = _text(rs.plan_view(plan))
    assert "cannot be undone" in out and "✓ Conversations" in out and "· Downloaded llama.cpp models" in out and "Never touched" in out and "Ollama and its models" in out
    assert rs.parse_answer("RESET", plan) == (["conversations"], False)
    assert rs.parse_answer("RESET models", plan) == (["conversations", "models"], True)
    for bad in ("", "reset", "Reset", "yes", "RESET everything", "RESET models now", "xRESET"):
        assert rs.parse_answer(bad, plan) == (None, False), bad
    rep = _text(rs.report_view({"items": [{"id": "logs", "ok": False, "files": 1, "bytes": 10, "errors": ["/x: denied"]}], "removed_bytes": 10}))
    assert "✗" in rep and "/x: denied" in rep and "was reset" in rep


def test_reset_command_is_registered():
    from app.cli import app as cli
    assert "reset" in cli.COMMANDS and hasattr(cli.Cli, "cmd_reset")


def test_llamacpp_menu_has_the_found_programs_chooser():
    from app.cli import app as cli
    assert hasattr(cli.Cli, "choose_llama_server")


# ---- llama.cpp models: search, files by fit, uninstall
def test_llama_model_helpers_for_the_terminal():
    from app.cli.commands import llamamodels as lm
    assert lm.looks_like_repo("Qwen/Qwen3-8B-GGUF") and not lm.looks_like_repo("qwen coder") and not lm.looks_like_repo("../x/y") and not lm.looks_like_repo("")
    assert lm.short(1_234_567) == "1.2M" and lm.short(4321) == "4K" and lm.short(12) == "12"
    sr = lm.search_options([{"repo": "o/a-GGUF", "downloads": 1_500_000, "likes": 321}])
    assert sr[0].value == "o/a-GGUF" and "↓ 1.5M" in sr[0].hint and "♥ 321" in sr[0].hint
    info = {"suggested": "m-Q4_K_M.gguf", "files": [
        {"file": "m-Q4_K_M.gguf", "size": 4_700_000_000, "quant": "Q4_K_M", "split": False, "installed": False, "fit": {"verdict": {"label": "Runs on your GPU"}, "tps": 68.3}},
        {"file": "m-F16.gguf", "size": 90_000_000_000, "quant": "F16", "split": False, "installed": True, "fit": {"verdict": {"label": "Too large"}, "tps": None}},
        {"file": "m-00001-of-00002.gguf", "size": 5, "quant": None, "split": True, "installed": False, "fit": {"verdict": {"label": "x"}, "tps": None}}]}
    fo = lm.file_options(info)
    assert [o.value for o in fo] == ["m-Q4_K_M.gguf", "m-F16.gguf"]                                           # split models are not offered
    assert "Runs on your GPU ~68.3 tok/s" in fo[0].hint and "suggested" in fo[0].hint and "Too large" in fo[1].hint and "installed" in fo[1].hint and "suggested" not in fo[1].hint


def test_only_what_zeus_agent_may_delete_is_offered_for_uninstall():
    from app.cli.commands import llamamodels as lm
    models = [{"name": "dl", "source": "managed", "size": 5, "removable": {"bytes": 5 * 2**30, "files": ["/x"]}},
              {"name": "ollama/qwen3:8b", "source": "ollama", "size": 9, "removable": None},
              {"name": "own", "source": "folder", "size": 7, "removable": None},
              {"name": "cached", "source": "hf_cache", "size": 3, "removable": {"bytes": 0, "files": ["/y"]}}]
    opts = lm.removable_options(models)
    assert [o.value for o in opts] == ["dl", "cached"] and "downloaded by Zeus-Agent" in opts[0].hint and "5.0 GB" in opts[0].hint and "Hugging Face cache" in opts[1].hint
    assert lm.summary(models) == "4 models (1 shared from Ollama)" and lm.summary(models[:1]) == "1 model"


def test_llamacpp_menu_has_download_search_and_uninstall():
    from app.cli import app as cli
    assert hasattr(cli.Cli, "uninstall_gguf") and hasattr(cli.Cli, "download_gguf")
