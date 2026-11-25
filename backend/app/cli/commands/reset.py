"""Terminal views for `/reset`: what would be removed, and the confirmation parsing. Content comes from the backend (`/api/reset`)."""
from __future__ import annotations

from rich.console import Group
from rich.text import Text

from ..ui.fmt import fmt_bytes
from ..ui.theme import DIM, ERR, OK, PURPLE, WARN


def plan_view(plan: dict) -> Group:
    parts: list = [Text.assemble(("  Reset Zeus-Agent  ", f"bold {PURPLE}"), ("this cannot be undone", f"bold {ERR}"))]
    for it in plan["items"]:
        mark = ("✓", OK) if it["default"] else ("·", DIM)
        parts.append(Text.assemble(("  ", ""), (mark[0] + " ", mark[1]), (it["label"], "bold"), (f"  {it['summary']}" + (f" · {fmt_bytes(it['bytes'])}" if it["bytes"] else ""), DIM)))
        parts.append(Text(f"      {it['detail']}", style=DIM))
    parts.append(Text("\n  Never touched: " + "; ".join(plan["keeps"]), style=WARN))
    return Group(*parts)


def parse_answer(answer: str, plan: dict) -> tuple[list[str] | None, bool]:
    """(`ids` to remove, models included) or (None, False) when the answer is not exactly the confirmation word (optionally followed by `models`)."""
    words = answer.split()
    if not words or words[0] != plan["confirm"] or any(w.lower() != "models" for w in words[1:]):
        return None, False
    models = len(words) > 1
    ids = [it["id"] for it in plan["items"] if it["default"] or (models and it["id"] == "models")]
    return ids, models


def report_view(report: dict) -> Group:
    rows = [Text.assemble(("  ✓ " if r["ok"] else "  ✗ ", OK if r["ok"] else ERR), (r["id"], "bold"), (f"  {r['files']} files · {fmt_bytes(r['bytes'])}", DIM)) for r in report["items"]]
    rows += [Text(f"    {e}", style=ERR) for r in report["items"] for e in r["errors"]]
    return Group(*rows, Text(f"  Zeus-Agent was reset. Freed {fmt_bytes(report['removed_bytes'])}.", style=f"bold {OK}"))
