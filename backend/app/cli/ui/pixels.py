"""Draw the baked icon in the terminal with half-block characters: each cell shows two vertically stacked pixels."""
from __future__ import annotations

from rich.style import Style
from rich.text import Text

from . import logo


def _px(row: str, x: int) -> str | None:
    h = row[x * 6: x * 6 + 6]
    return None if h == "......" else h


def logo_text(rows: list[str] | None = None) -> Text:
    rows = rows if rows is not None else logo.ROWS
    out = Text(no_wrap=True)
    w = len(rows[0]) // 6
    for y in range(0, len(rows), 2):
        top = rows[y]
        bot = rows[y + 1] if y + 1 < len(rows) else "." * len(top)
        for x in range(w):
            t, b = _px(top, x), _px(bot, x)
            if t and b:
                out.append("▀", Style(color=f"#{t}", bgcolor=f"#{b}"))
            elif t:
                out.append("▀", Style(color=f"#{t}"))
            elif b:
                out.append("▄", Style(color=f"#{b}"))
            else:
                out.append(" ")
        if y + 2 < len(rows):
            out.append("\n")
    return out


def logo_size() -> tuple[int, int]:
    """(columns, text rows) the logo occupies."""
    return len(logo.ROWS[0]) // 6, (len(logo.ROWS) + 1) // 2
