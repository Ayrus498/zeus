"""Engine choice in the terminal: options for the chooser and the "Help me choose" screen (content comes from the backend)."""
from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..ui.chooser import Option
from ..ui.fmt import fmt_bytes
from ..ui.theme import DIM, PURPLE

LABEL = {"ollama": "Ollama", "llamacpp": "llama.cpp"}
ALIASES = {"ollama": "ollama", "llamacpp": "llamacpp", "llama.cpp": "llamacpp", "llama": "llamacpp", "llama-cpp": "llamacpp", "llama_cpp": "llamacpp"}


def engine_of(text: str) -> str | None:
    return ALIASES.get((text or "").strip().lower())


def status_hint(e: dict) -> str:
    if e["id"] == "llamacpp":
        if not e.get("available"):
            return "llama-server not found · choose Help me choose for install steps"
        srv = e.get("server") or {}
        loaded = f" · loaded: {srv['model']}" if srv.get("running") else ""
        return f"ready · {e.get('models', 0)} GGUF model{'s' if e.get('models') != 1 else ''}{loaded}"
    if not e.get("available"):
        return "not running · start it with `ollama serve`"
    return f"running · {e.get('models', 0)} model{'s' if e.get('models') != 1 else ''} installed"


def options(engines: dict[str, dict], recommended: str | None) -> list[Option]:
    out = []
    for key in ("ollama", "llamacpp"):
        e = engines.get(key)
        if e:
            out.append(Option(LABEL[key] + ("  (recommended)" if key == recommended else ""), key, status_hint(e)))
    out.append(Option("Help me choose…", "help", "compare the two engines and get a recommendation"))
    return out


def help_screen(info: dict, rec: dict, current: str, engines: dict[str, dict]) -> Group:
    """The comparison table, a recommendation for this machine and the install steps for llama.cpp."""
    t = Table(show_header=True, header_style=f"bold {PURPLE}", box=None, padding=(0, 2), expand=False)
    t.add_column("", style=DIM, no_wrap=True)
    t.add_column("Ollama" + (" ✓" if current == "ollama" else ""), overflow="fold", max_width=44)
    t.add_column("llama.cpp" + (" ✓" if current == "llamacpp" else ""), overflow="fold", max_width=44)
    for r in info.get("rows", []):
        t.add_row(r["topic"], r["ollama"], r["llamacpp"])
    parts = [Panel(t, title=Text(" Ollama or llama.cpp? ", style=f"bold {PURPLE}"), title_align="left", border_style=PURPLE, padding=(1, 2))]
    when = info.get("when", {})
    w = Table.grid(padding=(0, 3))
    w.add_column(style="bold")
    w.add_column(overflow="fold")
    w.add_row("Choose Ollama if", "  ·  ".join(when.get("ollama", [])))
    w.add_row("Choose llama.cpp if", "  ·  ".join(when.get("llamacpp", [])))
    parts.append(w)
    parts.append(Text.assemble(("\n  ⚡ For this machine: ", f"bold {PURPLE}"), (LABEL.get(rec.get("engine"), "?"), "bold"), (" — " + rec.get("reason", ""), DIM)))
    ll = engines.get("llamacpp") or {}
    if not ll.get("available"):
        steps = Text("\n  To use llama.cpp:\n", style="bold")
        for i, s in enumerate(info.get("install", []), 1):
            steps.append(f"   {i}. {s}\n", style=DIM)
        parts.append(steps)
    elif ll.get("devices"):
        d = ll["devices"][0]
        parts.append(Text(f"\n  llama.cpp sees: {d['name']} ({fmt_bytes(d['memory_mib'] * 2**20)})", style=DIM))
    return Group(*parts)
