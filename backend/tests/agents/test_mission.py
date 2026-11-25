import asyncio
import json

import pytest

from app.agents.engine import AgentEngine
from app.agents.mission import MARK_RE, MissionRunner, build_evidence, build_task, extract_json, parse_mission_md, read_verdict, referenced_files
from app.workspace.sessions import SessionManager
from app.toolchain.engine import ToolEngine

DOC = """# Todo CLI

## Objective
Build a tiny Python todo app.

## Acceptance criteria
- [ ] `todo.py` exists and supports add and list
- [ ] `test_todo.py` exists with passing tests
"""
CRIT = ["`todo.py` exists and supports add and list", "`test_todo.py` exists with passing tests"]


def call(name, **a): return {"function": {"name": name, "arguments": a}}


def verdict(*met, feedback=""):
    return json.dumps({"criteria": [{"id": i + 1, "met": m, "evidence": f"e{i + 1}"} for i, m in enumerate(met)],
                       "accepted": all(met), "feedback": feedback})


class FakeProvider:
    base_url = "http://fake"

    def __init__(self, worker, reviewer):
        self.worker, self.reviewer, self.seen, self.review_calls = list(worker), list(reviewer), [], []

    async def show(self, model): return {"capabilities": ["tools"], "parameters": ""}

    async def chat_stream(self, model, messages, tools, num_ctx, options=None, think=None):
        self.seen.append(messages)
        t = self.worker.pop(0)
        yield {"message": {"content": t.get("content", ""), "tool_calls": t.get("tool_calls")}}
        yield {"done": True, "prompt_eval_count": 10, "eval_count": 5, "eval_duration": 1_000_000_000}

    async def chat_once(self, model, messages, num_ctx=None, options=None, think=None):
        self.review_calls.append(messages)
        return self.reviewer.pop(0)


def make(harness, db, worker, reviewer, mode="trusted"):
    h = harness(mode)
    sm = SessionManager(db)
    s = sm.create(h.ctx.project["id"], model="m", num_ctx=16384)
    h.ctx.session_id = s["id"]                                   # the real app always sets this; file/command history is keyed by it
    prov = FakeProvider(worker, reviewer)
    return h, sm, s, prov, MissionRunner(db, AgentEngine(db, prov, ToolEngine()))


def types(h): return [e["type"] for e in h.events]


def test_document_parsing_extracts_title_objective_and_criteria():
    p = parse_mission_md(DOC, "todo.md")
    assert p.title == "Todo CLI" and p.objective == "Build a tiny Python todo app." and p.criteria == CRIT
    boxes = parse_mission_md("# X\nDo the thing.\n- [ ] one\n- [x] two\n* [ ] three\n", "x.md")
    assert boxes.criteria == ["one", "two", "three"] and boxes.objective == "Do the thing."
    bare = parse_mission_md("Just build me a website about cats.", "cats.md")
    assert bare.criteria == [] and "cats" in bare.objective and bare.title == "cats"
    numbered = parse_mission_md("# T\n## Requirements\n1. Must log\n2. Must exit 0\n## Notes\n- not a criterion\n", "t.md")
    assert numbered.criteria == ["Must log", "Must exit 0"]


def test_helpers():
    assert referenced_files("`todo.py` and src/app/main.py, see https://x.com/a.html v1.2.3") == ["todo.py", "src/app/main.py"]
    assert extract_json('Sure!\n```json\n{"a": {"b": 1}}\n```') == {"a": {"b": 1}}
    assert extract_json("<think>{\"x\":1}</think>{\"y\":2}") == {"y": 2}
    assert extract_json("no json here") is None
    task = build_task(7, "todo.md", "# Doc\n```py\nx\n```", ["a"], "be quick")
    assert MARK_RE.match(task) and "``````" not in task and "Extra instructions" in task and "1. a" in task and task.count("````markdown") == 1


def test_model_cannot_accept_its_own_work_when_a_criterion_is_unmet():
    v = read_verdict('{"criteria":[{"id":1,"met":true},{"id":2,"met":false,"evidence":"no tests"}],"accepted":true,"feedback":""}', CRIT)
    assert v["accepted"] is False and [r["met"] for r in v["results"]] == [True, False] and "(2)" in v["feedback"]
    assert read_verdict("not json", CRIT) is None
    assert read_verdict('{"criteria":[{"id":1,"met":"yes"},{"id":2,"met":true}]}', CRIT)["results"][0]["met"] is False   # truthy strings are not "true"


async def test_accepted_in_first_round(harness, ws_dir, db):
    h, sm, s, prov, runner = make(harness, db,
        [{"tool_calls": [call("create_file", path="todo.py", content="print('todo')", reason="r")]},
         {"tool_calls": [call("create_file", path="test_todo.py", content="import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertTrue(True)\n", reason="r")]},
         {"tool_calls": [call("run_command", command="python3 -m unittest", reason="run the tests")]}, {"content": "All done."}],
        [verdict(True, True)])
    res = await runner.run(h.ctx, s, "todo.md", DOC, "keep it small", 5)
    assert res["status"] == "accepted" and (ws_dir / "todo.py").exists() and (ws_dir / "test_todo.py").exists()
    t = types(h)
    assert t[0] == "run_start" and t[-1] == "run_end" and t.count("mission_round") == 1 and "mission_verifying" in t
    chk = [e for e in h.events if e["type"] == "mission_check"][0]
    assert chk["accepted"] and all(r["met"] for r in chk["results"])
    assert [e for e in h.events if e["type"] == "mission_end"][0]["status"] == "accepted"
    row = db.one("SELECT * FROM missions"); assert row["status"] == "accepted" and row["round"] == 1
    first_user = sm.messages(s["id"])[0]
    assert MARK_RE.match(first_user["content"]) and "keep it small" in first_user["content"] and "Todo CLI" in first_user["content"]
    # the model is told what the work is but never sees the bookkeeping marker
    assert "<!--zeus" not in json.dumps(prov.seen[0])
    # the reviewer saw real evidence: the file contents and the existence checks
    ev = prov.review_calls[0][1]["content"]
    assert "print('todo')" in ev and "todo.py: EXISTS" in ev and "test_todo.py: EXISTS" in ev


async def test_rejected_then_accepted_after_feedback(harness, ws_dir, db):
    h, sm, s, prov, runner = make(harness, db,
        [{"tool_calls": [call("create_file", path="todo.py", content="x=1", reason="r")]}, {"content": "done"},
         {"tool_calls": [call("create_file", path="test_todo.py", content="import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertTrue(True)\n", reason="r")]},
         {"tool_calls": [call("run_command", command="python3 -m unittest", reason="run the tests")]}, {"content": "now done"}],
        [verdict(True, False, feedback="Add test_todo.py with at least one passing test."), verdict(True, True)])
    res = await runner.run(h.ctx, s, "todo.md", DOC, "", 5)
    assert res["status"] == "accepted" and t_count(h, "mission_round") == 2
    checks = [e for e in h.events if e["type"] == "mission_check"]
    assert [c["accepted"] for c in checks] == [False, True] and "Add test_todo.py" in checks[0]["feedback"]
    fb = [m for m in sm.messages(s["id"]) if m["role"] == "user" and "[Zeus-Agent review" in m["content"]]
    assert len(fb) == 1 and "(2)" in fb[0]["content"] and "Add test_todo.py" in fb[0]["content"]
    # round 2 actually sent the feedback to the model (marker stripped)
    round2 = json.dumps(prov.seen[2])
    assert "NOT accepted yet" in round2 and "<!--zeus" not in round2
    assert db.one("SELECT round FROM missions")["round"] == 2


def t_count(h, kind): return len([e for e in h.events if e["type"] == kind])


async def test_stalled_mission_stops_instead_of_burning_rounds(harness, db):
    h, sm, s, prov, runner = make(harness, db, [{"content": "I think it's done"}] * 6, [verdict(False, False, feedback="nothing exists")] * 6)
    res = await runner.run(h.ctx, s, "todo.md", DOC, "", 6)
    assert res["status"] == "stalled" and t_count(h, "mission_round") == 2                # round 2 changed nothing -> stop
    assert [e for e in h.events if e["type"] == "mission_end"][0]["status"] == "stalled" and h.events[-1]["status"] == "stalled"


async def test_round_limit(harness, db):
    worker = []
    for i in range(3):
        worker += [{"tool_calls": [call("create_file", path=f"f{i}.py", content="x", reason="r")]}, {"content": "done"}]
    h, sm, s, prov, runner = make(harness, db, worker, [verdict(False, False, feedback="more")] * 3)
    res = await runner.run(h.ctx, s, "todo.md", DOC, "", 3)
    assert res["status"] == "limit" and t_count(h, "mission_round") == 3 and db.one("SELECT status FROM missions")["status"] == "limit"


async def test_unreadable_reviewer_is_not_treated_as_acceptance(harness, db):
    h, sm, s, prov, runner = make(harness, db, [{"content": "done"}] * 3, ["blah", "still not json"])
    res = await runner.run(h.ctx, s, "todo.md", DOC, "", 1)
    chk = [e for e in h.events if e["type"] == "mission_check"][0]
    assert res["status"] == "limit" and not chk["accepted"] and "could not produce a verdict" in chk["feedback"]
    assert len(prov.review_calls) == 2                                                    # one retry with a stricter instruction


async def test_document_without_criteria_gets_them_extracted(harness, ws_dir, db):
    extraction = json.dumps({"objective": "A cat website", "criteria": ["index.html exists", "It mentions cats"]})
    (ws_dir / "index.html").write_text("<p>cats</p>")                                    # the criterion names this file; it must really exist
    h, sm, s, prov, runner = make(harness, db, [{"content": "done"}] * 3, [extraction, verdict(True, True)])
    res = await runner.run(h.ctx, s, "cats.md", "Build me a website about cats.", "", 1)
    assert res["status"] == "accepted"
    start = [e for e in h.events if e["type"] == "mission_start"][0]
    assert [c["text"] for c in start["criteria"]] == ["index.html exists", "It mentions cats"] and start["objective"] == "A cat website"
    assert any(e["type"] == "notice" and "extract" in e["text"] for e in h.events)


async def test_extraction_failure_falls_back_to_one_criterion(harness, db):
    h, sm, s, prov, runner = make(harness, db, [{"content": "done"}] * 3, ["garbage", verdict(True)])
    await runner.run(h.ctx, s, "cats.md", "Build me a website about cats.", "", 1)
    start = [e for e in h.events if e["type"] == "mission_start"][0]
    assert len(start["criteria"]) == 1 and "fully achieved" in start["criteria"][0]["text"]


async def test_stop_marks_mission_stopped(harness, db):
    h, sm, s, prov, runner = make(harness, db, [{"content": "x"}], [verdict(True, True)])

    async def slow(*a, **k):
        await asyncio.sleep(5)
    runner.engine.loop = slow
    task = asyncio.create_task(runner.run(h.ctx, s, "todo.md", DOC, "", 3))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db.one("SELECT status FROM missions")["status"] == "stopped"
    assert [e for e in h.events if e["type"] == "mission_end"][0]["status"] == "stopped"


async def test_permissions_still_apply_during_missions(harness, ws_dir, db):
    h, sm, s, prov, runner = make(harness, db,
        [{"tool_calls": [call("create_file", path="todo.py", content="x", reason="r")]}, {"content": "done"}], [verdict(False, False, feedback="x")], mode="approval")
    await runner.run(h.ctx, s, "todo.md", DOC, "", 1)         # harness answers "reject" by default
    assert not (ws_dir / "todo.py").exists() and [r["action"] for r in h.requests] == ["create_file"]
    assert not [e for e in h.events if e["type"] == "notice" and "did not do it" in e["text"]]       # a refusal is respected, not nagged


def test_evidence_lists_deleted_and_missing_files(db, ws_dir):
    from tests.conftest import Harness
    pid = db.x("INSERT INTO projects(name,path,last_opened) VALUES('p',?,0)", (str(ws_dir),))
    sid = db.x("INSERT INTO sessions(project_id,title,created,updated,uid) VALUES(?,?,0,0,'a'*1)", (pid, "t")) if False else db.x("INSERT INTO sessions(project_id,title,created,updated,uid) VALUES(?,?,0,0,?)", (pid, "t", "u" * 32))
    db.x("INSERT INTO file_changes(session_id,project_id,path,op,before,after,created) VALUES(?,?,?,?,?,?,?)", (sid, pid, "a.py", "create", None, "A" * 9000, 10.0))
    db.x("INSERT INTO file_changes(session_id,project_id,path,op,before,after,created) VALUES(?,?,?,?,?,?,?)", (sid, pid, "b.py", "delete", "x", None, 11.0))
    ev = build_evidence(db, {"path": str(ws_dir)}, sid, 0, "Create `a.py` and `ghost.py`", [], "final words")
    assert "b.py: DELETED" in ev and "chars omitted" in ev and "ghost.py: MISSING" in ev and "final words" in ev


async def test_plan_only_reply_is_sent_back_to_act(harness, ws_dir, db):
    """Real failure (qwen2.5-coder:7b): the model answered a mission with a numbered plan and no tool call."""
    plan = "1. Create todo.py\n2. Create test_todo.py\n3. Run the tests"
    h, sm, s, prov, runner = make(harness, db,
        [{"content": plan}, {"tool_calls": [call("create_file", path="todo.py", content="print(1)", reason="r")]},
         {"tool_calls": [call("create_file", path="test_todo.py", content="import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertTrue(True)\n", reason="r")]},
         {"tool_calls": [call("run_command", command="python3 -m unittest", reason="r")]}, {"content": "Done."}],
        [verdict(True, True)])
    res = await runner.run(h.ctx, s, "todo.md", DOC, "", 2)
    assert res["status"] == "accepted" and (ws_dir / "todo.py").exists()
    assert any(e["type"] == "notice" and "did not do it" in e["text"] for e in h.events)
    assert "NOTHING has been created" in json.dumps(prov.seen[1]) and "todo.py" in json.dumps(prov.seen[1])      # nudge includes the criteria


async def test_action_nudges_are_capped_and_not_used_for_plain_chat(harness, db):
    h, sm, s, prov, runner = make(harness, db, [{"content": "plan"}] * 3, [verdict(False, False)])
    await runner.run(h.ctx, s, "todo.md", DOC, "", 1)
    assert len([e for e in h.events if e["type"] == "notice" and "did not do it" in e["text"]]) == 2        # asks twice, then gives up
    # ordinary prompts (no mission) never get this treatment
    h.events.clear()
    s2 = SessionManager(db).create(h.ctx.project["id"], model="m")
    prov2 = FakeProvider([{"content": "Mutexes lock."}], [])
    await AgentEngine(db, prov2, ToolEngine()).run(h.ctx, s2, "explain mutexes")
    assert not [e for e in h.events if e["type"] == "notice"] and len(prov2.seen) == 1


def test_hard_checks_overrule_a_lenient_reviewer():
    from pathlib import Path
    from app.agents.mission import hard_checks, last_failure
    crit = ["`todo.py` exists", "All tests pass when run with `python3 -m unittest`", "A README explains usage"]
    ok = [{"id": i, "text": c, "met": True, "evidence": "looks good"} for i, c in enumerate(crit, 1)]
    failed = [{"command": "python3 -m unittest", "exit_code": 1}]
    out = hard_checks(ok, crit, failed, Path("/nonexistent-dir"))
    assert [r["met"] for r in out] == [False, False, True]                       # file missing; latest test run failed; README criterion names no file
    assert "MISSING" in out[0]["evidence"] and "exit code 1" in out[1]["evidence"]
    assert hard_checks(ok, crit, [], Path("/nonexistent-dir"))[1]["evidence"].startswith("No test command")
    passing = [{"command": "python3 -m unittest -q", "exit_code": 0}, {"command": "python3 -m unittest", "exit_code": 1}]     # newest first
    assert hard_checks([dict(ok[1])], [crit[1]], passing, Path("."))[0]["met"] is True      # latest run passed: reviewer's verdict stands
    assert last_failure([{"command": "a", "exit_code": 0}, {"command": "b", "exit_code": 2}])["command"] == "b" and last_failure([]) is None


async def test_failed_tests_block_acceptance_and_their_output_goes_back_to_the_agent(harness, ws_dir, db):
    (ws_dir / "todo.py").write_text("x=1")
    h, sm, s, prov, runner = make(harness, db,
        [{"tool_calls": [call("run_command", command="python3 -c 'import sys; print(\"boom\"); sys.exit(1)' && unittest", reason="r")]}, {"content": "ran it"},
         {"tool_calls": [call("run_command", command="true && echo unittest", reason="r")]}, {"content": "fixed"}],
        [verdict(True, True), verdict(True, True)])
    crit_doc = "# T\n## Acceptance criteria\n- [ ] `todo.py` exists\n- [ ] The unit tests pass\n"
    h.ctx.runner = __import__("app.toolchain.runner", fromlist=["Runner"]).Runner()
    res = await runner.run(h.ctx, s, "t.md", crit_doc, "", 3)
    checks = [e for e in h.events if e["type"] == "mission_check"]
    assert not checks[0]["accepted"] and "exit code" in checks[0]["results"][1]["evidence"]       # reviewer said met; the exit code said no
    fb = [m for m in sm.messages(s["id"]) if m["role"] == "user" and "[Zeus-Agent review" in m["content"]][0]["content"]
    assert "boom" in fb and "most recent failing command" in fb


def test_fabricated_tool_output_is_recognised():
    from app.toolchain.toolparse import looks_fabricated_tool_output as f
    assert f("<tool_response>\nCreated todo.py (1418 chars)\n</tool_response>") and f("Created todo.py (1418 chars)") and f("exit code: 1")
    assert not f("I created todo.py for you.") and not f("The exit code 1 means failure, so I will fix it.") and not f("")


async def test_engine_rejects_made_up_tool_results_and_retries(harness, ws_dir, db):
    h = harness("trusted")
    s = SessionManager(db).create(h.ctx.project["id"], model="m")
    h.ctx.session_id = s["id"]
    prov = FakeProvider([{"content": "<tool_response>\nCreated a.py (3 chars)\n</tool_response>"},
                         {"tool_calls": [call("create_file", path="a.py", content="x=1", reason="r")]}, {"content": "done"}], [])
    await AgentEngine(db, prov, ToolEngine()).run(h.ctx, s, "make a.py")
    assert (ws_dir / "a.py").read_text() == "x=1"
    assert any(e["type"] == "notice" and "made-up tool result" in e["text"] for e in h.events)
    assert not [m for m in SessionManager(db).messages(s["id"]) if "<tool_response>" in m["content"]]        # fiction is not kept in history


async def test_mission_saves_files_the_model_only_printed_then_continues(harness, ws_dir, db):
    """Real failure (qwen2.5-coder:7b): every round it printed `# todo.py` code blocks and never called create_file."""
    printed = ("```python\n# todo.py\nprint('todo')\n```\n```python\n# todos.json\n[]\n```\n"
               "```python\n# test_todo.py\nimport unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.assertTrue(True)\n```\n")
    h, sm, s, prov, runner = make(harness, db,
        [{"content": printed}, {"tool_calls": [call("run_command", command="python3 -m unittest", reason="run the tests")]}, {"content": "All done."}],
        [verdict(True, True)])
    res = await runner.run(h.ctx, s, "todo.md", DOC, "", 2)
    assert (ws_dir / "todo.py").read_text() == "print('todo')\n" and (ws_dir / "todos.json").read_text() == "[]\n" and (ws_dir / "test_todo.py").exists()
    assert res["status"] == "accepted"
    assert len([e for e in h.events if e["type"] == "notice" and "Saving it for you" in e["text"]]) == 3
    assert "call create_file yourself" in json.dumps(prov.seen[1])                      # the model is told to use the tool from now on
    # the same behaviour must respect the permission engine: nothing is written without approval
    h2 = harness  # (approval path covered below)


async def test_printed_files_still_need_approval(harness, ws_dir, db):
    h, sm, s, prov, runner = make(harness, db, [{"content": "```python\n# todo.py\nx=1\n```"}] * 3, [verdict(False, False)], mode="approval")
    await runner.run(h.ctx, s, "todo.md", DOC, "", 1)                                    # harness rejects every approval request
    assert not (ws_dir / "todo.py").exists() and h.requests and h.requests[0]["action"] == "create_file"


async def test_flaky_state_dependent_tests_are_caught_by_the_confirmation_run(harness, ws_dir, db):
    """Real failure: the model's tests only passed on a clean slate (they wrote to the real todos.json), and Zeus-Agent accepted the first run."""
    test_py = ("import os, unittest\n"
               "class T(unittest.TestCase):\n"
               "    def test_fresh_state(self):\n"
               "        self.assertFalse(os.path.exists('state.marker'))   # passes only the first time\n"
               "        open('state.marker', 'w').write('x')\n")
    h, sm, s, prov, runner = make(harness, db,
        [{"tool_calls": [call("create_file", path="test_todo.py", content=test_py.replace("\\\n", "\n"), reason="r")]},
         {"tool_calls": [call("run_command", command=f"cd {ws_dir} && python3 -m unittest", reason="run the tests")]}, {"content": "All tests pass."}],
        [verdict(True, True)])
    crit_doc = "# T\n## Acceptance criteria\n- [ ] `test_todo.py` exists\n- [ ] The unit tests pass\n"
    h.ctx.runner = __import__("app.toolchain.runner", fromlist=["Runner"]).Runner()
    res = await runner.run(h.ctx, s, "t.md", crit_doc, "", 1)
    assert any(e["type"] == "notice" and "Confirming the tests pass" in e["text"] for e in h.events)
    chk = [e for e in h.events if e["type"] == "mission_check"][0]
    assert not chk["accepted"] and res["status"] == "limit"                               # first run passed, confirmation run failed -> not accepted
    assert "exit code" in chk["results"][1]["evidence"] and chk["results"][0]["met"] is True
    runs = db.q("SELECT exit_code FROM executions ORDER BY id")
    assert [r["exit_code"] for r in runs] == [0, 1]


async def test_confirmation_run_is_skipped_without_a_tests_criterion_and_respects_permissions(harness, ws_dir, db):
    h, sm, s, prov, runner = make(harness, db, [{"tool_calls": [call("create_file", path="a.py", content="x=1", reason="r")]}, {"content": "done"}], [verdict(True)])
    await runner.run(h.ctx, s, "t.md", "# T\n## Acceptance criteria\n- [ ] `a.py` exists\n", "", 1)
    assert not [e for e in h.events if e["type"] == "notice" and "Confirming" in e["text"]]


def test_degenerate_documents_do_not_crash_the_parser():
    assert parse_mission_md("# Only a title", "a.md").title == "Only a title"
    assert parse_mission_md("", "empty.md").title == "empty"
    assert parse_mission_md("\n\n   \n", "blank.md").criteria == []
    assert parse_mission_md("- [ ] just a box", "box.md").criteria == ["just a box"]
