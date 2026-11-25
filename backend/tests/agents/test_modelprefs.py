from app.agents.engine import AgentEngine
from app.agents.modelprefs import build_prefs, est_tokens, parse_parameters, plan_context, prepare_messages, strip_think, text_tool_protocol
from app.workspace.sessions import SessionManager
from app.toolchain.engine import ToolEngine

QWEN3_CODER = {"capabilities": ["completion", "tools"], "parameters": "repeat_penalty 1.05\nstop \"<|im_end|>\"\ntemperature 0.7\ntop_k 20\ntop_p 0.8"}
R1 = {"capabilities": ["tools", "thinking", "completion"], "parameters": 'stop "<|User|>"'}
QWEN35 = {"capabilities": ["completion", "vision", "tools", "thinking"], "parameters": "temperature 1\ntop_k 20\ntop_p 0.95\npresence_penalty 1.5"}
GEMMA = {"capabilities": ["completion"], "parameters": ""}


def test_parameters_are_parsed_from_the_modelfile_block():
    assert parse_parameters('temperature 0.7\nstop "x"\nstop "y"\ntop_k 20\nrepeat_penalty 1.05') == {"temperature": 0.7, "top_k": 20, "repeat_penalty": 1.05}
    assert parse_parameters("") == {} and parse_parameters("garbage") == {}


def test_creators_recommendations_are_respected_with_a_cap_for_tool_use():
    p = build_prefs("qwen3-coder:30b", QWEN3_CODER, "agent")
    assert p.options["temperature"] == 0.7 and p.options["top_p"] == 0.8 and p.options["top_k"] == 20 and p.options["repeat_penalty"] == 1.05
    hot = build_prefs("qwen3.5:9b", QWEN35, "agent")
    assert hot.options["temperature"] == 0.7 and any("capped" in n for n in hot.notes)       # model suggests 1.0; capped for tool calls
    assert hot.options["presence_penalty"] == 1.5                                            # other recommendations are kept
    assert build_prefs("unknown-model:7b", {}, "agent").options["temperature"] == 0.2      # safe default


def test_verifier_is_steadier_than_the_agent():
    assert build_prefs("qwen3-coder:30b", QWEN3_CODER, "verify").options["temperature"] == 0.2


def test_reasoning_models_never_run_cold_and_get_instructions_in_the_user_turn():
    p = build_prefs("deepseek-r1:14b", R1, "verify")
    assert p.options["temperature"] >= 0.5 and p.options["top_p"] == 0.95 and p.fold_system and p.think is None   # thinking not toggled
    assert build_prefs("hf.co/unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF:Q4", R1, "agent").fold_system


def test_hybrid_thinking_models_think_for_review_not_for_tool_steps():
    assert build_prefs("qwen3.5:9b", QWEN35, "agent").think is False
    assert build_prefs("qwen3.5:9b", QWEN35, "verify").think is True
    assert build_prefs("qwen3-coder:30b", QWEN3_CODER, "agent").think is None              # no thinking capability: never send `think`
    gpt = {"capabilities": ["tools", "thinking"], "parameters": ""}
    assert build_prefs("gpt-oss:20b", gpt, "agent").think == "low" and build_prefs("gpt-oss:20b", gpt, "verify").think == "high"


def test_models_without_tool_calling_get_a_text_protocol_and_system_is_folded_for_gemma():
    g = build_prefs("gemma3:4b", GEMMA, "agent")
    assert not g.native_tools and g.fold_system
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "do it"}]
    out = prepare_messages(msgs, g, tools_on=True)
    assert out[0]["role"] == "user" and out[0]["content"].startswith("SYS") and "create_file" in out[0]["content"] and out[0]["content"].endswith("do it")
    assert msgs[0]["role"] == "system"                                                    # input is not mutated
    assert '{"name": "<tool_name>"' in text_tool_protocol() and "run_command" in text_tool_protocol()


def test_native_tool_models_keep_system_role_and_old_thoughts_are_dropped():
    p = build_prefs("qwen3-coder:30b", QWEN3_CODER, "agent")
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "<think>secret plan</think>Answer"}]
    out = prepare_messages(msgs, p, True)
    assert out[0] == {"role": "system", "content": "SYS"} and out[2]["content"] == "Answer"
    assert strip_think("<think>x</think>\n\nhi") == "hi"


def test_context_window_grows_to_fit_big_documents_within_the_model_limit():
    small = [{"role": "user", "content": "hi"}]
    assert plan_context(small, 16384, 131072) == (16384, None)
    big = [{"role": "user", "content": "x" * 120_000}]                                    # ~36K tokens
    n, note = plan_context(big, 16384, 131072)
    assert n == 65536 and "Raised the context window to 64K" in note
    n2, note2 = plan_context(big, 16384, 32768)                                           # model cannot go higher
    assert n2 == 32768 and "larger than this model" in note2
    assert est_tokens(big) > 30_000


class Capture:
    """Provider fake that records exactly what the engine would send to Ollama."""
    def __init__(self, meta): self.meta, self.calls = meta, []
    base_url = "http://fake"

    async def show(self, model): return self.meta

    async def chat_stream(self, model, messages, tools, num_ctx, options=None, think=None):
        self.calls.append({"messages": messages, "tools": tools, "num_ctx": num_ctx, "options": options, "think": think})
        yield {"message": {"content": "ok"}}
        yield {"done": True, "prompt_eval_count": 1, "eval_count": 1, "eval_duration": 1}


async def test_engine_sends_model_specific_request(harness, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="deepseek-r1:14b", num_ctx=8192)
    prov = Capture(R1)
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "list the files in this project")
    c = prov.calls[0]
    assert c["messages"][0]["role"] == "user" and "You are Zeus-Agent" in c["messages"][0]["content"]       # system folded for R1
    assert c["options"]["temperature"] >= 0.5 and c["think"] is None and c["tools"]                     # tools still native (reported by Ollama)


async def test_engine_uses_text_protocol_and_omits_tools_for_models_that_cannot_call_them(harness, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="gemma3:4b", num_ctx=8192)
    prov = Capture(GEMMA)
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "list the files in this project")
    c = prov.calls[0]
    assert c["tools"] is None and "reply with ONLY a single JSON object" in c["messages"][0]["content"]   # Ollama would 400 on `tools` otherwise


async def test_context_raise_is_announced_once_per_window_size(harness, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m", num_ctx=8192)
    from app.workspace.sessions import SessionManager as _SM  # noqa: F401
    big = "x" * 60_000                                                              # ~18K tokens: forces 32K
    SessionManager(db).db.x("INSERT INTO messages(session_id,agent,role,content,created) VALUES(?,?,?,?,0)", (s["id"], "user", "user", big))
    calls = {"n": 0}

    class Steps(Capture):
        async def chat_stream(self, model, messages, tools, num_ctx, options=None, think=None):
            calls["n"] += 1
            self.calls.append({"num_ctx": num_ctx})
            if calls["n"] < 4:
                yield {"message": {"content": "", "tool_calls": [{"function": {"name": "list_files", "arguments": {"path": ".", "recursive": calls["n"] == 3}}}]}}
            else:
                yield {"message": {"content": "done"}}
            yield {"done": True, "prompt_eval_count": 1, "eval_count": 1, "eval_duration": 1}
    prov = Steps({})
    await AgentEngine(db, prov, ToolEngine()).loop(h.ctx, s, __import__("app.agents.engine", fromlist=["AgentSpec"]).AgentSpec(), "m", True, "x")
    notes = [e for e in h.events if e["type"] == "notice" and "Raised the context window" in e["text"]]
    assert len(notes) == 1 and all(c["num_ctx"] == 32768 for c in prov.calls)
