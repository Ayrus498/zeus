"""Pure builders for what the terminal shows: markdown answers, command-output cards, user bubbles. No I/O."""
from __future__ import annotations

import re

from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock, Heading, Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text
from rich.theme import Theme

from .fmt import clip, short_path
from .theme import DIM, ERR, OK, PURPLE, PURPLE_DEEP, WARN

CODE_THEME = "monokai"
MAX_OUTPUT_LINES = 40
USER_BG = "on #1c1c21"

THEME = Theme({
    "markdown.code": "bold #c4b5fd on #27272a", "markdown.link": f"underline {PURPLE}", "markdown.link_url": DIM,
    "markdown.item.bullet": f"bold {PURPLE}", "markdown.item.number": f"bold {PURPLE}", "markdown.block_quote": "italic grey70",
    "markdown.hr": PURPLE_DEEP, "markdown.em": "italic", "markdown.strong": "bold",
})

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]")


def clean_output(text: str) -> str:
    """Strip terminal escape codes and collapse carriage-return progress updates to their last state."""
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    return "\n".join(line.split("\r")[-1] for line in text.split("\n"))


class _Heading(Heading):
    """# and ## become a purple bar + title; deeper levels a small marker. (rich's default is a boxed, centered title.)"""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text = self.text
        text.justify = "left"
        level = int(self.tag[1:])
        if level <= 2:
            yield Text.assemble(("▍ ", f"bold {PURPLE}"), (text.plain, "bold #ddd6fe"))
            if level == 1:
                yield Rule(style=PURPLE_DEEP, characters="─")
        else:
            yield Text.assemble(("▸ ", PURPLE), (text.plain, "bold"))


class _Code(CodeBlock):
    """Fenced code as a titled box (language on the top edge) with syntax colours."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        code = str(self.text).rstrip("\n")
        lang = self.lexer_name or "text"
        syn = Syntax(code, lang if lang != "text" else "text", theme=self.theme, word_wrap=True, background_color="default", padding=0)
        yield Panel(syn, title=Text(f" {lang} ", style=f"bold {PURPLE}"), title_align="left", border_style=PURPLE_DEEP, box=box.ROUNDED, padding=(0, 1))


class ZMarkdown(Markdown):
    elements = {**Markdown.elements, "heading_open": _Heading, "fence": _Code, "code_block": _Code}

    def __init__(self, text: str):
        super().__init__(text, code_theme=CODE_THEME, hyperlinks=False, inline_code_theme=CODE_THEME)


def answer_card(text: str, title: str = "ZEUS") -> Panel:
    return Panel(ZMarkdown(text.strip()), title=Text.assemble(("⚡ ", f"bold {PURPLE}"), (title, f"bold {PURPLE}")), title_align="left",
                 border_style=PURPLE_DEEP, box=box.ROUNDED, padding=(0, 1))


def user_card(text: str) -> Padding:
    lines = text.splitlines() or [""]
    t = Text.assemble(("❯ ", f"bold {PURPLE}"), (lines[0], "bold"))
    for extra in lines[1:]:
        t.append("\n  " + extra, style="bold")
    return Padding(t, (0, 1), style=USER_BG, expand=True)


def _looks_like_diff(cmd: str, out: str) -> bool:
    return bool(re.match(r"^\s*git\s+(diff|show|log\s+-p)", cmd)) or out.startswith("diff --git") or bool(re.search(r"^@@ .* @@", out, re.M))


def output_card(cmd: str, output: str, *, exit_code: int | None, duration: float | None = None, status: str = "done", cwd: str | None = None,
                max_lines: int = MAX_OUTPUT_LINES) -> Panel:
    """A command and everything it printed, in one box: green edge when it succeeded, red when it failed."""
    out = clean_output(output).rstrip("\n")
    lines = out.split("\n") if out else []
    hidden = max(0, len(lines) - max_lines)
    shown = "\n".join(lines[:max_lines])
    ok = exit_code == 0 and status in ("done", "completed", "ok")
    colour = OK if ok else (WARN if status in ("stopped", "cancelled", "timeout") else ERR)
    if not lines:
        body: Text | Syntax | Group = Text("(no output)", style=DIM)
    elif _looks_like_diff(cmd, out):
        body = Syntax(shown, "diff", theme=CODE_THEME, word_wrap=True, background_color="default")
    else:
        body = Text(shown, overflow="fold", no_wrap=False)
    parts = [body]
    if hidden:
        parts.append(Text(f"… {hidden} more line{'s' if hidden != 1 else ''} · /copy copies everything", style=DIM))
    state = f"exit {exit_code}" if status in ("done", "completed", "ok") and exit_code is not None else status
    sub = Text.assemble(("●", colour), (f" {state}", colour), (f" · {duration:.1f}s" if duration else "", DIM), (f" · {short_path(cwd, 40)}" if cwd else "", DIM))
    return Panel(Group(*parts), title=Text.assemble(("$ ", f"bold {PURPLE}"), (clip(cmd, 110), "bold")), title_align="left", subtitle=sub, subtitle_align="right",
                 border_style=colour, box=box.ROUNDED, padding=(0, 1))


def preview_text(cmd: str, lines: list[str], rows: int = 6) -> str:
    """Live preview for a running command: the command line and the last few lines it printed."""
    tail = [l for l in lines[-rows:]]
    return "\n".join([f"$ {clip(cmd, 100)}", *("  " + l[:160] for l in tail)])
