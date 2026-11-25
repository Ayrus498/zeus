"""Inline arrow-key selection lists and text prompts (prompt_toolkit), used by every slash command."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.styles import Style

from .theme import PURPLE

STYLE = Style.from_dict({
    "title": f"bold {PURPLE}", "pointer": f"bold {PURPLE}", "sel": f"bold {PURPLE}", "dim": "ansibrightblack",
    "cur": "ansigreen", "off": "ansibrightblack italic", "hint": "ansibrightblack", "q": "bold",
})


@dataclass
class Option:
    label: str
    value: Any = None
    hint: str = ""
    disabled: bool = False
    group: str | None = None      # non-selectable heading row when value is None and group is set


def filter_options(options: list[Option], query: str) -> list[Option]:
    """Case-insensitive, all-terms match on label + hint; headings are dropped while filtering."""
    terms = query.lower().split()
    if not terms:
        return list(options)
    return [o for o in options if o.group is None and all(t in f"{o.label} {o.hint}".lower() for t in terms)]


class _State:
    def __init__(self, options: list[Option], current: Any, searchable: bool, page: int):
        self.all, self.searchable, self.page = options, searchable, page
        self.query = ""
        self.cur = current
        self.idx = 0
        self.top = 0
        self.refresh(initial=True)

    @property
    def shown(self) -> list[Option]:
        return filter_options(self.all, self.query)

    def selectable(self) -> list[int]:
        return [i for i, o in enumerate(self.shown) if o.group is None and not o.disabled]

    def refresh(self, initial: bool = False):
        sel = self.selectable()
        if initial and self.cur is not None:
            for i in sel:
                if self.shown[i].value == self.cur:
                    self.idx = i
                    break
            else:
                self.idx = sel[0] if sel else 0
        else:
            self.idx = sel[0] if sel else 0
        self.scroll()

    def move(self, d: int):
        sel = self.selectable()
        if not sel:
            return
        pos = sel.index(self.idx) if self.idx in sel else 0
        self.idx = sel[max(0, min(len(sel) - 1, pos + d))] if abs(d) < 99 else (sel[0] if d < 0 else sel[-1])
        self.scroll()

    def scroll(self):
        if self.idx < self.top:
            self.top = self.idx
        elif self.idx >= self.top + self.page:
            self.top = self.idx - self.page + 1
        if self.idx >= 1 and self.top > 0 and self.shown[self.top].group is None and self.top - 1 >= 0 and self.shown[self.top - 1].group is not None and self.idx == self.top:
            self.top -= 1   # keep the heading above the first visible item


def render(st: _State, title: str, hint: str | None) -> FormattedText:
    out: list[tuple[str, str]] = [("class:title", f"{title}\n")]
    if st.searchable:
        out += [("class:dim", "  search: "), ("", st.query), ("class:pointer", "▏\n")]
    shown = st.shown
    if not shown:
        out.append(("class:off", "  no matches\n"))
    window = shown[st.top: st.top + st.page]
    for off, o in enumerate(window):
        i = st.top + off
        if o.group is not None and o.value is None:
            out.append(("class:dim", f"  {o.group}\n"))
            continue
        on = i == st.idx
        mark = "❯ " if on else "  "
        is_cur = st.cur is not None and o.value == st.cur
        style = "class:off" if o.disabled else ("class:sel" if on else "")
        out.append(("class:pointer" if on else "", mark))
        out.append((style, o.label))
        if is_cur:
            out.append(("class:cur", "  ✓ current"))
        if o.hint:
            out.append(("class:hint", f"  {o.hint}"))
        out.append(("", "\n"))
    if len(shown) > st.page:
        out.append(("class:dim", f"  ({st.top + 1}-{min(len(shown), st.top + st.page)} of {len(shown)})\n"))
    out.append(("class:hint", "  " + (hint or "↑/↓ move · Enter select · Esc cancel" + (" · type to search" if st.searchable else "")) + "\n"))
    return FormattedText(out)


async def choose(title: str, options: list[Option], *, current: Any = None, searchable: bool = False, hint: str | None = None,
                 page: int = 12, pipe_input=None, output=None) -> Any:
    """Show a list and return the chosen option's value, or None when cancelled."""
    st = _State(options, current, searchable, page)
    kb = KeyBindings()
    result: dict[str, Any] = {"v": None, "done": False}

    @kb.add(Keys.Up)
    @kb.add("c-p")
    def _(e):
        st.move(-1)

    @kb.add(Keys.Down)
    @kb.add("c-n")
    def _(e):
        st.move(1)

    @kb.add(Keys.PageUp)
    def _(e):
        st.move(-st.page)

    @kb.add(Keys.PageDown)
    def _(e):
        st.move(st.page)

    @kb.add(Keys.Home)
    def _(e):
        st.move(-99999)

    @kb.add(Keys.End)
    def _(e):
        st.move(99999)

    @kb.add(Keys.Enter)
    def _(e):
        sel = st.selectable()
        if st.idx in sel:
            result["v"], result["done"] = st.shown[st.idx].value, True
            e.app.exit(result=st.shown[st.idx].value)

    @kb.add(Keys.Escape, eager=True)
    @kb.add("c-c")
    @kb.add("c-d")
    def _(e):
        e.app.exit(result=None)

    @kb.add(Keys.Backspace)
    def _(e):
        if st.searchable and st.query:
            st.query = st.query[:-1]
            st.refresh()

    @kb.add(Keys.Any)
    def _(e):
        ch = e.data
        if len(ch) != 1 or not ch.isprintable():
            return
        if st.searchable:
            st.query += ch
            st.refresh()
        elif ch.isdigit() and ch != "0":
            n = int(ch) - 1
            real = [o for o in st.shown if o.group is None]
            if n < len(real) and not real[n].disabled:
                e.app.exit(result=real[n].value)
        elif ch in "jk":
            st.move(1 if ch == "j" else -1)

    ctl = FormattedTextControl(lambda: render(st, title, hint), show_cursor=False)
    app = Application(layout=Layout(HSplit([Window(ctl, dont_extend_height=True)])), key_bindings=kb, style=STYLE, full_screen=False,
                      erase_when_done=True, input=pipe_input, output=output)
    return await app.run_async()


class _DirCompleter(Completer):
    def __init__(self, only_dirs=True):
        self.inner = PathCompleter(only_directories=only_dirs, expanduser=True)

    def get_completions(self, document, complete_event):
        yield from self.inner.get_completions(document, complete_event)


async def ask_text(prompt: str, *, default: str = "", paths: bool = False, only_dirs: bool = True, pipe_input=None, output=None,
                   password: bool = False) -> str | None:
    """Single-line input; None on Esc / Ctrl+C. Optional path completion."""
    kb = KeyBindings()

    @kb.add(Keys.Escape, eager=True)
    def _(e):
        e.app.exit(exception=KeyboardInterrupt())

    sess: PromptSession = PromptSession(key_bindings=kb, completer=_DirCompleter(only_dirs) if paths else None, complete_while_typing=paths,
                                        style=STYLE, input=pipe_input, output=output, is_password=password)
    try:
        return await sess.prompt_async(FormattedText([("class:q", prompt + " "), ("class:pointer", "❯ ")]), default=default)
    except (KeyboardInterrupt, EOFError):
        return None


async def confirm(question: str, *, default: bool = False, pipe_input=None, output=None) -> bool:
    v = await choose(question, [Option("Yes", True), Option("No", False)], current=default, pipe_input=pipe_input, output=output)
    return bool(v)
