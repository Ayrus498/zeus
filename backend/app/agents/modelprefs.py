"""Model-aware communication: how to talk to *this* model so it performs well.

Sources of truth, in order:
  1. Ollama's own metadata (/api/show): capability flags (tools / thinking / vision) and the `parameters` block, which holds the
     model creators' recommended sampling settings (temperature, top_p, top_k, repeat/presence penalty).
  2. Family rules for things the metadata cannot say (e.g. DeepSeek-R1 should get its instructions in the user turn, gemma has no
     system role, hybrid-thinking Qwen3 models are faster for agent steps with thinking off).
  3. Safe defaults.
Roles: "agent" (tool-calling steps), "plan" (extract objective/criteria), "verify" (strict judge), "think" (deep analysis).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..modelconnectors.ollama.catalog import CATALOG
from ..toolchain.engine import TOOL_SCHEMAS

CTX_PRESETS = [8192, 16384, 32768, 65536, 131072]
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


def parse_parameters(text: str) -> dict:
    """Modelfile-style 'temperature  0.7' lines -> {'temperature': 0.7}; repeated keys (stop) are ignored."""
    out: dict = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or parts[0] == "stop":
            continue
        try:
            out[parts[0]] = float(parts[1]) if "." in parts[1] else int(parts[1])
        except ValueError:
            pass
    return out


def _family(name: str) -> str:
    base = name.split(":")[0].split("/")[-1].lower()
    best = ""
    for m in CATALOG:
        f = m["name"]
        if (base == f or (base.startswith(f) and base[len(f):len(f) + 1] in "-_.")) and len(f) > len(best):
            best = f
    if not best:
        for key in ("deepseek-r1", "r1", "qwq", "gemma", "llama", "mistral", "phi", "qwen"):
            if key in base:
                return key
    return best or base


@dataclass
class Prefs:
    model: str
    family: str
    options: dict = field(default_factory=dict)
    think: object = None                 # None = leave to the model; True/False/"low"|"medium"|"high"
    fold_system: bool = False            # put the system prompt into the first user turn
    native_tools: bool = True
    thinking_capable: bool = False
    context_length: int | None = None
    notes: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return {"model": self.model, "family": self.family, "temperature": self.options.get("temperature"),
                "top_p": self.options.get("top_p"), "top_k": self.options.get("top_k"), "think": self.think,
                "tools": "native" if self.native_tools else "text protocol", "system": "folded into user turn" if self.fold_system else "system role",
                "context_length": self.context_length, "notes": self.notes}


# sampling keys Ollama accepts that we forward from the model's own recommendations
_FORWARD = ("temperature", "top_p", "top_k", "repeat_penalty", "presence_penalty", "min_p")


def build_prefs(name: str, meta: dict | None, role: str = "agent") -> Prefs:
    meta = meta or {}
    caps = set(meta.get("capabilities") or [])
    fam = _family(name)
    rec = parse_parameters(meta.get("parameters", ""))
    p = Prefs(model=name, family=fam, native_tools=("tools" in caps) if caps else True,
              thinking_capable="thinking" in caps, context_length=meta.get("context_length"))
    opts = {k: v for k, v in rec.items() if k in _FORWARD}
    if rec:
        p.notes.append("using the creators' recommended sampling settings")
    # --- role adjustments: agent steps and judging want steadier output than chat defaults
    if role == "verify":
        opts["temperature"] = min(opts.get("temperature", 0.2), 0.2)
        opts.pop("top_k", None)
    elif role in ("agent", "plan"):
        t = opts.get("temperature")
        opts["temperature"] = 0.2 if t is None else min(t, 0.7)
        if t is not None and t > 0.7:
            p.notes.append(f"temperature capped at 0.7 for tool use (model suggests {t:g})")
    # --- reasoning-only families (R1 / QwQ): low temperatures make them repeat themselves, whatever the role
    if fam in ("deepseek-r1", "r1", "qwq"):
        opts["temperature"] = max(opts.get("temperature", 0.6), 0.5)
        opts.setdefault("top_p", 0.95)
        p.notes.append("temperature kept >= 0.5 (reasoning models loop at low temperature)")
    # --- thinking control
    if p.thinking_capable:
        reasoning_only = fam in ("deepseek-r1", "r1", "qwq")            # thinking cannot be switched off sensibly
        if fam == "gpt-oss":
            p.think = {"agent": "low", "plan": "low", "verify": "high", "think": "high"}.get(role, "medium")
        elif not reasoning_only:
            p.think = role in ("verify", "think")
            if role in ("agent", "plan"):
                p.notes.append("thinking off for faster tool steps")
    # --- system prompt handling
    if fam in ("deepseek-r1", "r1"):
        p.fold_system = True
        p.notes.append("instructions go in the user turn (recommended by DeepSeek)")
    if "gemma" in fam:
        p.fold_system = True
        p.notes.append("no system role: instructions folded into the user turn")
    if not p.native_tools:
        p.notes.append("no native tool calling: using a text tool protocol")
    p.options = opts
    return p


def strip_think(text: str) -> str:
    return _THINK_BLOCK.sub("", text or "")


def text_tool_protocol() -> str:
    """Taught to models without native tool calling, so the fallback parser sees a format they were actually told about."""
    lines = ["You have these tools. To use one, reply with ONLY a single JSON object (no prose around it, no code fence):",
             '{"name": "<tool_name>", "arguments": {...}}', "", "Tools:"]
    for t in TOOL_SCHEMAS:
        props = ", ".join(f"{k}{'' if k in t['parameters'].get('required', []) else '?'}" for k in t["parameters"]["properties"])
        lines.append(f"- {t['name']}({props}): {t['description']}")
    lines.append("\nCall one tool per reply. After the result arrives, continue. When the task is done, reply with a short summary and no JSON.")
    return "\n".join(lines)


def prepare_messages(msgs: list[dict], p: Prefs, tools_on: bool) -> list[dict]:
    """Final shaping of the conversation for this model: text tool protocol, system folding, stale <think> blocks removed."""
    out = [dict(m) for m in msgs]
    if out and out[0]["role"] == "system":
        if tools_on and not p.native_tools:
            out[0]["content"] += "\n\n" + text_tool_protocol()
    for m in out:
        if m["role"] == "assistant" and m.get("content"):
            m["content"] = strip_think(m["content"])               # reasoning models should not re-read their old thoughts
    if p.fold_system and out and out[0]["role"] == "system":
        sysmsg = out.pop(0)["content"]
        for m in out:
            if m["role"] == "user":
                m["content"] = f"{sysmsg}\n\n---\n\n{m['content']}"
                break
        else:
            out.insert(0, {"role": "user", "content": sysmsg})
    return out


def est_tokens(msgs: list[dict]) -> int:
    """Cheap estimate (~3.3 chars/token for mixed code and prose); only used to size the context window."""
    return int(sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or "")) for m in msgs) / 3.3) + 64 * len(msgs)


def plan_context(msgs: list[dict], configured: int | None, model_max: int | None, reserve: int = 2048) -> tuple[int, str | None]:
    """Pick num_ctx so the prompt + room for the answer fits. Returns (num_ctx, note)."""
    need = int((est_tokens(msgs) + reserve) * 1.15)
    cur = configured or 16384
    if need <= cur:
        return cur, None
    for size in CTX_PRESETS:
        if size >= need and (not model_max or size <= model_max):
            return size, f"Raised the context window to {size // 1024}K for this request (the conversation needs about {need:,} tokens)."
    top = min(model_max or CTX_PRESETS[-1], CTX_PRESETS[-1])
    return max(cur, top), f"The conversation (~{need:,} tokens) is larger than this model's {top // 1024}K context; older tool output will be shortened."
