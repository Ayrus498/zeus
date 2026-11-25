"""Turns backend events into terminal output. Pure rendering: no I/O besides the rich Console it is given."""
from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from .cards import answer_card, clean_output, output_card, preview_text
from .fmt import clip
from .theme import DIM, ERR, OK, PURPLE, TAGLINE, WARN

READ_TOOLS = {"list_files": "listed", "read_file": "read", "search_files": "searched", "git_status": "git status", "git_diff": "git diff"}
FILE_OPS = {"create": ("✚", OK, "created"), "edit": ("✎", WARN, "edited"), "delete": ("✖", ERR, "deleted")}


def tool_summary(name: str, args: dict | None) -> str:
    a = args or {}
    for k in ("path", "command", "pattern", "query"):
        if a.get(k):
            return clip(str(a[k]), 90)
    for v in a.values():
        if isinstance(v, str) and v:
            return clip(v, 90)
    return ""


class Renderer:
    """Turns backend events into cards. In the full-screen UI the model's text is shown as a live preview while it streams and then
    replaced by one parsed, formatted answer card; commands become one output box each; reads and edits become one-line rows."""

    def __init__(self, console: Console, ui=None):
        self.c = console
        self.ui = ui                 # FullUI: live preview + spinner live there; None = plain print mode
        self.stream = ""             # raw text of the assistant message being streamed
        self.answer: list[str] = []  # everything the assistant said this turn (for /copy)
        self.last_output = ""        # output of the last command (for /copy)
        self.status = None           # rich spinner (print mode only)
        self.usage: dict | None = None
        self.edited: list[str] = []
        self.mission_round = 0
        self.call: dict | None = None
        self.cmd: dict | None = None
        self.cmd_done = False

    # ------------------------------------------------------------------ spinner (print mode)
    def wait(self):
        if self.ui is None and self.status is None and self.c.is_terminal:
            self.status = self.c.status(Text(TAGLINE, style=PURPLE), spinner="dots", spinner_style=PURPLE)
            self.status.start()

    def quiet(self):
        if self.status is not None:
            self.status.stop()
            self.status = None

    def _live(self, text: str, glyph: str = "⚡ "):
        if self.ui is not None:
            self.ui.set_live(text, glyph)

    def _row(self, *parts: tuple[str, str]):
        self.quiet()
        self.c.print(Text.assemble(*parts))

    def _flush_answer(self, text: str | None = None):
        """Show the assistant's message as a card. `text` is the engine's cleaned message (tool-call JSON removed)."""
        raw = self.stream if text is None else text
        self.stream = ""
        self._live("")
        if not raw.strip():
            return
        self.quiet()
        self.answer.append(raw.strip())
        if self.ui is not None or self.c.is_terminal:
            self.c.print(answer_card(raw))
        else:
            self.c.print(raw.strip())

    def _end_stream(self):
        self._flush_answer()

    # ------------------------------------------------------------------ events
    def handle(self, ev: dict) -> None:
        fn = getattr(self, f"on_{ev.get('type')}", None)
        if fn:
            fn(ev)

    def on_run_start(self, ev):
        self.edited, self.usage = [], None
        self.wait()

    def on_model_selected(self, ev):
        why = f" · {clip(ev['reason'], 100)}" if ev.get("reason") else ""
        self._row(("  ↪ ", PURPLE), (f"{ev.get('model')}", "bold"), (f" ({ev.get('role')}){why}", DIM))

    def on_model_loading(self, ev):
        self.wait()

    def on_token(self, ev):
        text = ev.get("text", "")
        if not text:
            return
        self.quiet()
        self.stream += text
        self._live(self.stream)        # print mode: nothing is shown until the message is complete and cleaned

    def on_assistant_end(self, ev):
        self._flush_answer(ev["text"] if "text" in ev else None)
        self.wait()

    # ---- tools
    def on_tool_call(self, ev):
        self._flush_answer()
        self.call = {"name": ev.get("name", "tool"), "args": ev.get("args") or {}}
        self.cmd_done = False
        if self.call["name"] == "run_command":
            self._live("$ " + clip(str(self.call["args"].get("command", "")), 100), glyph="")
        elif self.call["name"] not in READ_TOOLS and self.call["name"] not in ("create_file", "edit_file", "delete_file"):
            self._live(f"● {self.call['name']}({tool_summary(self.call['name'], self.call['args'])})", glyph="")

    def on_tool_result(self, ev):
        call, self.call = self.call or {"name": ev.get("name", "tool"), "args": {}}, None
        name, out, ok = call["name"], (ev.get("output") or "").strip(), ev.get("ok")
        self._live("")
        if name == "run_command":
            if not self.cmd_done:                      # missions run the confirmation command without command_* events
                code, body = None, out
                if out.startswith("exit code:"):
                    first, _, body = out.partition("\n")
                    code = int(first.split(":")[1].strip()) if first.split(":")[1].strip().lstrip("-").isdigit() else None
                self._command_card(str(call["args"].get("command", "")), body, code, None, "done" if code is not None or ok else "failed")
            self.cmd_done = False
        elif name in READ_TOOLS:
            n = len(out.splitlines()) if out else 0
            detail = {"list_files": f"{n} entries", "read_file": f"{n} lines", "search_files": f"{n} matches"}.get(name, clip(out.splitlines()[0], 80) if out else "clean")
            self._row(("  ◦ ", PURPLE), (READ_TOOLS[name], "bold"), (f"  {tool_summary(name, call['args'])}", ""), (f"  · {detail}", DIM))
        elif not ok:
            self._row(("  ✗ ", ERR), (name, "bold"), (f"  {tool_summary(name, call['args'])}", ""), (f"  {clip(out.splitlines()[0], 120) if out else 'failed'}", ERR))
        # successful file operations are reported by the file_change event right before this

    # ---- commands
    def on_command_start(self, ev):
        self.cmd = {"cmd": ev.get("command", ""), "lines": [], "buf": "", "t0": time.time()}
        self.cmd_done = False
        self._live(preview_text(self.cmd["cmd"], []), glyph="")

    def on_command_output(self, ev):
        c = self.cmd
        if c is None:
            return
        c["buf"] += ev.get("text", "")
        *lines, c["buf"] = c["buf"].split("\n")
        c["lines"].extend(lines)
        del c["lines"][:-3000]
        self._live(preview_text(c["cmd"], c["lines"] + ([c["buf"]] if c["buf"] else [])), glyph="")

    def on_command_end(self, ev):
        c, self.cmd = self.cmd or {"cmd": "", "lines": [], "buf": "", "t0": time.time()}, None
        out = "\n".join(c["lines"] + ([c["buf"]] if c["buf"] else []))
        self._live("")
        self._command_card(c["cmd"], out, ev.get("exit_code"), ev.get("duration") or (time.time() - c["t0"]), ev.get("status") or "done")
        self.cmd_done = True
        self.wait()

    def _command_card(self, cmd: str, out: str, code, duration, status: str):
        self.quiet()
        self.last_output = clean_output(out)
        self.c.print(output_card(cmd, out, exit_code=code, duration=duration, status=status))

    # ---- files
    def on_file_change(self, ev):
        glyph, colour, verb = FILE_OPS.get(ev.get("op"), ("•", DIM, "changed"))
        self.edited.append(ev.get("path", ""))
        self._row((f"  {glyph} ", colour), (verb, f"bold {colour}"), (f"  {ev.get('path', '')}", "bold"))

    # ---- misc
    def on_notice(self, ev):
        self._row(("  ℹ ", DIM), (clip(ev.get("text", ""), 220), DIM if ev.get("level") != "error" else ERR))

    def on_compaction(self, ev):
        self._flush_answer()
        self._row(("  Σ ", PURPLE), ("summarised", "bold"), (f"  the first {ev.get('covers')} messages (about {ev.get('tokens_before', 0):,} → {ev.get('tokens_after', 0):,} tokens); /summary shows it", DIM))

    def on_memory_saved(self, ev):
        self._row(("  ✎ ", PURPLE), ("project memory" if ev.get("added") else "already in project memory", "bold"), (f"  {ev.get('note', '')}", DIM))

    def on_error(self, ev):
        self._flush_answer()
        self._row(("  ✗ ", ERR), (ev.get("message", "error"), ERR))

    def on_usage(self, ev):
        self.usage = ev

    # ------------------------------------------------------------------ missions
    def on_mission_start(self, ev):
        self._flush_answer()
        self.quiet()
        crit = ev.get("criteria") or []
        body = Text()
        body.append(f"{ev.get('objective') or ev.get('title') or ''}\n", style="bold")
        for c in crit:
            body.append(f"  ☐ {clip(c.get('text', c) if isinstance(c, dict) else c, 110)}\n", style=DIM)
        self.c.print(Panel(body, title=Text(f" Mission · {ev.get('name', '')} ", style=f"bold {PURPLE}"), title_align="left", border_style=PURPLE, padding=(0, 1)))
        self.wait()

    def on_mission_round(self, ev):
        self.mission_round = ev.get("round", 0)
        self._row((f"▶ Round {ev.get('round')}", f"bold {PURPLE}"))
        self.wait()

    def on_mission_verifying(self, ev):
        self._row(("  ◌ reviewing the result…", DIM))
        self.wait()

    def on_mission_check(self, ev):
        self.quiet()
        for r in ev.get("results") or []:
            ok = r.get("ok") if isinstance(r, dict) else None
            txt = r.get("text", "") if isinstance(r, dict) else str(r)
            self.c.print(Text.assemble(("  ✓ " if ok else "  ✗ ", OK if ok else ERR), (clip(txt, 110), DIM)))
        if ev.get("feedback"):
            self.c.print(Text("  feedback: " + clip(ev["feedback"], 200), style=DIM))
        self.wait()

    def on_mission_end(self, ev):
        st = ev.get("status", "")
        col = OK if st in ("accepted", "done", "completed") else WARN
        self._row((f"■ Mission {st}", f"bold {col}"), (f" – {clip(ev.get('summary', ''), 200)}" if ev.get("summary") else "", DIM))

    # ------------------------------------------------------------------ end
    def on_run_end(self, ev):
        self._flush_answer()
        self.quiet()
        st = ev.get("status", "done")
        if st == "stopped":
            self.c.print(Text("  ■ stopped", style=WARN))
        elif st == "step_limit":
            self.c.print(Text("  ■ stopped at the step limit", style=ERR))
        u = self.usage
        if u:
            tps = f" · {u['tokens_per_sec']:.0f} tok/s" if u.get("tokens_per_sec") else ""
            self.c.print(Text(f"  {u.get('prompt_tokens', 0):,} in / {u.get('output_tokens', 0):,} out{tps} · context {u.get('context_used', 0):,}"
                              f" · {len(set(self.edited))} file(s) changed", style=DIM))
        self.c.print()


def permission_panel(ev: dict) -> Panel:
    """The approval request shown inline, before asking Yes / Yes for session / No."""
    from rich.console import Group
    d = ev.get("detail") or {}
    head = Text.assemble((f"{ev.get('action', '').replace('_', ' ')}  ", f"bold {WARN}"), (f"{ev.get('target', '')}", "bold"))
    parts: list = [head]
    if ev.get("reason"):
        parts.append(Text(clip(ev["reason"], 200), style=DIM))
    if d.get("diff"):
        parts.append(Syntax(d["diff"][:6000], "diff", theme="monokai", word_wrap=True, background_color="default"))
    elif d.get("content"):
        parts.append(Syntax(d["content"][:3000], "text", theme="monokai", word_wrap=True, background_color="default"))
    if d.get("cwd"):
        parts.append(Text(f"in {d['cwd']}", style=DIM))
    return Panel(Group(*parts), title=Text(" ⚠ Permission needed ", style=f"bold {WARN}"), title_align="left", border_style=WARN, padding=(0, 1))
