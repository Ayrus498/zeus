"""The full-screen terminal layout: a scrolling message window on top, the input box pinned to the bottom row, a status line
under it. Dialogs (selection lists, text prompts) and the slash-command menu are drawn inside the same screen, so the box never
moves. Everything ZEUS prints goes to the message window through `LogConsole`."""
from __future__ import annotations

import asyncio
import io
import os
import random
import shutil
import textwrap
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, PathCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (BufferControl, ConditionalContainer, Dimension, FloatContainer, Float, FormattedTextControl, HSplit, Layout, VSplit,
                                   Window)
from prompt_toolkit.layout.controls import UIControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style, merge_styles
from rich.console import Console, ConsoleDimensions

from .chooser import STYLE as CHOOSER_STYLE
from .chooser import Option, _State, render as render_choices
from .cards import THEME
from .theme import PURPLE, PURPLE_DEEP

MAX_LOG_LINES = 20000
WHEEL_STEP = 3

PLACEHOLDERS = [
    "Ask ZEUS to build, fix or explain code…",
    "Try: fix the failing tests in this project",
    "Try: explain what this folder does",
    "Try: add input validation and tests for the signup form",
    "Type / for commands · /shell ls runs a command here",
    "Try: /mission plan.md  to run a document as a goal",
]

BOX_STYLE = Style.from_dict({
    "box": PURPLE, "prompt": f"bold {PURPLE}", "placeholder": "ansibrightblack italic", "hint": "ansibrightblack",
    "completion-menu": "bg:#1f1f23", "completion-menu.completion": "bg:#1f1f23 fg:default", "completion-menu.completion.current": f"bg:{PURPLE_DEEP} fg:#ffffff",
    "completion-menu.meta.completion": "bg:#1f1f23 fg:ansibrightblack", "completion-menu.meta.completion.current": f"bg:{PURPLE_DEEP} fg:#e5e7eb",
    "scrollbar.background": "bg:#1f1f23", "scrollbar.button": f"bg:{PURPLE_DEEP}", "dialog": "", "dialog.rule": PURPLE_DEEP,
})


class Placeholder(Processor):
    """Greyed hint text shown while the box is empty (the cursor stays at the start, so typing replaces it)."""

    def __init__(self, text: str | Callable[[], str]):
        self.text = text

    def apply_transformation(self, ti):
        if ti.lineno == 0 and not ti.document.text:
            return Transformation([("class:placeholder", self.text() if callable(self.text) else self.text)])
        return Transformation(ti.fragments)


def detect_color_system() -> str:
    ct = os.environ.get("COLORTERM", "").lower()
    if "truecolor" in ct or "24bit" in ct:
        return "truecolor"
    return "256" if "256" in os.environ.get("TERM", "") else "standard"


class LogSink(io.TextIOBase):
    """File-like target for rich: complete lines are appended to the message window."""

    def __init__(self, ui: "FullUI"):
        self.ui, self.pending = ui, ""

    def write(self, s: str) -> int:
        self.pending += s
        if "\n" in self.pending:
            *lines, self.pending = self.pending.split("\n")
            self.ui.append_ansi(lines)
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True


class LogConsole(Console):
    """A rich Console whose output lands in the message window and whose width follows the terminal."""

    def __init__(self, ui: "FullUI"):
        self._ui = ui
        super().__init__(file=LogSink(ui), force_terminal=True, color_system=detect_color_system(), legacy_windows=False, highlight=False, theme=THEME)

    @property
    def size(self) -> ConsoleDimensions:  # type: ignore[override]
        return ConsoleDimensions(self._ui.cols(), self._ui.rows())


class _LogControl(FormattedTextControl):
    def __init__(self, ui: "FullUI"):
        super().__init__(ui.visible_log, show_cursor=False, focusable=False)
        self.ui = ui

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.ui.scroll_by(WHEEL_STEP)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.ui.scroll_by(-WHEEL_STEP)
            return None
        return NotImplemented


class _Dialog:
    def __init__(self, kind: str, title: str, fut: asyncio.Future, state: _State | None = None, hint: str | None = None):
        self.kind, self.title, self.fut, self.state, self.hint = kind, title, fut, state, hint


def _line(left: str, mid: str, right: str) -> VSplit:
    return VSplit([Window(width=1, char=left, style="class:box"), Window(char=mid, style="class:box"), Window(width=1, char=right, style="class:box")], height=1)


class FullUI:
    def __init__(self, history_path: Path | None, completer: Completer, status: Callable[[], FormattedText], *,
                 on_submit: Callable[[str], None], on_ctrl_c: Callable[[Buffer], None], on_eof: Callable[[], None], placeholder: str | None = None):
        self.on_submit, self.on_ctrl_c, self.on_eof = on_submit, on_ctrl_c, on_eof
        self.placeholder = placeholder or random.choice(PLACEHOLDERS)
        self.log: list[FormattedText] = []
        self.scroll = 0                       # lines scrolled back from the bottom (0 = follow the newest output)
        self.live, self.live_glyph, self.live_max = "", "⚡ ", 6
        self.dialog: _Dialog | None = None
        self._cols, self._rows = shutil.get_terminal_size((100, 30))
        history = FileHistory(str(history_path)) if history_path else InMemoryHistory()
        dialog_on = Condition(lambda: self.dialog is not None)
        self.buf = Buffer(history=history, completer=completer, complete_while_typing=Condition(lambda: self.dialog is None), multiline=True,
                          enable_history_search=False, read_only=dialog_on)
        self.dbuf = Buffer(multiline=False)                       # the text field of a text dialog
        self.dbuf_paths = False

        def prefix(line_number: int, wrap_count: int):
            return [("class:prompt", "❯ ")] if line_number == 0 and wrap_count == 0 else [("class:hint", "  ")]

        def body_height() -> Dimension:
            n = max(1, min(10, self.buf.document.line_count))
            return Dimension(min=1, max=n, preferred=n)

        self.body = Window(BufferControl(buffer=self.buf, input_processors=[Placeholder(lambda: self.placeholder)]), height=body_height, wrap_lines=True,
                           get_line_prefix=prefix)
        side = lambda ch: Window(width=1, char=ch, style="class:box", height=body_height)  # noqa: E731
        box = HSplit([_line("╭", "─", "╮"), VSplit([side("│"), Window(width=1, height=body_height), self.body, Window(width=1, height=body_height), side("│")]),
                      _line("╰", "─", "╯")])
        self.msg_win = Window(_LogControl(self), wrap_lines=False, height=Dimension(weight=1, min=3))
        self.dwin = Window(BufferControl(buffer=self.dbuf), height=1)
        dialog_list = ConditionalContainer(
            Window(FormattedTextControl(self._dialog_text), dont_extend_height=True, wrap_lines=False),
            filter=Condition(lambda: self.dialog is not None and self.dialog.kind == "choose"))
        dialog_text = ConditionalContainer(
            HSplit([Window(FormattedTextControl(lambda: [("class:prompt", (self.dialog.title if self.dialog else "") + "\n")]), dont_extend_height=True, wrap_lines=True),
                    VSplit([Window(FormattedTextControl([("class:prompt", "❯ ")]), width=2), self.dwin]),
                    Window(FormattedTextControl([("class:hint", "  Enter to confirm · Esc to cancel · Tab completes paths")]), height=1)]),
            filter=Condition(lambda: self.dialog is not None and self.dialog.kind == "text"))
        status_bar = Window(FormattedTextControl(status), height=1)
        root = HSplit([self.msg_win, dialog_list, dialog_text, box, status_bar])
        menu = Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=9, scroll_offset=1))
        self.app = Application(layout=Layout(FloatContainer(root, floats=[menu]), focused_element=self.body), key_bindings=self._bindings(),
                               style=merge_styles([CHOOSER_STYLE, BOX_STYLE]), full_screen=True, mouse_support=True, refresh_interval=0.1)

    # ------------------------------------------------------------------ geometry
    def cols(self) -> int:
        try:
            return self.app.output.get_size().columns if self.app.is_running else shutil.get_terminal_size((100, 30)).columns
        except Exception:
            return self._cols

    def rows(self) -> int:
        try:
            return self.app.output.get_size().rows if self.app.is_running else shutil.get_terminal_size((100, 30)).lines
        except Exception:
            return self._rows

    def make_console(self) -> LogConsole:
        return LogConsole(self)

    # ------------------------------------------------------------------ message window
    def append_ansi(self, lines: list[str]) -> None:
        for ln in lines:
            self.log.append(FormattedText(to_formatted_text(ANSI(ln))))
        if len(self.log) > MAX_LOG_LINES:
            del self.log[: len(self.log) - MAX_LOG_LINES]
        if self.scroll:
            self.scroll += len(lines)         # keep the lines being read in place while new output arrives below
        self._invalidate()

    def clear_log(self) -> None:
        self.log.clear()
        self.scroll = 0
        self._invalidate()

    def set_live(self, text: str, glyph: str = "⚡ ", max_rows: int = 6) -> None:
        """Preview of what is being produced right now (the model's text, a running command): the last few rows, dimmed.
        It is replaced by the finished card, so the log only ever holds formatted results."""
        self.live, self.live_glyph, self.live_max = text, glyph, max_rows
        self._invalidate()

    def scroll_by(self, n: int) -> None:
        self.scroll = max(0, min(self.scroll + n, max(0, len(self.log) - 1)))
        self._invalidate()

    def _invalidate(self) -> None:
        if self.app.is_running:
            self.app.invalidate()

    def _live_rows(self) -> list[FormattedText]:
        if not self.live:
            return []
        glyph = self.live_glyph
        pad = " " * max(2, len(glyph))
        width = max(10, self.cols() - len(pad))
        rows: list[str] = []
        for para in self.live.split("\n"):
            rows.extend(textwrap.wrap(para, width=width, drop_whitespace=False, break_long_words=True, replace_whitespace=False) or [""])
        cut = len(rows) > self.live_max
        rows = rows[-self.live_max:]
        return [FormattedText([("class:prompt", glyph if (i == 0 and not cut and glyph) else pad), ("class:hint", r)]) for i, r in enumerate(rows)]

    def _height(self) -> int:
        info = self.msg_win.render_info
        return info.window_height if info else max(3, self.rows() - 6)

    def visible_log(self) -> FormattedText:
        live = self._live_rows()
        total = len(self.log) + len(live)
        h = self._height()
        self.scroll = min(self.scroll, max(0, total - h))
        end = total - self.scroll
        start = max(0, end - (h - 1 if self.scroll else h))      # one row is kept for the "more lines below" notice
        out: list[tuple[str, str]] = []
        for i in range(start, end):
            out.extend(self.log[i] if i < len(self.log) else live[i - len(self.log)])
            if i < end - 1:
                out.append(("", "\n"))
        if self.scroll:
            out.append(("class:hint", f"\n  ↓ {self.scroll} more lines below · PageDown / End to follow"))
        return FormattedText(out)

    # ------------------------------------------------------------------ dialogs (shown above the box, inside the same screen)
    def _dialog_text(self) -> FormattedText:
        if not self.dialog or not self.dialog.state:
            return FormattedText([])
        return render_choices(self.dialog.state, self.dialog.title, self.dialog.hint)

    async def choose(self, title: str, options: list[Option], *, current: Any = None, searchable: bool = False, hint: str | None = None, page: int = 12) -> Any:
        st = _State(options, current, searchable, min(page, max(4, self.rows() - 10)))
        return await self._run_dialog(_Dialog("choose", title, asyncio.get_running_loop().create_future(), st, hint))

    async def confirm(self, question: str, *, default: bool = False) -> bool:
        return bool(await self.choose(question, [Option("Yes", True), Option("No", False)], current=default))

    async def ask_text(self, prompt: str, *, default: str = "", paths: bool = False, only_dirs: bool = True) -> str | None:
        self.dbuf.completer = PathCompleter(only_directories=only_dirs, expanduser=True) if paths else None
        self.dbuf.complete_while_typing = Condition(lambda: paths)
        self.dbuf.reset()
        self.dbuf.text = default
        self.dbuf.cursor_position = len(default)
        return await self._run_dialog(_Dialog("text", prompt, asyncio.get_running_loop().create_future()))

    async def _run_dialog(self, d: _Dialog) -> Any:
        while self.dialog is not None:         # one dialog at a time
            await asyncio.sleep(0.05)
        self.dialog = d
        if d.kind == "text":
            self.app.layout.focus(self.dwin)
        self._invalidate()
        try:
            return await d.fut
        finally:
            self.dialog = None
            self.app.layout.focus(self.body)
            self._invalidate()

    def _finish(self, value: Any) -> None:
        if self.dialog and not self.dialog.fut.done():
            self.dialog.fut.set_result(value)

    # ------------------------------------------------------------------ keys
    def _bindings(self) -> KeyBindings:
        kb, buf = KeyBindings(), self.buf
        idle = Condition(lambda: self.dialog is None)
        choosing = Condition(lambda: self.dialog is not None and self.dialog.kind == "choose")
        texting = Condition(lambda: self.dialog is not None and self.dialog.kind == "text")

        # ---- normal typing
        @kb.add(Keys.Enter, filter=idle)
        def _(e):
            if buf.text.endswith("\\"):
                buf.delete_before_cursor(1)
                buf.insert_text("\n")
                return
            text = buf.text
            if text.strip():
                buf.reset(append_to_history=True)
                self.placeholder = random.choice(PLACEHOLDERS)
                self.scroll = 0
                self.on_submit(text)

        @kb.add(Keys.Escape, Keys.Enter, filter=idle)
        @kb.add("c-j", filter=idle)
        def _(e):
            buf.insert_text("\n")

        @kb.add("tab", filter=idle)
        def _(e):
            if buf.complete_state:
                buf.complete_next()
            else:
                buf.start_completion(select_first=True)

        @kb.add("s-tab", filter=idle)
        def _(e):
            if buf.complete_state:
                buf.complete_previous()

        @kb.add("c-c", filter=idle)
        def _(e):
            self.on_ctrl_c(buf)

        @kb.add("c-d", filter=idle)
        def _(e):
            if not buf.text:
                self.on_eof()

        @kb.add(Keys.Escape, filter=idle)       # not eager: Esc followed by Enter (= Alt+Enter) must still insert a newline
        def _(e):
            if buf.complete_state:
                buf.cancel_completion()

        @kb.add(Keys.PageUp, filter=idle)
        def _(e):
            self.scroll_by(max(3, self._height() - 2))

        @kb.add(Keys.PageDown, filter=idle)
        @kb.add("c-end", filter=idle)
        def _(e):
            self.scroll_by(-max(3, self._height() - 2) if e.key_sequence[-1].key != "c-end" else -10**9)

        # ---- selection list
        st = lambda: self.dialog.state  # noqa: E731

        @kb.add(Keys.Up, filter=choosing)
        @kb.add("c-p", filter=choosing)
        def _(e):
            st().move(-1)

        @kb.add(Keys.Down, filter=choosing)
        @kb.add("c-n", filter=choosing)
        def _(e):
            st().move(1)

        @kb.add(Keys.PageUp, filter=choosing)
        def _(e):
            st().move(-st().page)

        @kb.add(Keys.PageDown, filter=choosing)
        def _(e):
            st().move(st().page)

        @kb.add(Keys.Home, filter=choosing)
        def _(e):
            st().move(-99999)

        @kb.add(Keys.End, filter=choosing)
        def _(e):
            st().move(99999)

        @kb.add(Keys.Enter, filter=choosing)
        def _(e):
            s = st()
            if s.idx in s.selectable():
                self._finish(s.shown[s.idx].value)

        @kb.add(Keys.Escape, filter=choosing | texting, eager=True)
        @kb.add("c-c", filter=choosing | texting)
        @kb.add("c-d", filter=choosing)
        def _(e):
            self._finish(None)

        @kb.add(Keys.Backspace, filter=choosing)
        def _(e):
            s = st()
            if s.searchable and s.query:
                s.query = s.query[:-1]
                s.refresh()

        @kb.add(Keys.Any, filter=choosing)
        def _(e):
            s, ch = st(), e.data
            if len(ch) != 1 or not ch.isprintable():
                return
            if s.searchable:
                s.query += ch
                s.refresh()
            elif ch.isdigit() and ch != "0":
                real = [o for o in s.shown if o.group is None]
                n = int(ch) - 1
                if n < len(real) and not real[n].disabled:
                    self._finish(real[n].value)
            elif ch in "jk":
                s.move(1 if ch == "j" else -1)

        # ---- text field
        @kb.add(Keys.Enter, filter=texting)
        def _(e):
            self._finish(self.dbuf.text)

        @kb.add("tab", filter=texting)
        def _(e):
            if self.dbuf.complete_state:
                self.dbuf.complete_next()
            else:
                self.dbuf.start_completion(select_first=True)

        return kb

    async def run(self) -> None:
        await self.app.run_async()

    def exit(self) -> None:
        if self.app.is_running:
            self.app.exit()
