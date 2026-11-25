"""Which model is best for which job, how it scored, and whether it runs on this machine.

Data: Arena's public human-preference leaderboard (CC BY 4.0, Arena Intelligence) as filtered by https://freellmarena.com, which adds parameter
counts and architecture numbers read from the official Hugging Face repositories, a catalogue of graphics cards with memory bandwidth, and
speeds calibrated on real llama.cpp runs. Zeus-Agent is not affiliated with either. A snapshot ships in `data/arena_snapshot.json`; `fetch`
refreshes it from https://freellmarena.com/data.json on request and keeps the last good copy in the settings table.

Privacy: the refresh is one plain GET of a public file; nothing about the user, the machine or the projects is sent.
Scores are blind human votes on chat and web-app answers; they say which answers people prefer, not that a model passes a given test suite.

`assess` ports the site's published memory and speed model (weights at 0.606 bytes per parameter for Q4_K_M, KV cache from the model's
architecture, 0.6 GB of runtime overhead, speed = memory bandwidth x 0.78 / bytes read per token; about +-30%) and applies it to the
GPU and RAM of this machine.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import httpx

URL = "https://freellmarena.com/data.json"
SNAPSHOT = Path(__file__).resolve().parent / "data" / "arena_snapshot.json"
TTL = 24 * 3600
LICENSE = "Arena leaderboard dataset, CC BY 4.0, by Arena Intelligence, via freellmarena.com"
EFF_GPU, EFF_RAM, RUNTIME = 0.78, 0.8, 0.6
COST_MS = {"denso": 1.0, "moe": 2.8}                   # fixed cost per token (ms), dense vs mixture-of-experts, on non-Apple GPUs
READ_TPS = 8                                           # tokens/s a person reads comfortably
QUANTS = [("Q2_K", 0.40, 56.5, 8.82), ("Q3_K_M", 0.50, 10.5, 4.72), ("Q4_K_M", 0.606, 2.82, 1.20), ("Q5_K_M", 0.711, 0.91, 0.40),
          ("Q6_K", 0.82, 0.35, 0.16), ("Q8_0", 1.0625, 0.04, 0.02), ("F16", 2.0, 0.0, 0.0)]
# What Zeus-Agent can run: text and vision models. (Image, video and audio models are on the website, not here.)
ACTIVITIES = {
    "code": {"label": "Coding", "arena": "WebDev", "blurb": "Writing and fixing code, building web apps. Arena's WebDev ranking."},
    "agentcode": {"label": "Agentic coding", "arena": "Agent coding", "blurb": "Coding with tools over many steps: reading files, running commands, fixing tests. What Zeus-Agent does."},
    "agent": {"label": "Agents", "arena": "Agents", "blurb": "Long tool-using tasks beyond code: planning, research, operating software."},
    "chat": {"label": "Chat & reasoning", "arena": "Chat", "blurb": "General questions, writing, explanations and reasoning."},
    "vision": {"label": "Vision", "arena": "Vision", "blurb": "Understanding screenshots, diagrams and photos."},
    "document": {"label": "Documents", "arena": "Documents", "blurb": "Reading and answering from long documents and PDFs."},
}
_mem: dict[str, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------------------------------- data
def _hf(h) -> dict | None:
    if not isinstance(h, dict) or not isinstance(h.get("b"), (int, float)) or h["b"] <= 0:
        return None
    num = lambda k: h.get(k) if isinstance(h.get(k), (int, float)) and not isinstance(h.get(k), bool) else None  # noqa: E731
    return {"repo": str(h.get("repo") or "")[:200], "b": float(h["b"]), "att": num("att"), "kv": num("kv"), "pf": num("pf"), "fw": num("fw"), "w": num("w"), "ctx": num("ctx")}


def normalize(raw: dict) -> dict:
    """The site's data.json -> the compact English-keyed shape used here. Tolerant: anything malformed is dropped. Pure function."""
    cats = []
    for c in raw.get("categorie", []) if isinstance(raw, dict) else []:
        if c.get("id") not in ACTIVITIES:
            continue
        open_models, paid = [], []
        for m in c.get("modelli", []):
            try:
                row = {"name": str(m["nome"])[:120], "org": str(m.get("org") or "")[:40], "license": str(m.get("licenza") or "")[:80], "open": bool(m.get("aperto")),
                       "rank": int(m["pos"]), "score": int(m["punti"]), "votes": int(m.get("voti") or 0), "hf": _hf(m.get("hf"))}
            except (KeyError, TypeError, ValueError):
                continue
            (open_models if row["open"] else paid).append(row)
        cats.append({"id": c["id"], "total": int(c.get("totale") or 0), "date": str(c.get("data") or ""), "models": open_models, "top_paid": paid[:3]})
    small = []
    for m in raw.get("piccoli", []) if isinstance(raw, dict) else []:
        hf = _hf(m.get("hf"))
        if hf and m.get("nome"):
            small.append({"name": str(m["nome"])[:120], "org": str(m.get("org") or "")[:40], "downloads": int(m.get("download") or 0), "cats": [str(x) for x in m.get("cat", [])][:6],
                          "gated": bool(m.get("gated")), "hf": hf})
    gpus = []
    for g in (raw.get("schede") or {}).get("schede", []) if isinstance(raw, dict) else []:
        if isinstance(g.get("gb"), (int, float)) and isinstance(g.get("banda"), (int, float)):
            gpus.append({"id": str(g.get("id")), "name": str(g.get("nome")), "brand": str(g.get("marca") or ""), "gb": g["gb"], "bw": g["banda"], "unified": bool(g.get("unificata"))})
    ram = [{"id": str(r["id"]), "name": str(r["nome"]), "bw": r["banda"]} for r in (raw.get("schede") or {}).get("ram", []) if isinstance(r.get("banda"), (int, float))] if isinstance(raw, dict) else []
    return {"updated": str(raw.get("aggiornato") or "") if isinstance(raw, dict) else "", "source": URL, "license": LICENSE, "categories": cats, "small": small, "gpus": gpus, "ram": ram}


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text("utf-8"))


async def fetch(db=None, force: bool = False) -> dict:
    """Snapshot data, refreshed from freellmarena.com when `force` (or the cache is a day old and a refresh was ever requested)."""
    now = time.time()
    hit = _mem.get("data")
    if hit and not force and now - hit[0] < TTL:
        return hit[1]
    err = None
    if force:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "zeus-agent"}) as c:
                r = await c.get(URL)
                r.raise_for_status()
            data = normalize(r.json())
            if sum(len(c["models"]) for c in data["categories"]) >= 50 and data["gpus"]:
                data["fetched_at"], data["source_state"] = int(now), "live"
                _mem["data"] = (now, data)
                if db is not None:
                    try:
                        db.set_setting("arena:data", json.dumps(data, separators=(",", ":")))
                    except Exception:  # noqa: BLE001
                        pass
                return data
            err = "freellmarena.com returned data Zeus-Agent could not read."
        except (httpx.HTTPError, ValueError, OSError) as e:
            err = f"Could not reach freellmarena.com ({type(e).__name__})."
    data = None
    if db is not None:
        try:
            raw = db.get_setting("arena:data")
            data = json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            data = None
    if data:
        data["source_state"] = "cached"
    else:
        data = load_snapshot()
        data["source_state"] = "bundled"
    if err:
        data = {**data, "error": err}
    else:
        _mem["data"] = (now, data)
    return data


# ---------------------------------------------------------------------------------------------------- hardware
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def find_gpu(name: str, catalogue: list[dict]) -> dict | None:
    """The catalogue card that matches a detected GPU name ('NVIDIA GeForce RTX 5070 Ti Laptop GPU' -> 'RTX 5070 Ti'). Laptop parts match their desktop
    name loosely (their memory bandwidth is lower), so a laptop match is flagged. None when nothing matches."""
    n = _norm(name)
    best = None
    for g in catalogue:
        key = _norm(re.sub(r"\s*\d+\s*GB\s*$", "", g["name"], flags=re.I))
        if key and key in n and (best is None or len(key) > best[0]):
            best = (len(key), g)
    if not best:
        return None
    cands = [g for g in catalogue if _norm(re.sub(r"\s*\d+\s*GB\s*$", "", g["name"], flags=re.I)) == _norm(re.sub(r"\s*\d+\s*GB\s*$", "", best[1]["name"], flags=re.I))]
    return {**best[1], "laptop": "laptop" in name.lower() or "mobile" in name.lower(), "variants": len(cands)}


def hardware_profile(about: dict, data: dict, vram_total: int | None = None, ram_total: int | None = None) -> dict:
    """What this machine can offer a model: GPU memory (free now) and bandwidth, system RAM and its speed. All numbers GB and GB/s."""
    gpus = about.get("gpus") or []
    g0 = gpus[0] if gpus else {}
    total = (vram_total if vram_total is not None else g0.get("vram_total"))
    total_gb = total / 2**30 if total else None          # GiB: a '12 GB' card reports 11.9, which is what a model can really use
    match = find_gpu(g0.get("name") or "", data.get("gpus", [])) if g0.get("name") else None
    bw = None
    if match:
        same = [g for g in data["gpus"] if _norm(re.sub(r"\s*\d+\s*GB\s*$", "", g["name"], flags=re.I)) == _norm(re.sub(r"\s*\d+\s*GB\s*$", "", match["name"], flags=re.I))]
        pick = min(same, key=lambda g: abs(g["gb"] - (total_gb or g["gb"]))) if same else match      # the variant closest to the detected memory
        bw = pick["bw"] * (0.7 if match["laptop"] else 1.0)                                            # laptop parts clock memory lower: be conservative
    mem = about.get("memory") or {}
    ram = (ram_total if ram_total is not None else mem.get("total"))
    ram_gb = ram / 2**30 if ram else 0
    ram_bw = next((r["bw"] for r in data.get("ram", []) if r["id"] == "ddr4"), 51)                     # RAM type is not readable without root: assume the slower kit
    return {"gpu": g0.get("name"), "vram_gb": round(total_gb, 1) if total_gb else 0.0,
            "vram_free_gb": round(total_gb, 1) if total_gb else 0.0,          # what the machine can offer: Ollama evicts its own loaded models to make room
            "bandwidth": round(bw) if bw else None, "bandwidth_known": bool(bw), "gpu_catalogue_match": match["name"] if match else None,
            "ram_gb": round(ram_gb, 1), "ram_bandwidth": ram_bw, "unified": bool(match and match.get("unified"))}


# ---------------------------------------------------------------------------------------------------- the memory and speed model
def is_moe(hf: dict) -> bool:
    return bool(hf.get("att")) and hf["att"] < hf["b"] * 0.6


def kv_gb(hf: dict, ctx: int, kvq: float = 1.0) -> float:
    if hf.get("kv") and hf.get("pf") is not None:
        tok = hf["pf"] * ctx + (hf.get("fw") or 0) * min(ctx, hf.get("w") or ctx)
        return hf["kv"] * tok * kvq / 1e9
    return 0.000004 * hf["b"] * ctx * kvq               # rough estimate when the architecture is unknown


def simulate(hf: dict, hw: dict, bpp: float = 0.606, ctx: int = 8192, kvq: float = 1.0) -> dict:
    """Where the model would live and how fast it would run. `where`: gpu (all in GPU memory), split (GPU + system RAM), cpu (no usable GPU, RAM only), no."""
    weights = hf["b"] * bpp
    kv = kv_gb(hf, ctx, kvq)
    serve = weights + kv + RUNTIME
    vram, ram = hw.get("vram_free_gb") or 0.0, hw.get("ram_gb") or 0.0
    in_ram, where = 0.0, "no"
    if vram >= 2 and serve <= vram:
        where = "gpu"
    elif vram >= 2 and serve <= vram + ram * 0.8 and weights > serve - vram:
        where, in_ram = "split", serve - vram
    elif vram < 2 and serve <= ram * 0.8:
        where, in_ram = "cpu", serve
    speed = None
    if where != "no":
        active = (hf.get("att") or hf["b"]) * bpp
        share = in_ram / serve if serve else 0
        ram_t = (active * share + (kv * share if where == "cpu" else 0)) / (hw["ram_bandwidth"] * EFF_RAM)
        gpu_t = None
        if where != "cpu" and hw.get("bandwidth"):
            gpu_t = (active * (1 - share) + kv * (1 - share)) / (hw["bandwidth"] * EFF_GPU) + COST_MS["moe" if is_moe(hf) else "denso"] / 1000
        if where == "cpu":
            speed = 1 / (ram_t + COST_MS["denso"] / 1000) if ram_t else None
        elif gpu_t is not None:
            speed = 1 / (gpu_t + ram_t)
    return {"where": where, "weights_gb": round(weights, 1), "kv_gb": round(kv, 1), "serve_gb": round(serve, 1), "in_ram_gb": round(in_ram, 1),
            "tps": round(speed, 1) if speed else None}


def quality_loss(bpp_row, b: float) -> float:
    """Perplexity increase vs 16-bit, interpolated on log(parameters) between an 8B and a 70B model (as measured by llama.cpp)."""
    _, _, p8, p70 = bpp_row
    t = min(1.0, max(0.0, (math.log(max(b, 0.1)) - math.log(8)) / (math.log(70) - math.log(8))))
    return p8 + (p70 - p8) * t


def quality_word(p: float) -> str:
    return "same as full" if p < 0.1 else "near-lossless" if p < 0.6 else "very small loss" if p < 1.6 else "small loss" if p < 3.5 else "noticeable loss" if p < 12 else "large loss"


def gguf_fit(size_bytes: int, quant: str | None, hw: dict, ctx: int = 8192) -> dict:
    """How a GGUF file of this size would run on this machine (same memory/speed model as `assess`; the parameter count is derived from the file size and its
    quantization, and a dense model is assumed, so a mixture-of-experts model is usually faster than the estimate)."""
    bpp = next((q[1] for q in QUANTS if q[0] == (quant or "").upper()), 0.606)
    gb = size_bytes / 1e9
    r = simulate({"b": max(gb / bpp, 0.1), "att": None, "kv": None, "pf": None, "fw": None, "w": None, "ctx": None}, hw, bpp, ctx)
    r["verdict"] = verdict(r)
    return r


# which quantization to suggest when several fit: the usual balance of size and quality first
QUANT_PREFERENCE = ["Q5_K_M", "Q4_K_M", "Q6_K", "Q5_K_S", "Q4_K_S", "Q8_0", "IQ4_XS", "Q4_0", "Q3_K_M", "Q3_K_L", "IQ3_M", "Q3_K_S"]


def suggest_file(files: list[dict]) -> str | None:
    """The file to download: of those that run entirely on the GPU the preferred quantization; otherwise Q4_K_M (or the smallest) among those that run at all.
    `files` carry `file`, `size`, `quant`, `split`, `fit`. Split (multi-part) files are never suggested."""
    single = [f for f in files if not f.get("split") and f.get("fit")]
    def pick(pool):
        for q in QUANT_PREFERENCE:
            for f in pool:
                if (f.get("quant") or "").upper() == q:
                    return f["file"]
        return min(pool, key=lambda f: f["size"])["file"] if pool else None
    gpu = [f for f in single if f["fit"]["where"] == "gpu"]
    if gpu:
        return pick(gpu)
    runs = [f for f in single if f["fit"]["where"] in ("split", "cpu")]
    if not runs:
        return None
    q4 = [f for f in runs if (f.get("quant") or "").upper() == "Q4_K_M"]
    return q4[0]["file"] if q4 else min(runs, key=lambda f: f["size"])["file"]


def assess(hf: dict | None, hw: dict, ctx: int = 8192, detail: bool = False) -> dict | None:
    """Verdict for one model at Q4_K_M, plus (detail) every quantization level. None when the model's size is unknown."""
    if not hf:
        return None
    r = simulate(hf, hw, 0.606, ctx)
    r["verdict"] = verdict(r)
    if detail:
        rows = []
        for q in QUANTS:
            s = simulate(hf, hw, q[1], ctx)
            rows.append({"quant": q[0], "size_gb": s["weights_gb"], "where": s["where"], "tps": s["tps"], "loss": round(quality_loss(q, hf["b"]), 2), "quality": quality_word(quality_loss(q, hf["b"]))})
        fitting = [x for x in rows if x["where"] == "gpu" and x["quant"] not in ("Q2_K", "Q3_K_M")]
        best = fitting[-1] if fitting else next((x for x in rows if x["quant"] == "Q4_K_M" and x["where"] != "no"), None)
        r["quants"], r["best_quant"] = rows, best["quant"] if best else None
    return r


def verdict(r: dict) -> dict:
    """A word and a sentence a person can act on."""
    w, tps = r["where"], r["tps"]
    speed = (f"about {tps:g} tokens/s" if tps else "speed unknown")
    comfy = tps is not None and tps >= READ_TPS
    if w == "gpu":
        return {"level": "great" if comfy or tps is None else "ok", "label": "Runs on your GPU", "text": f"Fits in GPU memory ({r['serve_gb']:g} GB needed), {speed}."}
    if w == "split":
        return {"level": "ok" if comfy else "slow", "label": "GPU + RAM", "text": f"{r['in_ram_gb']:g} GB spills into system RAM, so it is slower: {speed}."}
    if w == "cpu":
        return {"level": "ok" if comfy else "slow", "label": "CPU only", "text": f"No usable GPU memory: runs from RAM ({r['serve_gb']:g} GB), {speed}."}
    return {"level": "no", "label": "Too large", "text": f"Needs about {r['serve_gb']:g} GB; this machine has less GPU memory and RAM."}


# ---------------------------------------------------------------------------------------------------- matching to Ollama's library
_SUFFIX = re.compile(r"(?:^|[-_.])(?:instruct|it|chat|gguf|awq|gptq|fp8|bf16|preview|rl|hf)(?=$|[-_.])", re.I)
_SIZE = re.compile(r"(?:^|[-_ ])(e?\d+(?:\.\d+)?)\s*b(?=$|[-_ ])", re.I)


def ollama_key(repo_or_name: str) -> tuple[str, str | None]:
    """('qwen3coder', '30b') for 'Qwen/Qwen3-Coder-30B-A3B-Instruct'. The family is every word before the size, without separators."""
    name = repo_or_name.split("/")[-1]
    name = re.sub(r"-\d{4}(?:-\d{2})?$", "", name)                       # trailing date or version stamp like -2507
    m = _SIZE.search(name)
    fam = name[:m.start()] if m else _SUFFIX.sub("", name)
    return _norm(fam), (m.group(1).lower() + "b" if m else None)


def match_ollama(hf: dict | None, name: str, library: list[dict]) -> dict | None:
    """The Ollama library entry (and size tag) that is the same model, or None. Conservative: family and size must both agree."""
    fam, size = ollama_key(hf["repo"] if hf and hf.get("repo") else name)
    if not fam:
        return None
    for e in library:
        if _norm(e["name"].split("/")[-1]) == fam and not e.get("community"):
            sizes = [s.lower() for s in e.get("sizes", [])]
            if size is None or size in sizes:
                # an entry without size tags is a cloud-hosted or very large model: it is listed, but not offered as a local download
                return {"family": e["name"], "tag": f"{e['name']}:{size}" if size and sizes else e["name"], "size": size, "local": bool(sizes)}
    return None


def overview(data: dict, hw: dict, library: list[dict], ctx: int = 8192) -> dict:
    """Every activity: Arena ranking of open models, each with this machine's verdict and its Ollama match. Sorted by score."""
    acts = []
    for c in data["categories"]:
        meta = ACTIVITIES[c["id"]]
        rows = []
        for m in c["models"]:
            a = assess(m["hf"], hw, ctx)
            rows.append({**{k: m[k] for k in ("name", "org", "license", "rank", "score", "votes")}, "repo": (m["hf"] or {}).get("repo"), "params_b": (m["hf"] or {}).get("b"),
                         "active_b": (m["hf"] or {}).get("att"), "context": (m["hf"] or {}).get("ctx"), "fit": a, "ollama": match_ollama(m["hf"], m["name"], library)})
        rows.sort(key=lambda r: -r["score"])
        runnable = [r for r in rows if r["fit"] and r["fit"]["where"] in ("gpu", "split", "cpu")]
        # best_here: the highest score that is comfortable to use (a person reads about 8 tokens/s; unknown speed counts only when it is all on the GPU).
        comfy = [r for r in runnable if (r["fit"]["tps"] or 0) >= READ_TPS or (r["fit"]["tps"] is None and r["fit"]["where"] == "gpu")]
        best = (comfy or runnable or [None])[0]
        # best_quality: the highest score that runs at all, when it is better than best_here but slow.
        top = runnable[0] if runnable else None
        acts.append({"id": c["id"], "label": meta["label"], "arena": meta["arena"], "blurb": meta["blurb"], "date": c["date"], "total": c["total"], "top_paid": c["top_paid"],
                     "best_here": best, "best_quality": top if top and best and top["name"] != best["name"] else None, "models": rows})
    return {"updated": data["updated"], "license": data["license"], "source": data["source"], "state": data.get("source_state", "bundled"), "error": data.get("error"),
            "hardware": hw, "ctx": ctx, "activities": acts}


def for_family(data: dict, hw: dict, family: str, ctx: int = 8192) -> list[dict]:
    """Arena entries that belong to an Ollama family (e.g. 'qwen3-coder'): per activity, score, rank and the verdict for each size."""
    fam = _norm(family.split("/")[-1])
    out = []
    for c in data["categories"]:
        for m in c["models"]:
            f, size = ollama_key((m["hf"] or {}).get("repo") or m["name"])
            if f == fam:
                out.append({"activity": c["id"], "label": ACTIVITIES[c["id"]]["label"], "name": m["name"], "rank": m["rank"], "of": c["total"], "score": m["score"], "votes": m["votes"],
                            "size": size, "params_b": (m["hf"] or {}).get("b"), "fit": assess(m["hf"], hw, ctx)})
    return sorted(out, key=lambda r: (r["activity"], -r["score"]))
