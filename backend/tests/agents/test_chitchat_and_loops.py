"""Greetings are answered without tools; a model that keeps repeating a tool call gets a nudge and then one chance to answer in words."""
import pytest

from app.agents.engine import AgentEngine
from app.agents.intent import is_chitchat
from app.workspace.sessions import SessionManager
from app.toolchain.engine import ToolEngine


@pytest.mark.parametrize("text", ["HI", "hi", "Hi!", "hello", "Hello there", "hey", "heyyy", "good morning", "Good Evening!", "how are you", "how are you?", "thanks", "Thank you so much",
                                  "thx", "ok", "okay", "bye", "who are you", "what can you do?", "hi, how are you", "hello how are you doing", "test", "ping", "help"])
def test_conversation_is_recognised(text):
    assert is_chitchat(text), text


@pytest.mark.parametrize("text", ["", "   ", "hi, create a todo app", "thanks, now add tests", "hello world program in c", "create hello.py", "fix the failing test", "help me fix this bug",
                                  "what can you do with this repository", "list the files", "run the tests", "who wrote this function", "hi " * 20, "ok so please refactor the database layer",
                                  "test the login page", "explain main.py", "x" * 100])
def test_real_requests_keep_their_tools(text):
    assert not is_chitchat(text), text


class Spy:
    """Scripted turns; remembers whether tools were offered and the last message of each request."""
    def __init__(self, turns):
        self.turns, self.tools, self.last, self.systems = list(turns), [], [], []

    async def chat_stream(self, model, messages, tools, num_ctx, options=None, think=None):
        self.tools.append(bool(tools))
        self.last.append(messages[-1]["content"])
        self.systems.append(messages[0]["content"] if messages[0]["role"] == "system" else "")
        t = self.turns.pop(0)
        yield {"message": {"content": t.get("content", ""), "tool_calls": t.get("tool_calls")}}
        yield {"done": True, "prompt_eval_count": 10, "eval_count": 5, "eval_duration": 1_000_000_000}


def call(name, **a):
    return {"function": {"name": name, "arguments": a}}


async def run(harness, db, turns, text, mode="trusted"):
    h = harness(mode)
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    prov = Spy(turns)
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, text)
    return h, prov


async def test_a_greeting_is_answered_without_tools(harness, db):
    h, prov = await run(harness, db, [{"content": "Hello! How can I help with your project?"}], "HI")
    assert prov.tools == [False] and "no tools are available" in prov.systems[0]
    assert not [e for e in h.events if e["type"] == "tool_call"] and h.events[-1] == {"type": "run_end", "status": "completed"}
    assert "Hello! How can I help" in "".join(e["text"] for e in h.events if e["type"] == "token")


async def test_a_model_that_answers_hi_with_a_tool_call_does_not_get_it_executed(harness, db, ws_dir):
    (ws_dir / "keep.txt").write_text("x")
    h, prov = await run(harness, db, [{"tool_calls": [call("list_files", path=".")]}, {"tool_calls": [call("list_files", path=".")]}], "hi")
    assert not [e for e in h.events if e["type"] in ("tool_call", "tool_result")]                       # nothing was executed
    assert h.events[-1]["type"] == "run_end"


async def test_a_real_request_still_gets_its_tools(harness, db):
    h, prov = await run(harness, db, [{"tool_calls": [call("list_files", path=".")]}, {"content": "done"}], "hi, list the files please")
    assert prov.tools[0] is True and [e["name"] for e in h.events if e["type"] == "tool_call"] == ["list_files"]


async def test_the_second_identical_call_triggers_a_nudge_before_anything_else(harness, db):
    same = {"tool_calls": [call("list_files", path=".")]}
    h, prov = await run(harness, db, [same, same, {"content": "The folder is empty."}], "show me the project")
    assert "same tool call twice" in prov.last[2]                                                    # the third request carries the nudge
    assert h.events[-1] == {"type": "run_end", "status": "completed"}


async def test_a_model_stuck_in_a_loop_gets_one_chance_to_answer_in_words(harness, db):
    same = {"tool_calls": [call("list_files", path=".")]}
    h, prov = await run(harness, db, [same] * 4 + [{"content": "The project folder is empty. What would you like to build?"}], "look around and tell me what you find")
    notes = [e["text"] for e in h.events if e["type"] == "notice"]
    assert any("asking it to answer in words" in n for n in notes)
    assert prov.tools[-1] is False and "Do NOT call any tool again" in prov.last[-1]
    assert not [e for e in h.events if e["type"] == "error"] and h.events[-1] == {"type": "run_end", "status": "completed"}
    assert "What would you like to build?" in "".join(e["text"] for e in h.events if e["type"] == "token")


async def test_if_even_that_fails_the_run_stops_with_the_usual_message(harness, db):
    same = {"tool_calls": [call("list_files", path=".")]}
    h, prov = await run(harness, db, [same] * 10, "look around")
    assert h.events[-1] == {"type": "run_end", "status": "loop_detected"}
    assert any(e["type"] == "error" and "kept repeating" in e["message"] for e in h.events)
    assert 5 <= len(prov.tools) < 10 and prov.tools[-1] is False                                    # it tried the plain-words turn, then gave up; it did not burn every step


async def test_no_tool_runs_once_the_answer_in_words_turn_has_started(harness, db):
    same = {"tool_calls": [call("list_files", path=".")]}
    h, prov = await run(harness, db, [same] * 10, "look around")
    i = next(n for n, e in enumerate(h.events) if e["type"] == "notice" and "answer in words" in e["text"])
    after = h.events[i + 1:]
    assert not [e for e in after if e["type"] in ("tool_call", "tool_result", "permission_decision")]    # the final turn (and everything after) executes nothing
    before = [e for e in h.events[:i] if e["type"] == "tool_call"]
    assert 3 <= len(before) <= 5                                                                       # the loop was cut short, not run to the step limit


async def test_a_turn_without_tools_does_not_show_the_model_the_tool_instructions(harness, db):
    from app.toolchain.engine import TOOL_SCHEMAS
    h, prov = await run(harness, db, [{"content": "Hi! What are we building?"}], "hello")
    system = prov.systems[0]
    assert "no tools are available" in system and "friendly local coding assistant" in system
    assert not any(t["name"] in system for t in TOOL_SCHEMAS)                                        # no list_files, create_file ... for a small model to parrot
    assert "You work on the project by calling tools" not in system and "Workflow" not in system


async def test_a_turn_with_tools_still_gets_the_full_instructions(harness, db):
    h, prov = await run(harness, db, [{"content": "ok"}], "create hello.py")
    assert "Workflow" in prov.systems[0] and "create_file" in prov.systems[0]


async def test_the_nudges_tell_the_model_what_the_tool_actually_answered(harness, db):
    same = {"tool_calls": [call("list_files", path=".")]}
    h, prov = await run(harness, db, [same, same, same, same, {"content": "The folder is empty."}], "look around")
    assert "The result was: '(empty directory)'" in prov.last[2]                                     # the nudge after the second identical call
    assert "The last result was: '(empty directory)'" in prov.last[-1]                               # and the one before the plain-words turn
