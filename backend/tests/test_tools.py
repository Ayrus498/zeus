import os


async def test_create_rejected_writes_nothing(harness, ws_dir):
    h = harness("approval", ["reject"])
    r = await h.call("create_file", path="app/main.py", content="print(1)")
    assert not r["ok"] and "NOT created" in r["output"]
    assert not (ws_dir / "app").exists()
    assert h.requests[0]["action"] == "create_file" and "print(1)" in h.requests[0]["detail"]["diff"]


async def test_create_approved(harness, ws_dir):
    h = harness("approval", ["once"])
    r = await h.call("create_file", path="app/main.py", content="print(1)\n", reason="entry")
    assert r["ok"] and (ws_dir / "app/main.py").read_text() == "print(1)\n"
    assert h.requests[0]["reason"] == "entry"


async def test_create_never_overwrites(harness, ws_dir):
    (ws_dir / "a.txt").write_text("keep")
    h = harness("trusted")
    r = await h.call("create_file", path="a.txt", content="new")
    assert not r["ok"] and (ws_dir / "a.txt").read_text() == "keep"


async def test_read_only_blocks_everything(harness, ws_dir):
    h = harness("read_only", ["once"] * 5)
    assert not (await h.call("create_file", path="a", content="x"))["ok"]
    assert not (await h.call("run_command", command="touch f"))["ok"]
    assert not (ws_dir / "a").exists() and not (ws_dir / "f").exists()
    assert h.requests == []   # never even prompts


async def test_edit_shows_diff_and_applies(harness, ws_dir):
    (ws_dir / "a.py").write_text("x = 1\ny = 2\n")
    h = harness("approval", ["once"])
    r = await h.call("edit_file", path="a.py", old_str="y = 2", new_str="y = 3")
    assert r["ok"] and (ws_dir / "a.py").read_text() == "x = 1\ny = 3\n"
    d = h.requests[0]["detail"]["diff"]
    assert "-y = 2" in d and "+y = 3" in d


async def test_edit_errors(harness, ws_dir):
    (ws_dir / "a.py").write_text("a\na\n")
    h = harness("trusted")
    assert "not found" in (await h.call("edit_file", path="a.py", old_str="zzz", new_str="q"))["output"]
    assert "matches 2" in (await h.call("edit_file", path="a.py", old_str="a", new_str="b"))["output"]
    assert (ws_dir / "a.py").read_text() == "a\na\n"


async def test_delete_needs_approval_even_trusted(harness, ws_dir):
    (ws_dir / "a.txt").write_text("hi")
    h = harness("trusted", ["reject"])
    assert not (await h.call("delete_file", path="a.txt"))["ok"] and (ws_dir / "a.txt").exists()
    h.answers = ["once"]
    assert (await h.call("delete_file", path="a.txt"))["ok"] and not (ws_dir / "a.txt").exists()


async def test_outside_workspace_blocked_even_trusted(harness, ws_dir, tmp_path):
    h = harness("trusted")
    for p in ["../evil.txt", str(tmp_path / "evil.txt")]:
        r = await h.call("create_file", path=p, content="x")
        assert not r["ok"] and "Blocked" in r["output"]
    assert not (tmp_path / "evil.txt").exists()
    r = await h.call("read_file", path="../../etc/passwd")
    assert not r["ok"]


async def test_symlink_write_blocked(harness, ws_dir, tmp_path):
    out = tmp_path / "out"; out.mkdir(); os.symlink(out, ws_dir / "l")
    h = harness("trusted")
    assert not (await h.call("create_file", path="l/x.txt", content="x"))["ok"]
    assert list(out.iterdir()) == []


async def test_command_rejected_not_run(harness, ws_dir):
    h = harness("approval", ["reject"])
    r = await h.call("run_command", command="touch ran.txt")
    assert not r["ok"] and not (ws_dir / "ran.txt").exists()
    assert h.requests[0]["target"] == "touch ran.txt"   # shown exactly


async def test_command_approved_streams_and_exit_code(harness, ws_dir):
    h = harness("approval", ["once"])
    r = await h.call("run_command", command="echo hello; echo err 1>&2; exit 3")
    assert not r["ok"] and "exit code: 3" in r["output"] and "hello" in r["output"] and "err" in r["output"]
    kinds = [e["type"] for e in h.events]
    assert "command_start" in kinds and "command_output" in kinds and "command_end" in kinds
    assert h.ctx.db.q("SELECT exit_code,status FROM executions")[0]["exit_code"] == 3


async def test_install_classified_and_asks_in_trusted(harness):
    h = harness("trusted", ["reject"])
    await h.call("run_command", command="pip install requests")
    assert h.requests[0]["action"] == "install_packages"


async def test_sudo_blocked_without_prompt(harness):
    h = harness("trusted", ["once"])
    r = await h.call("run_command", command="sudo rm -rf /var")
    assert not r["ok"] and h.requests == []


async def test_cwd_is_project(harness, ws_dir):
    h = harness("trusted")
    r = await h.call("run_command", command="pwd")
    assert str(ws_dir.resolve()) in r["output"]


async def test_timeout_kills(harness, ws_dir):
    h = harness("trusted")
    r = await h.tools.run_approved_command(h.ctx, "sleep 30", timeout=1)
    assert "timed out" in r["output"]


async def test_validation_errors(harness):
    h = harness("trusted")
    assert "unknown tool" in (await h.call("rm_rf", path="/"))["output"]
    assert "missing required" in (await h.call("create_file", path="a"))["output"]


async def test_search_and_list(harness, ws_dir):
    (ws_dir / "a.py").write_text("def foo():\n    pass\n"); (ws_dir / ".venv").mkdir(); (ws_dir / ".venv/x.py").write_text("def foo(): pass")
    h = harness()
    assert "a.py:1" in (await h.call("search_files", pattern="def foo"))["output"]
    assert ".venv" not in (await h.call("search_files", pattern="def foo"))["output"]
    assert "a.py" in (await h.call("list_files"))["output"]


async def test_changes_recorded(harness, ws_dir):
    h = harness("trusted")
    await h.call("create_file", path="a.txt", content="one")
    await h.call("edit_file", path="a.txt", old_str="one", new_str="two")
    rows = h.ctx.db.q("SELECT op,before,after FROM file_changes ORDER BY id")
    assert [r["op"] for r in rows] == ["create", "edit"] and rows[1]["before"] == "one" and rows[1]["after"] == "two"
