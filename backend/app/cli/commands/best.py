"""`/best`: which model is best for an activity (Arena human votes), and whether it runs on this computer. Content comes from the backend (`/api/arena`)."""
from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..ui.chooser import Option
from ..ui.theme import DIM, ERR, OK, PURPLE, WARN

ALIASES = {"code": "code", "coding": "code", "webdev": "code", "agentcode": "agentcode", "agentic": "agentcode", "agent-coding": "agentcode", "agents": "agent", "agent": "agent",
           "chat": "chat", "reasoning": "chat", "vision": "vision", "images": "vision", "documents": "document", "document": "document", "docs": "document"}
COLOR = {"great": OK, "ok": PURPLE, "slow": WARN, "no": ERR}


def activity_of(text: str) -> str | None:
    return ALIASES.get((text or "").strip().lower())


def options(ov: dict) -> list[Option]:
    out = []
    for a in ov["activities"]:
        b = a.get("best_here")
        out.append(Option(a["label"], a["id"], f"best here: {b['name']}" if b else "nothing ranked fits this computer"))
    return out


def _fit(m: dict) -> Text:
    f = m.get("fit")
    if not f:
        return Text("size unknown", style=DIM)
    v = f["verdict"]
    tps = f" ~{f['tps']:g} tok/s" if f.get("tps") else ""
    return Text(v["label"] + tps, style=COLOR.get(v["level"], ""))


def render(ov: dict, activity: str, limit: int = 12, only_fit: bool = True) -> Group:
    """Hardware line, the best model for this machine, then the ranking with a verdict per model."""
    a = next((x for x in ov["activities"] if x["id"] == activity), ov["activities"][0])
    hw = ov["hardware"]
    gpu = f"{hw['gpu']} · {hw['vram_gb']} GB" if hw.get("gpu") else "no GPU detected (models run from RAM)"
    parts: list = [Text.assemble(("  ", ""), (a["label"], f"bold {PURPLE}"), (f"  {a['blurb']}", DIM)),
                   Text(f"  Your computer: {gpu} · {hw['ram_gb']} GB RAM · context {ov['ctx']:,} tokens", style=DIM)]
    b = a.get("best_here")
    if b:
        pull = f"  ·  ollama pull {b['ollama']['tag']}" if b.get("ollama") and b["ollama"].get("local") else ""
        parts.append(Text.assemble(("\n  ⚡ Best for you: ", f"bold {PURPLE}"), (b["name"], "bold"), (f"  score {b['score']}, rank #{b['rank']} of {a['total']}", DIM), (pull, "bold")))
        q = a.get("best_quality")
        if q:
            parts.append(Text.assemble(("  Highest quality that runs at all: ", DIM), (q["name"], "bold"), (f"  score {q['score']}, rank #{q['rank']}", DIM), ("  (slow: ", DIM), (f"~{q['fit']['tps']:g} tok/s" if q["fit"].get("tps") else "speed unknown", WARN), (")", DIM)))
    else:
        parts.append(Text("\n  No ranked open model for this activity fits this computer.", style=WARN))
    t = Table(show_header=True, header_style=f"bold {PURPLE}", box=None, padding=(0, 2), expand=False)
    for col, kw in (("Rank", {"justify": "right"}), ("Model", {"overflow": "fold", "max_width": 34}), ("Score", {"justify": "right"}), ("Size", {"justify": "right"}), ("On your computer", {}), ("Ollama", {"overflow": "fold"})):
        t.add_column(col, **kw)
    shown = [m for m in a["models"] if not only_fit or (m.get("fit") and m["fit"]["where"] != "no")][:limit]
    for m in shown:
        size = f"{m['params_b']:g}B" + (f"/{m['active_b']:g}B" if m.get("active_b") and m.get("params_b") and m["active_b"] < m["params_b"] * 0.6 else "") if m.get("params_b") else "–"
        o = m.get("ollama")
        t.add_row(f"#{m['rank']}", m["name"], str(m["score"]), size, _fit(m), (o["tag"] if o and o.get("local") else "–"))
    parts.append(t if shown else Text("  Nothing to show.", style=DIM))
    parts.append(Text(f"\n  {'Showing models that run here. ' if only_fit else ''}/best all {a['id']} shows everything. Data as of {ov['updated']}. {ov['license']}.\n"
                      "  Scores are blind human votes, not a test suite. Speeds are estimates for 4-bit files (about ±30%).", style=DIM))
    return Group(*parts)
