"""Agent loop tests with a scripted provider (unit-level only; the real-model run is scripts/e2e_demo.py)."""
import json

from app.agent.engine import AgentEngine
from app.agent.toolparse import parse_text_tool_calls
from app.sessions.manager import SessionManager
from app.tools.engine import ToolEngine


class Scripted:
    def __init__(self, turns): self.turns, self.seen = list(turns), []

    async def chat_stream(self, model, messages, tools, num_ctx, options=None, think=None):
        self.seen.append(messages)
        t = self.turns.pop(0)
        yield {"message": {"content": t.get("content", ""), "tool_calls": t.get("tool_calls")}}
        yield {"done": True, "prompt_eval_count": 100, "eval_count": 20, "eval_duration": 1_000_000_000}


def call(name, **a): return {"function": {"name": name, "arguments": a}}


async def test_loop_executes_tools_then_finishes(harness, ws_dir, db):
    h = harness("approval", ["once"])
    sm = SessionManager(db)
    s = sm.create(h.ctx.project["id"], model="m", num_ctx=4096)
    prov = Scripted([{"content": "plan", "tool_calls": [call("list_files")]},
                     {"tool_calls": [call("create_file", path="a.py", content="x=1", reason="r")]},
                     {"content": "done"}])
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "make a.py")
    assert (ws_dir / "a.py").read_text() == "x=1"
    types = [e["type"] for e in h.events]
    assert types.count("tool_call") == 2 and types[-1] == "run_end" and "usage" in types
    roles = [m["role"] for m in sm.messages(s["id"])]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert sm.get(s["id"])["input_tokens"] == 300
    assert any(m["role"] == "tool" for m in prov.seen[-1])   # tool output fed back to the model


async def test_rejection_reaches_model_and_no_write(harness, ws_dir, db):
    h = harness("approval", ["reject"])
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    prov = Scripted([{"tool_calls": [call("create_file", path="a.py", content="x")]}, {"content": "ok"}])
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "go")
    assert not (ws_dir / "a.py").exists()
    assert "Permission denied" in [m for m in prov.seen[-1] if m["role"] == "tool"][0]["content"]


async def test_text_json_fallback(harness, ws_dir, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    txt = 'Creating.\n```json\n{"name": "create_file", "arguments": {"path": "b.py", "content": "y=2"}}\n```'
    prov = Scripted([{"content": txt}, {"content": "done"}])
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "go")
    assert (ws_dir / "b.py").read_text() == "y=2"


def test_parse_variants():
    assert parse_text_tool_calls('<tool_call>{"name":"read_file","arguments":{"path":"a"}}</tool_call>')
    assert parse_text_tool_calls('{"name":"read_file","parameters":{"path":"a"}}')
    assert not parse_text_tool_calls('{"name":"launch_missiles","arguments":{}}')
    assert not parse_text_tool_calls("just prose")


async def test_repeat_loop_is_stopped(harness, ws_dir, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    same = {"tool_calls": [call("run_command", command="sudo ls")]}
    prov = Scripted([same] * 10)
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "go")
    assert h.events[-1] == {"type": "run_end", "status": "loop_detected"}
    assert len(prov.turns) >= 5    # did not burn all steps


def test_parse_embedded_json_in_prose():
    t = '# Plan\n1. list\n\n# Action\n{"name": "list_files", "arguments": {"path": ".", "recursive": false}}'
    c = parse_text_tool_calls(t)
    assert c and c[0]["function"]["name"] == "list_files"
    assert not parse_text_tool_calls('config = {"name": "x", "arguments": {}}')   # not a known tool


def test_oscillation_detected():
    recent = []
    a, b = [call("run_command", command="pytest")], [call("edit_file", path="x", old_str="1", new_str="2")]
    res = [AgentEngine._repeat_guard(recent, c) for c in [a, b] * 5]
    assert any(res)


def test_extract_strips_call_text():
    from app.agent.toolparse import extract_tool_calls
    calls, text = extract_tool_calls('Let me look.\n```json\n{"name":"list_files","arguments":{}}\n```\nDone.')
    assert calls and "list_files" not in text and "Let me look." in text and "Done." in text
    calls, text = extract_tool_calls('Plan\n{"name": "read_file", "arguments": {"path": "a"}}')
    assert calls and text == "Plan"


def test_xml_style_tool_call_parsed():
    from app.agent.toolparse import extract_tool_calls
    txt = ("I'll create it.\n<tool_call>\n<function=create_file>\n<parameter=path>\nswap.cpp\n</parameter>\n"
           "<parameter=content>\n#include <iostream>\nint main() {\n  return 0;\n}\n</parameter>\n"
           "<parameter=reason>demo</parameter>\n</function>\n</tool_call>")
    calls, clean = extract_tool_calls(txt)
    assert len(calls) == 1
    a = calls[0]["function"]["arguments"]
    assert calls[0]["function"]["name"] == "create_file" and a["path"] == "swap.cpp"
    assert a["content"] == "#include <iostream>\nint main() {\n  return 0;\n}\n" and a["reason"] == "demo"
    assert clean == "I'll create it."


def test_xml_style_single_line_unterminated_and_types():
    from app.agent.toolparse import extract_tool_calls
    calls, _ = extract_tool_calls("<function=create_file> <parameter=path> swap.cpp </parameter> <parameter=content> int x; </parameter> </function> </tool_call>")
    a = calls[0]["function"]["arguments"]
    assert a["path"] == "swap.cpp" and a["content"].strip() == "int x;"
    calls, _ = extract_tool_calls("<function=list_files><parameter=recursive>true</parameter>")
    assert calls[0]["function"]["arguments"] == {"recursive": True}
    assert extract_tool_calls("<function=rm_rf><parameter=x>1</parameter></function>")[0] == []


# ---------------------------------------------------------------- malformed tool calls (found on a real 16 KB HTML request)
BIG_HTML = ('<!DOCTYPE html>\n<html lang="en">\n<head><title>Zeus</title></head>\n<body>\n'
            '<button class="btn" onclick="go()">Go</button>\n<script>\nconst a = "x";\nfunction go(){ alert("hi"); }\n</script>\n</body>\n</html>')


def broken_json_call(content=BIG_HTML):
    """What the model actually produced: valid-looking JSON in a fence, with one unescaped quote inside the string."""
    esc = json.dumps(content)[1:-1].replace('onclick=\\"go()\\"', 'onclick=\\"go()"')     # break the escaping like the model did
    return '```json\n{\n  "name": "create_file",\n  "arguments": {\n    "path": "index.html",\n    "content": "' + esc + '"\n  }\n}\n```'


def test_lenient_parser_recovers_file_with_one_bad_quote():
    from app.agent.toolparse import extract_tool_calls, looks_like_tool_attempt
    text = broken_json_call()
    with __import__("pytest").raises(ValueError):
        json.loads(text.split("```json")[1].split("```")[0])           # strict JSON really is broken
    calls, clean = extract_tool_calls(text)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "create_file"
    a = calls[0]["function"]["arguments"]
    assert a["path"] == "index.html" and a["content"].startswith("<!DOCTYPE html>\n") and a["content"].rstrip().endswith("</html>")
    assert "\\n" not in a["content"] and 'function go(){ alert("hi"); }' in a["content"] and clean == ""
    assert looks_like_tool_attempt(text) and not looks_like_tool_attempt("Here is how a mutex works.")


def test_lenient_parser_keeps_reason_and_ignores_non_files():
    from app.agent.toolparse import extract_tool_calls
    t = '```json\n{"name": "create_file", "arguments": {"path": "a.txt", "content": "he said "hi"\\nbye", "reason": "demo"}}\n```'
    a = extract_tool_calls(t)[0][0]["function"]["arguments"]
    assert a["content"] == 'he said "hi"\nbye' and a["reason"] == "demo"
    assert extract_tool_calls('```json\n{"name": "delete_file", "arguments": {"path": "a" broken}}\n```')[0] == []   # only files are repaired


async def test_engine_retries_a_malformed_tool_call_instead_of_ending(harness, ws_dir, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m", num_ctx=4096)
    garbage = 'Sure:\n```json\n{"name": "create_file", "arguments": {"path": "a.py", "content": "x = (1,\n```'      # unparseable even leniently
    prov = Scripted([{"content": garbage}, {"tool_calls": [call("create_file", path="a.py", content="x=1", reason="r")]}, {"content": "done"}])
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "make a.py")
    assert (ws_dir / "a.py").read_text() == "x=1"                                  # recovered after one correction
    assert any(e["type"] == "notice" and "retry" in e["text"] for e in h.events)
    assert "could not be parsed" in prov.seen[1][-1]["content"]                     # the model was told what went wrong
    assert garbage not in [m["content"] for m in SessionManager(db).messages(s["id"])]   # garbage is not kept in history


async def test_engine_gives_up_after_two_retries(harness, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    bad = '{"name": "create_file", "arguments": {"path": '
    prov = Scripted([{"content": bad}, {"content": bad}, {"content": bad}])
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "go")
    assert [e["type"] for e in h.events][-1] == "run_end" and len([e for e in h.events if e["type"] == "notice"]) == 2
