"""Terminal helpers for llama.cpp models: searching Hugging Face, choosing a file by how it runs here, and uninstalling. Content comes from the backend."""
from __future__ import annotations

import re

from ..ui.chooser import Option
from ..ui.fmt import fmt_bytes

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
SOURCE_LABEL = {"managed": "downloaded by Zeus-Agent", "hf_cache": "Hugging Face cache", "llama_cache": "llama.cpp cache", "folder": "your folder", "ollama": "from Ollama (shared)"}


def short(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1_000_000 else f"{round(n / 1000)}K" if n >= 1000 else str(n)


def looks_like_repo(text: str) -> bool:
    return bool(REPO_RE.match((text or "").strip()))


def search_options(results: list[dict]) -> list[Option]:
    return [Option(r["repo"], r["repo"], f"↓ {short(r['downloads'])}  ♥ {short(r['likes'])}") for r in results]


def file_options(info: dict) -> list[Option]:
    """One option per single-file model: size, quantization and how it would run on this machine; the suggested one is marked."""
    out = []
    for f in info["files"]:
        if f["split"]:
            continue
        fit = f["fit"]["verdict"]
        hint = " · ".join(x for x in (fmt_bytes(f["size"]), f.get("quant"), fit["label"] + (f" ~{f['fit']['tps']:g} tok/s" if f["fit"].get("tps") else ""),
                                      "installed" if f.get("installed") else "", "suggested" if f["file"] == info.get("suggested") else "") if x)
        out.append(Option(f["file"], f["file"], hint))
    return out


def removable_options(models: list[dict]) -> list[Option]:
    """The models Zeus-Agent may delete, with where each is and how much it frees."""
    out = []
    for m in models:
        r = m.get("removable")
        if r:
            out.append(Option(m["name"], m["name"], f"{SOURCE_LABEL.get(m['source'], m['source'])} · frees {fmt_bytes(r['bytes'] or m['size'])}"))
    return out


def summary(models: list[dict]) -> str:
    n_ollama = sum(1 for m in models if m.get("source") == "ollama")
    return f"{len(models)} model{'' if len(models) == 1 else 's'}" + (f" ({n_ollama} shared from Ollama)" if n_ollama else "")
