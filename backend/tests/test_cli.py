"""Terminal UI (`zeus --cli`): pure helpers, selection list, event rendering, slash completion, banner, and a pty smoke test."""
import asyncio
import io
import os
import sys

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from app.cli import banner as bn
from app.cli.app import ALIAS, COMMANDS, SlashCompleter, build_parser, resolve_dir
from app.cli.chooser import Option, choose, filter_options
from app.cli.fmt import clip, fmt_bytes, fmt_ctx, parse_ctx, short_path
from app.cli.models import catalog_options, group_of, installed_options
from app.cli.render import Renderer, permission_panel, tool_summary


def test_parse_ctx():
    assert parse_ctx("32k") == 32768 and parse_ctx("16384") == 16384 and parse_ctx("1m") == 1048576 and parse_ctx("24,576") == 24576
    assert parse_ctx("abc") is None and parse_ctx("10") is None and parse_ctx("99999999999") is None


def test_fmt():
    assert fmt_bytes(None) == "unavailable" and fmt_bytes(940 * 2**20) == "940 MB" and fmt_bytes(17.3 * 2**30).endswith("GB")
    assert fmt_ctx(262144) == "256K" and fmt_ctx(None) == "unknown" and fmt_ctx(24000) == "24,000"
    assert clip("a  b\nc", 10) == "a b c" and len(clip("x" * 50, 10)) == 10
    assert short_path(os.path.expanduser("~/proj")) == "~/proj"


def test_resolve_dir(tmp_path):
    (tmp_path / "a").mkdir()
    assert resolve_dir("a", tmp_path) == (tmp_path / "a").resolve()
    assert resolve_dir(str(tmp_path)) == tmp_path.resolve()
    assert str(resolve_dir("~")) == os.path.realpath(os.path.expanduser("~"))


def test_parser_and_aliases():
    a = build_parser().parse_args(["/x", "-m", "auto", "--ctx", "32k", "--permission-mode", "trusted", "-c"])
    assert (a.path, a.model, a.ctx, a.permission_mode, a.resume) == ("/x", "auto", 32768, "trusted", True)
    assert ALIAS["ollama-ctxt"] == "ollama-ctx" and ALIAS["model"] == "ollama-model" and ALIAS["perm"] == "permissions" and ALIAS["q"] == "exit"
    for need in ("settings", "ollama-model", "ollama-ctx", "permissions", "project"):
        assert need in COMMANDS and need in ALIAS


def test_group_of_matches_desktop_rules():
    assert group_of("hf.co/unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF:Q4") == "deepseek" and group_of("qwen3-coder:30b") == "qwen"
    assert group_of("nomic-embed-text:latest") == "embeddings" and group_of("tinyllama") == "other" and group_of("gpt-oss:20b") == "openai"


def test_installed_options_groups_and_disables_embeddings():
    ms = [{"name": "qwen3:8b", "size": 5e9}, {"name": "nomic-embed-text:latest", "size": 3e8}, {"name": "deepseek-r1:14b", "size": 9e9}]
    o = installed_options(ms, [{"name": "qwen3:8b"}])
    labels = [(x.group or x.label) for x in o]
    assert labels[0] == "Auto" and "Qwen (1)" in labels and "DeepSeek (1)" in labels and "Embeddings (1)" in labels
    assert next(x for x in o if x.label == "nomic-embed-text:latest").disabled
    assert "loaded" in next(x for x in o if x.label == "qwen3:8b").hint


def test_catalog_options_marks_installed():
    cat = [{"name": "qwen3", "title": "Qwen3", "fit": "best", "tags": [{"tag": "qwen3:8b", "latest": True, "size_gb": 5}]}]
    o = catalog_options(cat, {"qwen3:8b"})
    assert "Qwen (1)" == o[0].group and "installed" in o[1].hint


def test_filter_options():
    o = [Option("alpha", 1, "x"), Option("beta", 2, "big"), Option("", None, group="G")]
    assert [x.value for x in filter_options(o, "")] == [1, 2, None]
    assert [x.value for x in filter_options(o, "be big")] == [2] and filter_options(o, "zzz") == []


def run_choose(keys, options, **kw):
    async def go():
        with create_pipe_input() as inp:
            inp.send_text(keys)
            return await choose("T", options, pipe_input=inp, output=DummyOutput(), **kw)
    return asyncio.run(go())


OPTS = [Option("a", "A"), Option("", None, group="Group"), Option("b", "B"), Option("c", "C", disabled=True), Option("d", "D")]


def test_choose_arrows_enter_and_skips_headings_and_disabled():
    assert run_choose("\r", OPTS) == "A"
    assert run_choose("\x1b[B\r", OPTS) == "B"                 # heading skipped
    assert run_choose("\x1b[B\x1b[B\r", OPTS) == "D"           # disabled skipped
    assert run_choose("\x1b[A\x1b[A\r", OPTS) == "A"           # clamps at top


def test_choose_current_number_search_and_cancel():
    assert run_choose("\r", OPTS, current="D") == "D"
    assert run_choose("2", OPTS) == "B"
    assert run_choose("d\r", OPTS, searchable=True) == "D"     # typing filters
    assert run_choose("zz\r\x03", OPTS, searchable=True) is None   # no match: Enter does nothing, Ctrl+C cancels
    assert run_choose("\x03", OPTS) is None


def render_events(events, ui=None, width=100, terminal=True):
    buf = io.StringIO()
    from app.cli.cards import THEME
    r = Renderer(Console(file=buf, width=width, force_terminal=terminal, color_system=None, theme=THEME), ui)
    for ev in events:
        r.handle(ev)
    return r, buf.getvalue()


def test_renderer_cards_for_answer_command_and_files():
    r, out = render_events([
        {"type": "run_start"}, {"type": "model_selected", "model": "qwen3:8b", "role": "code", "reason": "file task"},
        {"type": "token", "text": "Hel"}, {"type": "token", "text": "lo **world**"},
        {"type": "assistant_end", "text": "Hello **world**\n\n- one\n- two\n\n```python\nprint(1)\n```"},
        {"type": "tool_call", "name": "read_file", "args": {"path": "a.py"}},
        {"type": "tool_result", "name": "read_file", "ok": True, "output": "l1\nl2\nl3"},
        {"type": "tool_call", "name": "create_file", "args": {"path": "b.py", "content": "x"}},
        {"type": "file_change", "op": "create", "path": "b.py"},
        {"type": "tool_result", "name": "create_file", "ok": True, "output": "Created b.py (1 chars)"},
        {"type": "tool_call", "name": "run_command", "args": {"command": "pytest -q"}},
        {"type": "command_start", "run_id": "r", "command": "pytest -q"},
        {"type": "command_output", "run_id": "r", "text": "1 pas"}, {"type": "command_output", "run_id": "r", "text": "sed\nok\n"},
        {"type": "command_end", "run_id": "r", "status": "done", "exit_code": 0, "duration": 0.4},
        {"type": "tool_result", "name": "run_command", "ok": True, "output": "exit code: 0\n1 passed\nok"},
        {"type": "usage", "prompt_tokens": 10, "output_tokens": 5, "tokens_per_sec": 20.0, "context_used": 15},
        {"type": "run_end", "status": "done"}])
    assert "⚡ Zeus-Agent" in out and "Hello world" in out and "• one" in out and "print(1)" in out and " python " in out
    assert "**" not in out                                    # markdown was parsed, not shown raw
    assert "read" in out and "a.py" in out and "3 lines" in out
    assert "created" in out and "b.py" in out and "Created b.py" not in out      # file ops are one row, no duplicate tool result
    assert "$ pytest -q" in out and "1 passed" in out and "exit 0" in out
    assert out.count("1 passed") == 1                         # the command output appears once (not again as a tool result)
    assert "10 in / 5 out" in out and "1 file(s) changed" in out and r.answer and r.last_output.startswith("1 passed")


def test_renderer_drops_tool_call_json_the_model_printed_as_text():
    r, out = render_events([{"type": "token", "text": '{"name": "run_command", "arguments": {}}'},
                            {"type": "assistant_end", "text": ""}, {"type": "run_end", "status": "done"}])
    assert "run_command" not in out and "⚡ Zeus-Agent" not in out and r.answer == []


def test_renderer_failed_command_and_mission_command_without_events():
    r, out = render_events([
        {"type": "tool_call", "name": "run_command", "args": {"command": "false"}},
        {"type": "command_start", "run_id": "r", "command": "false"},
        {"type": "command_end", "run_id": "r", "status": "done", "exit_code": 1, "duration": 0.1},
        {"type": "tool_result", "name": "run_command", "ok": False, "output": "exit code: 1\n"},
        {"type": "tool_call", "name": "run_command", "args": {"command": "pytest"}},                       # mission style: no command_* events
        {"type": "tool_result", "name": "run_command", "ok": False, "output": "exit code: 2\nFAILED test_a"}])
    assert "exit 1" in out and "exit 2" in out and "FAILED test_a" in out and out.count("$ pytest") == 1


def test_renderer_ui_mode_previews_while_streaming_then_commits_one_card():
    class FakeUI:
        def __init__(self):
            self.live = []

        def set_live(self, text, glyph="⚡ ", max_rows=6):
            self.live.append((text, glyph))
    ui = FakeUI()
    buf = io.StringIO()
    r = Renderer(Console(file=buf, width=60, force_terminal=True, color_system=None), ui)
    r.handle({"type": "token", "text": "Hello wor"})
    assert buf.getvalue() == "" and ui.live[-1] == ("Hello wor", "⚡ ")      # nothing committed while streaming
    r.handle({"type": "token", "text": "ld"})
    r.handle({"type": "assistant_end", "text": "Hello world"})
    assert "Hello world" in buf.getvalue() and ui.live[-1][0] == ""
    r.handle({"type": "command_start", "run_id": "x", "command": "ls"})
    r.handle({"type": "command_output", "run_id": "x", "text": "a\nb\n"})
    assert ui.live[-1][0].startswith("$ ls") and "b" in ui.live[-1][0] and ui.live[-1][1] == ""


def test_renderer_errors_and_mission():
    buf = io.StringIO()
    r = Renderer(Console(file=buf, width=100))
    for ev in [{"type": "error", "message": "boom"}, {"type": "run_end", "status": "stopped"},
               {"type": "mission_start", "id": 1, "name": "m.md", "objective": "Ship it", "criteria": [{"text": "tests pass"}]},
               {"type": "mission_round", "id": 1, "round": 1}, {"type": "mission_check", "id": 1, "results": [{"text": "tests pass", "ok": True}]},
               {"type": "mission_end", "id": 1, "status": "accepted", "summary": "done"}]:
        r.handle(ev)
    o = buf.getvalue()
    assert "boom" in o and "stopped" in o and "Mission" in o and "Round 1" in o and "tests pass" in o and "Mission accepted" in o


def test_tool_summary_and_permission_panel():
    assert tool_summary("run_command", {"command": "ls -la"}) == "ls -la" and tool_summary("x", {}) == ""
    c = Console(file=io.StringIO(), width=80)
    c.print(permission_panel({"action": "edit_file", "target": "a.py", "reason": "fix", "detail": {"diff": "-a\n+b"}}))
    assert "Permission needed" in c.file.getvalue() and "a.py" in c.file.getvalue()


def test_slash_completer():
    c = SlashCompleter()
    got = {x.text for x in c.get_completions(Document("/ol"), None)}
    assert "/ollama-model" in got and "/ollama-ctx" in got and "/ollama-ctxt" in got
    assert {x.text for x in c.get_completions(Document("/"), None)} >= {"/settings", "/permissions", "/project", "/help"}
    assert list(c.get_completions(Document("hello"), None)) == []


def test_banner_wide_and_narrow():
    about = {"version": "0.1.0", "cpu": {"name": "CPU X", "cores": 4, "threads": 8}, "gpus": [{"name": "GPU Y", "vram_total": 12 * 2**30}],
             "memory": {"total": 32 * 2**30, "used": 8 * 2**30}, "platform": {"os": "Ubuntu", "arch": "x86_64"}}
    st = {"model": "auto", "ctx": 32768, "perm": "approval", "project_path": "/tmp/p", "ollama": {"connected": True, "version": "0.1"}}
    for width in (120, 60):
        b = io.StringIO()
        Console(file=b, width=width).print(bn.banner(st, about, width))
        o = b.getvalue()
        assert "▀" in o and "CPU X" in o and "GPU Y" in o and "12.0 GB VRAM" in o and "RAM" in o and "auto" in o and "32K" in o
        assert all(len(line) <= width for line in o.splitlines())
    wide = io.StringIO()
    Console(file=wide, width=120).print(bn.banner(st, about, 120))
    row = next(l for l in wide.getvalue().splitlines() if "CPU X" in l)
    assert row.index("CPU X") > 30            # info is to the RIGHT of the bolt
    b = io.StringIO()
    Console(file=b, width=120).print(bn.banner(st, None, 120))
    assert "information unavailable" in b.getvalue()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
def test_launcher_survives_project_with_its_own_app_package(tmp_path):
    """The user's project folder is the cwd: a project containing app/ must not shadow Zeus-Agent's own `app` package."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    proj = tmp_path / "proj"
    (proj / "app").mkdir(parents=True)
    (proj / "app" / "__init__.py").write_text("raise RuntimeError('shadowed')")
    r = subprocess.run([os.path.join(root, "packaging", "zeus-cli"), "--version"], cwd=proj, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "Zeus-Agent" in r.stdout, r.stderr


def test_logo_pixels_are_the_app_icon():
    from app.cli import logo
    from app.cli.pixels import logo_size, logo_text
    assert len(logo.ROWS) == logo.SIZE and all(len(r) == logo.SIZE * 6 for r in logo.ROWS)
    cols, rows = logo_size()
    t = logo_text()
    assert t.plain.count("\n") + 1 == rows == 13 and all(len(line) == cols for line in t.plain.splitlines())
    # the bolt colour of the icon (purple) and its dark background are both present
    flat = "".join(logo.ROWS)
    assert "925fff" in flat.lower() or any(int(flat[i:i + 2], 16) > 120 and int(flat[i + 4:i + 6], 16) > 200 for i in range(0, len(flat), 6) if flat[i:i + 6] != "......")
    assert logo.ROWS[0][:6] == "......"             # rounded corner is transparent


def test_logo_renders_truecolor_half_blocks_and_has_ascii_fallback():
    from app.cli import banner as bn
    b = io.StringIO()
    Console(file=b, width=120, force_terminal=True, color_system="truecolor").print(bn.banner({"model": "x"}, None, 120))
    assert "38;2;" in b.getvalue() and "48;2;" in b.getvalue()
    n = io.StringIO()
    Console(file=n, width=120).print(bn.banner({"model": "x"}, None, 120, color=False))
    assert "▟████▛" in n.getvalue()


def make_ui():
    from prompt_toolkit.formatted_text import FormattedText
    from app.cli.ui import FullUI
    return FullUI(None, SlashCompleter(), lambda: FormattedText([("", "status")]), on_submit=lambda t: None, on_ctrl_c=lambda b: None, on_eof=lambda: None,
                  placeholder="Ask me")


def test_ui_log_console_collects_lines_and_follows_width():
    from app.cli.ui import LogConsole
    ui = make_ui()
    c = LogConsole(ui)
    c.print("hello")
    c.print("two\nlines")
    assert len(ui.log) == 3
    plain = ["".join(t for _, t in line) for line in ui.log]
    assert plain == ["hello", "two", "lines"]
    assert c.size.width == ui.cols() and c.color_system in ("truecolor", "256", "standard")


def test_ui_scrolling_window_and_indicator():
    ui = make_ui()
    ui.append_ansi([f"line {i}" for i in range(100)])
    ui.msg_win.render_info  # not rendered yet -> default height used
    h = ui._height()
    text = "".join(t for _, t in ui.visible_log())
    assert text.endswith("line 99") and f"line {100 - h}" in text and "more lines below" not in text      # follows the newest output
    ui.scroll_by(10)
    text = "".join(t for _, t in ui.visible_log())
    assert "more lines below" in text and "line 99" not in text and "line 89" in text
    ui.append_ansi(["new"])                                    # reading position holds when output arrives
    assert ui.scroll == 11
    ui.scroll_by(-10**6)
    assert ui.scroll == 0 and "".join(t for _, t in ui.visible_log()).endswith("new")
    ui.clear_log()
    assert ui.log == [] and ui.scroll == 0


def test_ui_live_preview_shows_the_last_rows_only():
    ui = make_ui()
    ui.append_ansi(["done"])
    ui.set_live("word " * 200, glyph="⚡ ", max_rows=4)
    rows = ["".join(t for _, t in r) for r in ui._live_rows()]
    assert len(rows) == 4 and not rows[0].startswith("⚡")      # truncated: only the tail is shown, so no glyph on the first row
    ui.set_live("$ ls\n  a\n  b", glyph="")
    rows = ["".join(t for _, t in r) for r in ui._live_rows()]
    assert rows == ["  $ ls", "    a", "    b"]
    text = "".join(t for _, t in ui.visible_log())
    assert text.startswith("done") and "$ ls" in text.splitlines()[-3]
    ui.set_live("")
    assert ui._live_rows() == []


def test_ui_box_has_placeholder_and_borders():
    from app.cli.ui import Placeholder
    ui = make_ui()
    assert ui.app.full_screen and ui.app.mouse_support() and ui.app.layout.current_buffer is ui.buf
    ph = Placeholder("hint")

    class TI:
        lineno = 0
        fragments = []

        class document:  # noqa: N801
            text = ""
    assert ph.apply_transformation(TI).fragments == [("class:placeholder", "hint")]
    TI.document.text = "typed"
    TI.fragments = [("", "typed")]
    assert ph.apply_transformation(TI).fragments == [("", "typed")]


async def test_ui_dialogs_resolve_with_the_chosen_value():
    ui = make_ui()

    async def pick():
        return await ui.choose("T", [Option("a", "A"), Option("b", "B")], current="A")
    task = asyncio.create_task(pick())
    await asyncio.sleep(0.05)
    assert ui.dialog is not None and ui.dialog.state.shown[ui.dialog.state.idx].value == "A"
    ui.dialog.state.move(1)
    ui._finish(ui.dialog.state.shown[ui.dialog.state.idx].value)
    assert await task == "B" and ui.dialog is None


def make_cli(tmp_path):
    from app.cli.app import Cli
    buf = io.StringIO()
    cli = Cli(None, Console(file=buf, width=100, force_terminal=False), tmp_path)
    cli.project = {"path": str(tmp_path), "id": 1}
    return cli, buf


async def test_shell_runs_in_project_folder_and_prints_output(tmp_path):
    (tmp_path / "alpha.txt").write_text("x")
    cli, buf = make_cli(tmp_path)
    await cli.run_shell("ls")
    out = buf.getvalue()
    assert "$ ls" in out and "alpha.txt" in out and "exit 0" in out and "╭" in out and "╰" in out and cli.working is False
    assert cli.last_copy.strip() == "alpha.txt"


async def test_shell_reports_stderr_and_exit_code(tmp_path):
    cli, buf = make_cli(tmp_path)
    await cli.run_shell("echo out; echo err 1>&2; exit 3")
    out = buf.getvalue()
    assert "out" in out and "err" in out and "exit 3" in out


async def test_shell_cd_usage_and_empty(tmp_path):
    cli, buf = make_cli(tmp_path)
    await cli.run_shell("cd /tmp")
    await cli.run_shell("   ")
    out = buf.getvalue()
    assert "cd does not stick" in out and "Usage: /shell" in out


async def test_shell_caps_long_output_and_times_out(tmp_path, monkeypatch):
    import app.cli.app as appmod
    cli, buf = make_cli(tmp_path)
    await cli.run_shell("seq 1 600")
    assert "560 more lines" in buf.getvalue() and "/copy" in buf.getvalue() and cli.last_copy.splitlines()[-1] == "600"
    monkeypatch.setattr(appmod, "SHELL_TIMEOUT", 0.5)
    cli2, buf2 = make_cli(tmp_path)
    await cli2.run_shell("sleep 30")
    assert "timeout" in buf2.getvalue() and cli2.shell_proc is None


async def test_shell_can_be_stopped(tmp_path):
    cli, buf = make_cli(tmp_path)
    task = asyncio.create_task(cli.run_shell("sleep 30"))
    await asyncio.sleep(0.4)
    assert cli.working and cli.shell_proc is not None
    cli.interrupt()
    await asyncio.wait_for(task, 5)
    assert "stopped" in buf.getvalue() and not cli.working


def test_shell_command_is_registered_and_completes():
    assert ALIAS["sh"] == "shell" and "shell" in COMMANDS and ALIAS["copy"] == "copy"
    got = {x.text for x in SlashCompleter().get_completions(Document("/sh"), None)}
    assert "/shell" in got


def test_copy_falls_back_to_osc52(monkeypatch):
    import app.cli.app as appmod
    monkeypatch.setattr(appmod.shutil, "which", lambda name: None)
    sent = []

    class Out:
        def write_raw(self, s):
            sent.append(s)

        def flush(self):
            pass
    assert appmod.copy_to_clipboard("héllo", Out()).startswith("terminal clipboard")
    import base64
    assert sent and sent[0].startswith("\x1b]52;c;") and base64.b64decode(sent[0][7:-1]).decode() == "héllo"
    assert appmod.copy_to_clipboard("x", None) is None


def test_ui_turns_fold_to_one_row_and_unfold_on_click():
    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton, MouseModifier
    from prompt_toolkit.data_structures import Point
    from app.cli.cards import user_card
    from app.cli.ui import LogConsole
    ui = make_ui()
    c = LogConsole(ui)
    c.print("banner")
    for n in (1, 2):
        ui.begin_turn(f"prompt {n}")
        c.print(user_card(f"prompt {n}"))
        c.print(f"output {n} a\noutput {n} b\noutput {n} c")
    plain = lambda: "\n".join("".join(t for _, t in r) for r in ui._visible_rows()[0])  # noqa: E731
    text = plain()
    assert "▾ prompt 1" in text and "output 1 b" in text and "output 2 c" in text
    ui.visible_log()
    head = ui._row_turn.index(1)                               # the row drawn for turn 1's prompt
    ctl = ui.msg_win.content
    ev = MouseEvent(position=Point(x=2, y=head), event_type=MouseEventType.MOUSE_UP, button=MouseButton.LEFT, modifiers=frozenset())
    assert ctl.mouse_handler(ev) is None                       # the click folds turn 1
    text = plain()
    assert "▸ prompt 1" in text and "lines hidden" in text
    assert "output 1" not in text and "output 2 b" in text and "banner" in text   # only that turn folded
    ui.visible_log()
    assert ui._row_turn.count(1) == 1                          # one row left for it
    assert ctl.mouse_handler(MouseEvent(position=Point(x=2, y=ui._row_turn.index(1)), event_type=MouseEventType.MOUSE_UP, button=MouseButton.LEFT, modifiers=frozenset())) is None
    assert "output 1 b" in plain() and "▾ prompt 1" in plain()      # click again: shown again
    ui.fold_all(True, keep_last=True)
    assert "output 1" not in plain() and "output 2 b" in plain()
    ui.fold_all(False)
    assert "output 1 b" in plain()
    ui.clear_log()
    assert ui.turns == {} and ui.turn_of == []
