"""Model-list helpers for the CLI (families mirror the desktop Models window)."""
from __future__ import annotations

import re

from .fmt import fmt_bytes
from .chooser import Option

GROUPS = [("qwen", "Qwen"), ("deepseek", "DeepSeek"), ("llama", "Llama"), ("gemma", "Gemma"), ("mistral", "Mistral"), ("phi", "Phi"),
          ("openai", "OpenAI"), ("granite", "IBM Granite"), ("embeddings", "Embeddings"), ("other", "Other")]
LABEL = dict(GROUPS)
FIT_HINT = {"best": "best for ZEUS", "good": "good", "limited": "limited", "no": "cannot chat"}


def group_of(name: str) -> str:
    n = name.lower()
    if re.search(r"embed|(^|[/-])bge-|minilm", n):
        return "embeddings"
    if "deepseek" in n:
        return "deepseek"
    if re.search(r"qwen|qwq", n):
        return "qwen"
    if re.search(r"^tinyllama|llava", n):
        return "other"
    if "llama" in n:
        return "llama"
    if "gemma" in n:
        return "gemma"
    if re.search(r"mistral|mixtral|devstral|codestral", n):
        return "mistral"
    if re.search(r"(^|/)phi\d?", n):
        return "phi"
    if "gpt-oss" in n:
        return "openai"
    if "granite" in n:
        return "granite"
    return "other"


def is_embedding(name: str) -> bool:
    return group_of(name) == "embeddings"


def norm(n: str) -> str:
    return n if ":" in n else f"{n}:latest"


def installed_options(models: list[dict], loaded: list[dict] | None = None, *, with_auto: bool = True, chat_only: bool = True) -> list[Option]:
    """Options for choosing among installed models, grouped by family."""
    ld = {norm(x["name"]) for x in loaded or []}
    out: list[Option] = []
    if with_auto:
        out.append(Option("Auto", "auto", "ZEUS picks a coding or thinking model for each prompt"))
    buckets: dict[str, list[dict]] = {}
    for m in sorted(models, key=lambda x: x["name"].lower()):
        buckets.setdefault(group_of(m["name"]), []).append(m)
    for gid, label in GROUPS:
        items = buckets.get(gid)
        if not items:
            continue
        out.append(Option("", None, group=f"{label} ({len(items)})"))
        for m in items:
            bits = [fmt_bytes(m.get("size")), m.get("parameter_size") or "", m.get("quantization") or ""]
            if norm(m["name"]) in ld:
                bits.append("● loaded")
            emb = is_embedding(m["name"])
            if emb and chat_only:
                bits.append("embedding model, cannot chat")
            out.append(Option(m["name"], m["name"], " · ".join(b for b in bits if b), disabled=emb and chat_only))
    return out


def catalog_options(models: list[dict], installed: set[str]) -> list[Option]:
    """Catalog entries grouped by family; value is the catalog model dict."""
    out: list[Option] = []
    buckets: dict[str, list[dict]] = {}
    for m in models:
        buckets.setdefault(group_of(m["name"]), []).append(m)
    rank = {"best": 0, "good": 1, "limited": 2, "no": 3}
    for gid, label in GROUPS:
        items = sorted(buckets.get(gid, []), key=lambda m: (rank.get(m["fit"], 9), m["title"].lower()))
        if not items:
            continue
        out.append(Option("", None, group=f"{label} ({len(items)})"))
        for m in items:
            have = any(norm(t["tag"]) in installed or (t.get("latest") and f"{m['name']}:latest" in installed) for t in m["tags"])
            sizes = "/".join(t["tag"].split(":")[-1] for t in m["tags"][:5])
            out.append(Option(m["title"], m, f"{FIT_HINT.get(m['fit'], '')} · {sizes}" + (" · ✓ installed" if have else "")))
    return out
