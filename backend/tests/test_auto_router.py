import pytest

from app.agent.engine import AgentEngine
from app.agent.router import AUTO, Choice, build_profile, classify, decide, family_of, split_override
from app.sessions.manager import SessionManager
from app.tools.engine import ToolEngine

GB = 10**9
GPU, RAM = 12 * GB, 40 * GB


def P(name, size_gb, loaded=False, caps=None):
    return build_profile(name, int(size_gb * GB), loaded, caps)


FLEET = lambda **kw: [  # noqa: E731
    P("qwen3-coder-16k:latest", 18, caps=["tools"]), P("deepseek-r1-8b-32k:latest", 5, caps=["thinking"]),
    P("qwen3:8b", 5.2, caps=["tools", "thinking"]), P("nomic-embed-text:latest", 0.3, caps=["embedding"]),
    P("qwen3-vl:8b", 6, caps=["vision", "tools"]), P("llama3.2:1b", 1.3, caps=["tools"])]


@pytest.mark.parametrize("text,role", [
    ("create a file hello.txt containing hi", "code"), ("fix the bug in src/app.py", "code"), ("run the tests", "code"),
    ("implement a linked list in C", "code"), ("add a /health endpoint to the API", "code"),
    ("why is my merge sort O(n log n)? explain step by step", "think"),
    ("Compare REST and GraphQL trade-offs for this project", "think"),
    ("what is the difference between a mutex and a semaphore?", "think"),
    ("should I use postgres or sqlite for this?", "think"),
    ("yes", "keep"), ("continue", "keep"), ("ok go ahead", "keep"), ("hello", "keep"),
    ("/think refactor this", "think"), ("/code why is it slow", "code"),
])
def test_classify(text, role):
    assert classify(text).role == role


def test_override_prefix_and_reasons():
    assert split_override("/think  why so slow") == ("think", "why so slow")
    assert split_override("no override") == (None, "no override")
    i = classify("fix src/app.py")
    assert i.role == "code" and "file" in i.why


def test_profiles_know_families_and_do_not_confuse_variants():
    assert family_of("qwen3-coder-16k:latest")["name"] == "qwen3-coder"
    assert family_of("qwen3-coder-ubuntucare:latest")["name"] == "qwen3-coder"
    assert family_of("hf.co/unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF:Q4_K_XL")["name"] == "deepseek-r1"
    assert family_of("qwen3.5:9b") is None                       # unknown family, not mistaken for qwen3
    assert build_profile("qwen3-vl:8b").purposes == {"vision"}    # shares the qwen3 prefix but is a vision model
    assert not build_profile("nomic-embed-text:latest").chat
    assert not build_profile("anything:1b", api_caps=["embedding"]).chat


def test_code_prompt_picks_coding_model_think_prompt_picks_reasoner():
    fleet = FLEET()
    c = decide("create a file hello.txt", fleet, GPU, RAM, None)
    assert c.model == "qwen3-coder-16k:latest" and c.role == "code" and c.tools
    t = decide("why does this deadlock? explain the root cause", fleet, GPU, RAM, None)
    assert t.model == "qwen3:8b" and t.role == "think" and t.tools          # reasoning model that keeps tool access


def test_reasoning_only_model_is_used_without_tools_when_it_is_all_there_is():
    fleet = [P("qwen3-coder-16k:latest", 18, caps=["tools"]), P("deepseek-r1:8b", 5, caps=["thinking"])]
    t = decide("compare these two designs and recommend one", fleet, GPU, RAM, None)
    # coder has no reasoning ability but R1 is reasoning-only: R1 wins the thinking role, tools disabled
    assert t.model == "deepseek-r1:8b" and t.tools is False


def test_never_picks_embedding_or_vision_for_coding():
    only_bad = [P("nomic-embed-text:latest", 0.3, caps=["embedding"]), P("qwen3-vl:8b", 6, caps=["vision", "tools"])]
    c = decide("write a script", only_bad, GPU, RAM, None)
    assert c is None or c.model != "nomic-embed-text:latest"
    assert decide("write a script", [P("nomic-embed-text:latest", 0.3, caps=["embedding"])], GPU, RAM, None) is None


def test_follow_up_keeps_current_model_and_unknown_last_model_falls_back():
    fleet = FLEET()
    assert decide("yes", fleet, GPU, RAM, "qwen3:8b").model == "qwen3:8b"
    assert decide("yes", fleet, GPU, RAM, "gone:1b").role == "code"


def test_hysteresis_avoids_pointless_swap_but_forced_override_wins():
    near = [P("qwen2.5-coder:7b", 4.7, loaded=True, caps=["tools"]), P("qwen2.5-coder:14b", 9.0, caps=["tools"])]
    c = decide("add a function to utils.py", near, GPU, RAM, "qwen2.5-coder:7b")
    assert c.model == "qwen2.5-coder:7b"                          # marginally better model is not worth a reload
    f = decide("/think what now", FLEET(), GPU, RAM, "qwen3-coder-16k:latest")
    assert f.model == "qwen3:8b"                                   # explicit /think always switches


def test_too_large_models_are_avoided_and_pins_respected():
    huge = [P("qwen3-coder:30b", 19, caps=["tools"]), P("qwen2.5-coder:3b", 1.9, caps=["tools"])]
    c = decide("create a file", huge, 4 * GB, 8 * GB, None)        # 19 GB cannot work on 4 GB GPU + 8 GB RAM
    assert c.model == "qwen2.5-coder:3b"
    pinned = decide("create a file", huge, 4 * GB, 8 * GB, None, {"code": "qwen3-coder:30b"})
    assert pinned.model == "qwen3-coder:30b" and "pinned" in pinned.reason


# ---------------------------------------------------------------- engine integration
class Recorder:
    def __init__(self, turns): self.turns, self.calls = list(turns), []

    async def chat_stream(self, model, messages, tools, num_ctx):
        self.calls.append({"model": model, "tools": tools, "system": messages[0]["content"]})
        t = self.turns.pop(0)
        yield {"message": {"content": t}}
        yield {"done": True, "prompt_eval_count": 1, "eval_count": 1, "eval_duration": 1}


class FakeRouter:
    def __init__(self, choice): self.choice, self.asked = choice, []

    async def route(self, prompt, last): self.asked.append((prompt, last)); return self.choice


async def test_engine_routes_per_prompt_and_disables_tools_for_reasoning_only(harness, db):
    h = harness("trusted")
    sm = SessionManager(db)
    s = sm.create(h.ctx.project["id"], model=AUTO, num_ctx=4096)
    prov = Recorder(["analysis done"])
    eng = AgentEngine(db, prov, ToolEngine())
    eng.router = FakeRouter(Choice("deepseek-r1:8b", "think", False, "reasoning task"))
    await eng.run(h.ctx, s, "why is this slow?")
    assert prov.calls[0]["model"] == "deepseek-r1:8b" and prov.calls[0]["tools"] is None
    assert "no tools are available" in prov.calls[0]["system"]
    ev = [e for e in h.events if e["type"] == "model_selected"][0]
    assert ev["model"] == "deepseek-r1:8b" and ev["role"] == "think" and ev["tools"] is False
    assert sm.get(s["id"])["last_model"] == "deepseek-r1:8b" and sm.get(s["id"])["model"] == AUTO   # session stays in Auto mode
    # next prompt gets the stored model as 'last' so follow-ups can stay put
    prov.turns.append("ok")
    eng.router.choice = Choice("qwen3-coder:30b", "code", True, "coding")
    await eng.run(h.ctx, sm.get(s["id"]), "now create a.py")
    assert eng.router.asked[1][1] == "deepseek-r1:8b"
    assert prov.calls[1]["model"] == "qwen3-coder:30b" and prov.calls[1]["tools"]


async def test_engine_reports_clear_error_when_auto_has_no_model(harness, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model=AUTO)
    eng = AgentEngine(db, Recorder([]), ToolEngine())
    eng.router = FakeRouter(None)
    await eng.run(h.ctx, s, "hi")
    assert [e["type"] for e in h.events][-2:] == ["error", "run_end"]
    assert "Models tab" in [e for e in h.events if e["type"] == "error"][0]["message"]


async def test_fixed_model_sessions_are_untouched_by_the_router(harness, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="qwen3:8b")
    prov = Recorder(["hello"])
    eng = AgentEngine(db, prov, ToolEngine())
    eng.router = FakeRouter(Choice("x", "code", True, ""))
    await eng.run(h.ctx, s, "hi")
    assert prov.calls[0]["model"] == "qwen3:8b" and eng.router.asked == []
    assert not [e for e in h.events if e["type"] == "model_selected"]


def test_tiny_models_never_win_just_because_they_fit():
    """Regression (found on a real machine): qwen3:0.6b was picked as the 'thinking' model."""
    real = [P("qwen3:0.6b", 0.52, caps=["tools", "thinking"]), P("deepseek-r1:14b", 9.0, caps=["thinking"]),
            P("qwq:32b", 19, caps=["tools", "thinking"]), P("qwen2.5-coder:1.5b-base", 1.0, caps=[]), P("qwen2.5-coder:7b", 4.7, caps=["tools"]),
            P("qwen3-coder:30b", 18, caps=["tools"])]
    t = decide("explain the trade-offs between these two designs", real, GPU, RAM, None)
    assert t.model == "deepseek-r1:14b"            # not the 0.6B, and not the dense 32B that would crawl on CPU
    c = decide("create a file hello.txt", real, GPU, RAM, None)
    assert c.model in ("qwen2.5-coder:7b", "qwen3-coder:30b") and c.model != "qwen2.5-coder:1.5b-base"
