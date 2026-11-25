"""Terminal views for project memory, the context meter and the rolling summary. Content comes from the backend (`/api/sessions/{id}/context`)."""
from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from ..ui.fmt import fmt_ctx
from ..ui.theme import DIM, ERR, OK, PURPLE, WARN


def meter(used: int, window: int, trigger: int, width: int = 32) -> Text:
    pct = min(100, round(100 * used / window)) if window else 0
    fill = round(width * pct / 100)
    mark = round(width * trigger / 100)
    bar = Text()
    for i in range(width):
        ch = "█" if i < fill else ("┊" if i == mark else "░")
        bar.append(ch, style=(WARN if pct >= trigger else PURPLE) if i < fill else DIM)
    bar.append(f"  {pct}%", style="bold")
    bar.append(f"  ~{used:,} of {fmt_ctx(window)} tokens", style=DIM)
    return bar


def context_view(c: dict) -> Group:
    parts: list = [Text.assemble(("  Context  ", f"bold {PURPLE}")), Text("  ") + meter(c["tokens"], c["window"], c["trigger_percent"])]
    s = c.get("summary")
    parts.append(Text.assemble(("  Messages sent to the model: ", DIM), (f"{c['messages_in_context']}", "bold"), (f" of {c['messages_total']}", DIM),
                               (f"  ·  the first {s['covers']} are replaced by a summary" if s else "", DIM)))
    parts.append(Text(f"  Older messages are summarised automatically at {c['trigger_percent']}% of the window; /compact does it now" + ("; /summary shows it." if s else "."), style=DIM))
    m = c["memory"]
    parts.append(Text(f"  Project memory: {m['bytes']:,} bytes are added to every request (/memory to see, /remember <note> to add)." if m["exists"]
                      else "  No project memory yet: /remember <note> creates it.", style=DIM))
    return Group(*parts)


def summary_view(text: str, covers: int) -> Panel:
    return Panel(Text(text.strip()), title=Text(f" Summary of the first {covers} messages ", style=f"bold {PURPLE}"), title_align="left", border_style=PURPLE, padding=(1, 2))


def memory_view(m: dict) -> Panel | Text:
    if not m["text"].strip():
        return Text("  No project memory yet. Add a note with /remember <note>, or edit " + m["path"], style=DIM)
    return Panel(Text(m["text"].strip()), title=Text(" Project memory ", style=f"bold {PURPLE}"), subtitle=Text(f" {m['path']} · {m['bytes']:,} of {m['max_bytes']:,} bytes ", style=DIM), border_style=PURPLE, padding=(1, 2))


def compact_result(r: dict) -> Group:
    if not r.get("ok"):
        return Group(Text(f"  {r.get('reason', 'Nothing to compact.')}", style=WARN))
    return Group(Text.assemble(("  ✓ ", OK), "summarised the first ", (str(r["covers"]), "bold"), f" messages (about {r['tokens_before']:,} → {r['tokens_after']:,} tokens). The full transcript is kept."),
                 summary_view(r["summary"], r["covers"]))


def error(text: str) -> Text:
    return Text(f"  {text}", style=ERR)
