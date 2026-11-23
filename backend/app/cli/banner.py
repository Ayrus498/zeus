"""The welcome screen: thunder-bolt mark on the left, organised system information on the right."""
from __future__ import annotations

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .fmt import fmt_bytes, fmt_ctx, short_path
from .pixels import logo_size, logo_text
from .theme import BOLT, DIM, PURPLE, PURPLE_DEEP

MODE_LABEL = {"read_only": "Read only", "approval": "Approval (ask first)", "trusted": "Trusted workspace", "custom": "Custom"}


def _kv(rows: list[tuple[str, str | Text]]) -> Table:
    t = Table.grid(padding=(0, 2))
    t.add_column(style=DIM, no_wrap=True)
    t.add_column(overflow="fold")
    for k, v in rows:
        t.add_row(k, v)
    return t


def _heading(s: str) -> Text:
    return Text(s.upper(), style=f"bold {PURPLE}")


def session_rows(st: dict) -> list[tuple[str, str | Text]]:
    ol = st.get("ollama") or {}
    conn = Text("connected", style="green3") if ol.get("connected") else Text("not connected", style="red3")
    if ol.get("connected") and ol.get("version"):
        conn.append(f" · v{ol['version']}", style=DIM)
    model = st.get("model") or "none selected"
    return [("Model", model), ("Context", fmt_ctx(st.get("ctx"))), ("Permissions", MODE_LABEL.get(st.get("perm"), st.get("perm") or "?")),
            ("Project", short_path(st.get("project_path") or "?", 44)), ("Ollama", conn)]


def device_rows(about: dict | None) -> list[tuple[str, str | Text]]:
    if not about:
        return [("Device", "information unavailable")]
    cpu, mem = about.get("cpu") or {}, about.get("memory") or {}
    gpus = about.get("gpus") or []
    rows: list[tuple[str, str | Text]] = [("CPU", f"{cpu.get('name') or 'unavailable'}" + (f" · {cpu['cores']}c/{cpu['threads']}t" if cpu.get("cores") else ""))]
    for i, g in enumerate(gpus or [{}]):
        vram = f" · {fmt_bytes(g['vram_total'])} VRAM" if g.get("vram_total") else ""
        rows.append(("GPU" if len(gpus) < 2 else f"GPU {i + 1}", f"{g.get('name') or 'unavailable'}{vram}"))
    used = f" ({fmt_bytes(mem['used'])} in use)" if mem.get("used") is not None else ""
    rows.append(("RAM", f"{fmt_bytes(mem.get('total'))}{used}"))
    pl = about.get("platform") or {}
    rows.append(("OS", f"{pl.get('os', 'unknown')} · {pl.get('arch', '')}"))
    return rows


def info_panel(st: dict, about: dict | None) -> Group:
    return Group(_heading("Session"), _kv(session_rows(st)), Text(""), _heading("Device"), _kv(device_rows(about)))


def banner(st: dict, about: dict | None, width: int, color: bool = True) -> RenderableType:
    """Side-by-side when the terminal is wide enough, stacked otherwise."""
    version = (about or {}).get("version") or st.get("version") or ""
    mark = logo_text() if color else Text("\n".join(BOLT), style=f"bold {PURPLE}")
    left = Group(mark, Text(""), Text("Zeus-Agent", style=f"bold {PURPLE}"), Text("local coding agent", style=DIM),
                 Text("powered by Ollama", style=DIM))
    right = info_panel(st, about)
    if width >= 96:
        grid = Table.grid(expand=True, padding=(0, 3))
        grid.add_column(width=max(24, logo_size()[0] + 2), justify="center", vertical="middle")
        grid.add_column(width=1)
        grid.add_column(ratio=1)
        divider = Text("\n".join("│" for _ in range(logo_size()[1] + 5)), style=PURPLE_DEEP)
        grid.add_row(left, divider, right)
        body: RenderableType = grid
    else:
        body = Group(Text.assemble(mark), Text(""), right)
    return Panel(body, title=Text(f" Zeus-Agent {('v' + version) if version else ''} ", style=f"bold {PURPLE}"), title_align="left",
                 border_style=PURPLE_DEEP, box=box.ROUNDED, padding=(1, 2))


TIPS = [
    ("/help", "all commands"), ("/settings", "models, endpoint, about"), ("/ollama-model", "switch model"),
    ("/ollama-ctx", "context size"), ("/permissions", "what Zeus-Agent may do"), ("/project", "change folder"), ("/shell ls", "run a command here"),
]


def tips() -> Text:
    t = Text("  ")
    for i, (c, d) in enumerate(TIPS):
        t.append(c, style=f"bold {PURPLE}")
        t.append(f" {d}", style=DIM)
        t.append("   " if i < len(TIPS) - 1 else "")
    t.append("\n  Enter sends · Alt+Enter new line · PageUp/PageDown scroll · Ctrl+C stops a run · Ctrl+D exits", style=DIM)
    return t
