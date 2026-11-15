"""Auto model routing: pick a coding or a thinking model per prompt from the models that are actually installed.

How it decides (deliberately simple and explainable - every decision comes with a human-readable reason):
  1. classify(prompt): rule-based intent -> "code" | "think" | "keep" (short follow-ups keep the previous model)
  2. pick(role, candidates): score installed chat-capable models using what ZEUS knows about each family
     (providers/catalog.py), Ollama's own capability flags (/api/show), whether it fits this machine's GPU/RAM,
     and whether it is already loaded (a model swap costs seconds to a minute).
The choice is made once per prompt and kept for every step of that agent run (no mid-run swapping).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..providers import catalog

AUTO = "auto"

# ------------------------------------------------------------------ classification
_CODE_VERBS = r"create|write|implement|add|build|make|generate|fix|refactor|rename|delete|remove|install|run|execute|test|debug|patch|update|edit|modify|scaffold|port|convert|compile|lint|format|deploy|commit|migrate"
_CODE_NOUNS = r"function|class|method|module|script|file|endpoint|api|cli|unit test|tests?|bug|error|exception|traceback|stack trace|compile|build|package|dependency|requirements|dockerfile|makefile|repo|project|schema|query|regex"
_FILE_HINT = re.compile(r"(?:^|[\s`'\"(])[\w./-]+\.(?:py|js|jsx|ts|tsx|c|cc|cpp|h|hpp|java|go|rs|rb|php|sh|sql|json|ya?ml|toml|md|txt|html|css|cs|kt|swift)\b", re.I)
_THINK = re.compile(
    r"\b(why|explain|how (?:does|do|would|should|can|come)|what(?:'s| is| are) the (?:difference|best|trade-?offs?|reason|cause)|"
    r"design|architect(?:ure)?|plan|strategy|compare|comparison|trade-?offs?|pros and cons|analy[sz]e|analysis|reason(?:ing)? about|"
    r"think (?:through|about|step)|step by step|prove|proof|derive|root cause|should i|which (?:approach|option|one)|brainstorm|"
    r"evaluate|assess|review the (?:design|approach)|complexity|big-?o|algorithm(?:ic)? (?:choice|design)|decide|recommend|"
    r"edge cases|race condition|deadlock|security (?:implications|review|risks?))\b", re.I)
_ACTION = re.compile(rf"\b(?:{_CODE_VERBS})\b", re.I)
_CODE_NOUN = re.compile(rf"\b(?:{_CODE_NOUNS})\b", re.I)
_FOLLOWUP = re.compile(r"^\s*(?:yes|yep|yeah|ok(?:ay)?|sure|go ahead|continue|proceed|do it|thanks?|thank you|retry|again|next|looks good|lgtm|keep going|approved?)\b[\s.!]*$", re.I)


@dataclass
class Intent:
    role: str                       # "code" | "think" | "keep"
    reasons: list[str] = field(default_factory=list)
    forced: bool = False

    @property
    def why(self) -> str:
        return "; ".join(self.reasons) or "default"


def split_override(text: str) -> tuple[str | None, str]:
    """'/think why is this slow' -> ('think', 'why is this slow'). Also '/code'."""
    m = re.match(r"^\s*/(think|code)\b[:\s]*", text, re.I)
    return (m.group(1).lower(), text[m.end():].strip() or text) if m else (None, text)


def classify(prompt: str) -> Intent:
    forced, text = split_override(prompt)
    if forced:
        return Intent(forced, [f"you asked for /{forced}"], True)
    t = text.strip()
    if len(t.split()) <= 4 and _FOLLOWUP.match(t):
        return Intent("keep", ["short follow-up: staying with the current model"])
    code, think, why_c, why_t = 0, 0, [], []
    if "```" in t:
        code += 2; why_c.append("contains a code block")
    if _FILE_HINT.search(t):
        code += 2; why_c.append("mentions a file")
    acts = _ACTION.findall(t)
    if acts:
        code += 2; why_c.append(f"action request ({acts[0].lower()})")
    if _CODE_NOUN.search(t):
        code += 1; why_c.append("about code artifacts")
    th = _THINK.findall(t)
    if th:
        think += 2 + (1 if len(th) > 1 else 0); why_t.append(f"asks for analysis/design ({th[0] if isinstance(th[0], str) else th[0][0]})".lower())
    if t.endswith("?") and not acts and not _FILE_HINT.search(t):
        think += 1; why_t.append("open question")
    if len(t.split()) > 60 and not acts:
        think += 1; why_t.append("long open-ended request")
    if think > code:
        return Intent("think", why_t)
    if code > think:
        return Intent("code", why_c)
    return Intent("keep" if code == 0 else "code", ["no strong signal" if code == 0 else "mixed signals: favouring the coding model"])


# ------------------------------------------------------------------ model profiles
@dataclass
class Profile:
    name: str
    family: str | None
    title: str
    purposes: set[str]
    fit: str                          # best|good|limited|no  (for the ZEUS agent)
    caps: set[str]                    # tools, thinking, vision, embedding
    size: int                         # bytes on disk
    loaded: bool = False
    known: bool = True                # False: not in the catalog, inferred from the name
    params_b: float | None = None

    @property
    def chat(self) -> bool:
        return self.fit != "no" and "embedding" not in self.caps

    @property
    def tools(self) -> bool:
        return "tools" in self.caps

    def public(self) -> dict:
        return {"name": self.name, "family": self.family, "title": self.title, "purposes": sorted(self.purposes), "fit": self.fit,
                "capabilities": sorted(self.caps), "size": self.size, "loaded": self.loaded, "known": self.known}


def family_of(name: str) -> dict | None:
    """Longest catalog family that prefixes the model's base name ('qwen3-coder-16k:latest' -> qwen3-coder)."""
    base = name.split(":")[0].split("/")[-1].lower()
    best = None
    for m in catalog.CATALOG:
        f = m["name"]
        if base == f or (base.startswith(f) and base[len(f)] in "-_"):
            if best is None or len(f) > len(best["name"]):
                best = m
    return best


def _params_b(name: str) -> float | None:
    m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)b\b", name.lower())
    return float(m.group(1)) if m else None


def build_profile(name: str, size: int = 0, loaded: bool = False, api_caps: list[str] | None = None) -> Profile:
    fam = family_of(name)
    low = name.lower()
    rest = low.split(":")[0].split("/")[-1]
    caps: set[str] = set()
    if api_caps:
        caps = {c for c in api_caps if c in ("tools", "thinking", "vision", "embedding")}
    if fam:
        purposes, fit = set(fam["purposes"]), fam["fit"]
        if not api_caps:
            caps = set(fam["capabilities"])
        title, known = fam["title"], True
        suffix = rest[len(fam["name"]):]
        if re.search(r"(?:^|[-_])(vl|vision)(?:$|[-_])", suffix):       # e.g. qwen3-vl: same family prefix, different job
            purposes, fit, caps = {"vision"}, "limited", caps | {"vision"}
        if re.search(r"(?:^|[-_])embed", suffix):
            purposes, fit, caps = {"embedding"}, "no", caps | {"embedding"}
    else:
        known, title, purposes, fit = False, name, {"chat"}, "good"
        if re.search(r"coder|code(?:llama|r)?\b|devstral|starcoder", low):
            purposes, fit = {"coding"}, "good"
        if re.search(r"(?:^|[-_/])r1|reason|think|qwq|o1", low):
            purposes = {"reasoning"}
        if re.search(r"embed|bge|minilm", low) or "embedding" in caps:
            purposes, fit = {"embedding"}, "no"
        if re.search(r"(?:^|[-_/])(vl|vision|llava)", low):
            purposes, fit = {"vision"}, "limited"
    if "embedding" in caps:
        fit = "no"
    return Profile(name, fam["name"] if fam else None, title, purposes, fit, caps, size, loaded, known, _params_b(name))


# ------------------------------------------------------------------ choosing
def _placement(size_bytes: int, gpu_total: int, ram: int) -> str:
    need = size_bytes * 1.1 + 1e9
    if gpu_total and need <= gpu_total * 0.95:
        return "gpu"
    if need <= gpu_total * 0.95 + ram * 0.85:
        return "split" if gpu_total else "cpu"
    return "toolarge"


def score(p: Profile, role: str, gpu_total: int, ram: int) -> tuple[float, list[str]]:
    """Higher is better. Returns (score, human reasons)."""
    if not p.chat:
        return -1e9, ["not a chat model"]
    s, why = 0.0, []
    s += {"best": 4, "good": 2, "limited": 0.5}.get(p.fit, 0)
    if role == "code":
        if "coding" in p.purposes:
            s += 3; why.append("coding model")
        if "agent" in p.purposes:
            s += 1
        if p.tools:
            s += 3; why.append("native tool calling")
        else:
            s -= 4; why.append("no tool calling")
        if p.purposes == {"reasoning"}:
            s -= 2
        if "vision" in p.purposes and "coding" not in p.purposes:
            s -= 3
    else:  # think
        if "reasoning" in p.purposes or "thinking" in p.caps:
            s += 4; why.append("reasoning model")
        if "thinking" in p.caps:
            s += 1.5
        if p.tools:
            s += 1.5; why.append("keeps tool access")
        if "coding" in p.purposes and "reasoning" not in p.purposes and "thinking" not in p.caps:
            s -= 2
        if "vision" in p.purposes and "reasoning" not in p.purposes:
            s -= 3
    # Capability prior: bigger is usually better; tiny models are poor agents and poor reasoners, however well they "fit".
    eff = p.params_b or (p.size / 1e9 / 0.6 if p.size else None)       # ~0.6 GB per billion params at Q4
    if eff:
        s += min(eff, 32) / 8
        if eff < 1.5:
            s -= 7; why.append("too small for this job")
        elif eff < 3.5:
            s -= 3.5; why.append("small model")
    place = _placement(p.size, gpu_total, ram) if p.size else "gpu"
    # Speed matters, especially for reasoning (very long outputs): a model that spills to CPU is slow.
    s += {"gpu": 2, "split": -2 if role == "code" else -3, "cpu": -2, "toolarge": -8}[place]
    if place == "gpu":
        why.append("fits GPU")
    elif place == "split":
        why.append("partly on CPU")
    elif place == "toolarge":
        why.append("too large for this machine")
    if p.loaded:
        s += 1.0; why.append("already loaded")
    return s, why


@dataclass
class Choice:
    model: str
    role: str
    tools: bool
    reason: str


def pick(role: str, profiles: list[Profile], gpu_total: int, ram: int, pin: str | None = None) -> tuple[Profile | None, list[str]]:
    by_name = {p.name: p for p in profiles}
    if pin and pin in by_name and by_name[pin].chat:
        return by_name[pin], ["pinned by you"]
    best, best_s, best_why = None, -1e8, []
    for p in profiles:
        s, why = score(p, role, gpu_total, ram)
        if s > best_s:
            best, best_s, best_why = p, s, why
    return best, best_why


def decide(prompt: str, profiles: list[Profile], gpu_total: int, ram: int, last_model: str | None,
           pins: dict[str, str | None] | None = None) -> Choice | None:
    """Full decision for one prompt. None if no chat-capable model is installed."""
    pins = pins or {}
    intent = classify(prompt)
    by_name = {p.name: p for p in profiles}
    role = intent.role
    if role == "keep":
        if last_model in by_name and by_name[last_model].chat:
            p = by_name[last_model]
            return Choice(p.name, "keep", p.tools, f"{intent.why} ({p.name})")
        role = "code"
    p, why = pick(role, profiles, gpu_total, ram, pins.get(role))
    if not p:
        return None
    # Hysteresis: don't swap to a model that is only marginally better than the one already in use for this role.
    if last_model and last_model != p.name and last_model in by_name and not pins.get(role) and not intent.forced:
        cur = by_name[last_model]
        cs, _ = score(cur, role, gpu_total, ram)
        ns, _ = score(p, role, gpu_total, ram)
        if cur.chat and ns - cs < 1.5:
            p, why = cur, ["close enough: avoiding a model reload"]
    kind = "coding" if role == "code" else "reasoning"
    reason = f"{kind} task ({intent.why}) -> {p.title if p.known else p.name}" + (f" [{', '.join(why)}]" if why else "")
    # A reasoning-only model without tool calling can still answer questions; it just can't touch files this turn.
    return Choice(p.name, role, p.tools, reason)


# ------------------------------------------------------------------ live state + plan
SUGGEST = {"code": ["qwen3-coder:30b", "qwen2.5-coder:14b", "qwen2.5-coder:7b", "qwen2.5-coder:3b"],
           "think": ["qwen3:14b", "qwen3:8b", "gpt-oss:20b", "qwen3:4b"]}


class AutoRouter:
    def __init__(self, db, provider_getter):
        self.db, self.provider = db, provider_getter
        self._caps: dict[tuple[str, str], list[str] | None] = {}

    def pins(self) -> dict[str, str | None]:
        return {"code": self.db.get_setting("auto_code_model") or None, "think": self.db.get_setting("auto_think_model") or None}

    async def _caps_for(self, prov, names: list[str]) -> dict[str, list[str] | None]:
        import asyncio
        from ..providers.ollama import OllamaError
        sem = asyncio.Semaphore(6)

        async def one(n):
            key = (prov.base_url, n)
            if key in self._caps:
                return n, self._caps[key]
            async with sem:
                try:
                    caps = (await prov.show(n)).get("capabilities") or None
                except (OllamaError, Exception):
                    caps = None
            self._caps[key] = caps
            return n, caps
        return dict(await asyncio.gather(*(one(n) for n in names)))

    async def gather(self) -> tuple[list[Profile], int, int]:
        import asyncio
        from ..providers.ollama import system_memory
        prov = self.provider()
        installed = await prov.list_models()
        loaded = {m.get("name") for m in await prov.running()}
        caps = await self._caps_for(prov, [m["name"] for m in installed])
        mem = await asyncio.get_running_loop().run_in_executor(None, system_memory)
        gpu = (mem["gpu"] or {}).get("total", 0)
        ram = mem["ram_available"] or 0
        profiles = [build_profile(m["name"], m.get("size", 0), m["name"] in loaded, caps.get(m["name"])) for m in installed]
        return profiles, gpu, ram

    async def route(self, prompt: str, last_model: str | None) -> Choice | None:
        profiles, gpu, ram = await self.gather()
        return decide(prompt, profiles, gpu, ram, last_model, self.pins())

    async def plan(self) -> dict:
        profiles, gpu, ram = await self.gather()
        pins = self.pins()
        chat = [p for p in profiles if p.chat]
        out: dict = {"chat_models": len(chat), "candidates": [p.name for p in chat], "pins": pins, "warnings": [], "suggestions": [], "roles": {}}
        picks: dict[str, Profile | None] = {}
        for role in ("code", "think"):
            p, why = pick(role, profiles, gpu, ram, pins.get(role))
            picks[role] = p
            out["roles"][role] = {**(p.public() if p else {}), "why": why, "tools": bool(p and p.tools)} if p else None
        c, t = picks["code"], picks["think"]
        if not chat:
            out["warnings"].append("No chat-capable model is installed. Download one in the model guide below.")
        elif len(chat) == 1:
            out["warnings"].append("Only one chat model is installed, so Auto always uses it. Add a second (coding or reasoning) model to get switching.")
        if c and t and c.name != t.name and gpu and (c.size + t.size) * 1.1 + 2e9 > gpu:
            out["warnings"].append("These two models do not fit in GPU memory together, so each switch reloads weights (typically 5-60 seconds). ZEUS avoids switching when the difference is small.")
        if t and not t.tools:
            out["warnings"].append(f"{t.name} is reasoning-only (no tool calling): in thinking turns it answers questions but cannot read or change files. Use /code to force the coding model.")
        have = {p.name for p in profiles}
        from ..providers.catalog import CATALOG
        sizes = {tg["tag"]: tg["size_gb"] for m in CATALOG for tg in m["tags"]}
        weak = {"code": not (c and "coding" in c.purposes), "think": not (t and ("reasoning" in t.purposes or "thinking" in t.caps) and t.tools)}
        for role, is_weak in weak.items():
            if not is_weak:
                continue
            for tag in SUGGEST[role]:
                if tag in have or f"{tag.split(':')[0]}:latest" in have and tag.endswith(":latest"):
                    continue
                size = int(sizes.get(tag, 0) * 1e9)
                if _placement(size, gpu, ram) in ("gpu", "split"):
                    out["suggestions"].append({"role": role, "tag": tag, "size_gb": sizes.get(tag),
                                               "why": "adds a coding-specialised model with tool calling" if role == "code" else "adds a reasoning model that keeps tool access (can still edit files while thinking)"})
                    break
        return out
