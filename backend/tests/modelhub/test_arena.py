import json

import pytest

from app.modelhub import arena

DATA = arena.load_snapshot()
HW_12 = {"gpu": "NVIDIA GeForce RTX 5070 Ti Laptop GPU", "vram_gb": 11.9, "vram_free_gb": 11.9, "bandwidth": 627, "bandwidth_known": True, "ram_gb": 62.0, "ram_bandwidth": 51}
HW_CPU = {"gpu": None, "vram_gb": 0.0, "vram_free_gb": 0.0, "bandwidth": None, "bandwidth_known": False, "ram_gb": 16.0, "ram_bandwidth": 51}
HW_24 = {"gpu": "RTX 4090", "vram_gb": 24.0, "vram_free_gb": 24.0, "bandwidth": 1008, "bandwidth_known": True, "ram_gb": 64.0, "ram_bandwidth": 51}
DENSE_8B = {"repo": "x/Dense-8B", "b": 8.0, "att": 8.0, "kv": 4096, "pf": 36, "fw": 0, "w": None, "ctx": 32768}
MOE_30B = {"repo": "x/Moe-30B-A3B", "b": 30.0, "att": 3.0, "kv": 2048, "pf": 48, "fw": 0, "w": None, "ctx": 32768}


def test_snapshot_is_attributed_and_covers_the_activities():
    assert "CC BY 4.0" in DATA["license"] and DATA["updated"]
    assert {c["id"] for c in DATA["categories"]} == set(arena.ACTIVITIES)
    assert all(m["open"] and 0 < m["rank"] and m["score"] > 0 for c in DATA["categories"] for m in c["models"])
    assert len(DATA["gpus"]) > 20 and DATA["ram"]


def test_normalize_drops_malformed_rows_and_unknown_categories():
    raw = {"aggiornato": "2026-01-01", "categorie": [
        {"id": "code", "totale": 10, "data": "d", "modelli": [{"nome": "A", "aperto": True, "pos": 1, "punti": 1500, "voti": 9, "hf": {"repo": "o/A", "b": 7}},
                                                              {"nome": "B", "aperto": True, "pos": "x", "punti": 1}, {"nome": "Paid", "aperto": False, "pos": 2, "punti": 1400}]},
        {"id": "music", "modelli": []}], "piccoli": [{"nome": "S", "hf": {"repo": "o/S", "b": 1}}, {"nome": "no-size"}],
        "schede": {"schede": [{"id": "g", "nome": "G", "gb": 8, "banda": 200}, {"id": "bad"}], "ram": [{"id": "ddr4", "nome": "D", "banda": 51}]}}
    n = arena.normalize(raw)
    assert [c["id"] for c in n["categories"]] == ["code"]
    assert [m["name"] for m in n["categories"][0]["models"]] == ["A"] and n["categories"][0]["top_paid"][0]["name"] == "Paid"
    assert len(n["small"]) == 1 and len(n["gpus"]) == 1 and n["ram"][0]["bw"] == 51
    assert arena.normalize("junk")["categories"] == []


def test_memory_model_matches_the_published_formula():
    r = arena.simulate(DENSE_8B, HW_24, 0.606, 8192)
    assert r["weights_gb"] == pytest.approx(4.8, abs=0.05)
    assert r["kv_gb"] == pytest.approx(4096 * 36 * 8192 / 1e9, abs=0.05)               # bytes per layer per token x layers x context
    assert r["serve_gb"] == pytest.approx(r["weights_gb"] + r["kv_gb"] + 0.6, abs=0.2)
    assert r["where"] == "gpu"


def test_speed_is_bandwidth_over_bytes_read_and_moe_reads_only_active_weights():
    dense = arena.simulate(DENSE_8B, HW_24, 0.606, 2048)["tps"]
    moe = arena.simulate(MOE_30B, HW_24, 0.606, 2048)["tps"]
    assert dense == pytest.approx(1 / ((8 * 0.606 + 0.3) / (1008 * 0.78) + 0.001), rel=0.05)
    assert moe > dense * 1.2                                # 3B active parameters vs 8B dense, even though the file is bigger


def test_placement_gpu_split_cpu_no():
    big = {"repo": "x/Big-70B", "b": 70.0, "att": 70.0, "kv": 4096, "pf": 80, "fw": 0, "w": None, "ctx": 8192}
    assert arena.simulate(DENSE_8B, HW_12)["where"] == "gpu"
    s = arena.simulate({**DENSE_8B, "b": 27.0, "att": 27.0}, HW_12)
    assert s["where"] == "split" and s["in_ram_gb"] > 0 and s["tps"] < arena.simulate(DENSE_8B, HW_12)["tps"]
    assert arena.simulate(DENSE_8B, HW_CPU)["where"] == "cpu" and 0 < arena.simulate(DENSE_8B, HW_CPU)["tps"] < 15
    assert arena.simulate(big, HW_CPU)["where"] == "no" and arena.simulate(big, HW_12)["where"] == "split"
    assert arena.simulate({**big, "b": 400.0}, HW_12)["where"] == "no"


def test_unknown_gpu_bandwidth_still_gives_fit_but_no_gpu_speed():
    hw = {**HW_12, "bandwidth": None, "bandwidth_known": False}
    r = arena.simulate(DENSE_8B, hw)
    assert r["where"] == "gpu" and r["tps"] is None and "speed unknown" in arena.verdict(r)["text"]


def test_verdict_levels():
    assert arena.verdict({"where": "gpu", "tps": 60.0, "serve_gb": 6, "in_ram_gb": 0})["level"] == "great"
    assert arena.verdict({"where": "split", "tps": 3.0, "serve_gb": 20, "in_ram_gb": 8})["level"] == "slow"
    assert arena.verdict({"where": "no", "tps": None, "serve_gb": 900, "in_ram_gb": 0})["label"] == "Too large"


def test_assess_detail_lists_every_quant_and_picks_the_best_that_fits():
    a = arena.assess({**DENSE_8B, "b": 14.0, "att": 14.0}, HW_12, detail=True)
    q = {x["quant"]: x for x in a["quants"]}
    assert list(q) == [n for n, *_ in arena.QUANTS]
    assert q["Q4_K_M"]["size_gb"] < q["Q8_0"]["size_gb"] < q["F16"]["size_gb"] and q["Q2_K"]["loss"] > q["Q4_K_M"]["loss"] > q["Q8_0"]["loss"]
    assert q["F16"]["where"] != "gpu" and a["best_quant"] in ("Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0")
    assert arena.assess(None, HW_12) is None


def test_find_gpu_matches_laptop_names_loosely_and_flags_them():
    g = arena.find_gpu("NVIDIA GeForce RTX 5070 Ti Laptop GPU", DATA["gpus"])
    assert g and "5070 Ti" in g["name"] and g["laptop"]
    assert arena.find_gpu("Some Unknown Accelerator", DATA["gpus"]) is None


def test_hardware_profile_uses_gib_and_discounts_laptop_bandwidth():
    about = {"gpus": [{"name": "NVIDIA GeForce RTX 5070 Ti Laptop GPU", "vram_total": 12 * 2**30}], "memory": {"total": 64 * 2**30}}
    hw = arena.hardware_profile(about, DATA)
    assert hw["vram_gb"] == 12.0 and hw["ram_gb"] == 64.0 and hw["bandwidth_known"]
    desk = next(g for g in DATA["gpus"] if g["name"].startswith("RTX 5070 Ti"))
    assert hw["bandwidth"] < desk["bw"]
    none = arena.hardware_profile({"gpus": [], "memory": {"total": 8 * 2**30}}, DATA)
    assert none["vram_gb"] == 0.0 and none["ram_gb"] == 8.0 and not none["bandwidth_known"]


LIB = [{"name": "qwen3-coder", "sizes": ["30b", "480b"], "community": False}, {"name": "granite4.1", "sizes": ["3b", "8b", "30b"], "community": False},
       {"name": "glm-5.3", "sizes": [], "community": False}, {"name": "someone/qwen3-coder", "sizes": ["30b"], "community": True}]


def test_ollama_key_and_matching():
    assert arena.ollama_key("Qwen/Qwen3-Coder-30B-A3B-Instruct") == ("qwen3coder", "30b")
    assert arena.ollama_key("ibm-granite/granite-4.1-8b-instruct") == ("granite4.1", "8b") or arena.ollama_key("ibm-granite/granite-4.1-8b-instruct")[1] == "8b"
    m = arena.match_ollama({"repo": "Qwen/Qwen3-Coder-30B-A3B-Instruct"}, "x", LIB)
    assert m == {"family": "qwen3-coder", "tag": "qwen3-coder:30b", "size": "30b", "local": True}
    assert arena.match_ollama({"repo": "Qwen/Qwen3-Coder-7B"}, "x", LIB) is None            # no such size in the library
    cloud = arena.match_ollama({"repo": "zai-org/GLM-5.3"}, "GLM-5.3", LIB)
    assert cloud and cloud["local"] is False
    assert arena.match_ollama(None, "Totally Unknown Model", LIB) is None


def test_overview_ranks_by_score_and_picks_a_best_model_for_this_machine():
    ov = arena.overview(DATA, HW_12, LIB)
    assert {a["id"] for a in ov["activities"]} == set(arena.ACTIVITIES)
    for a in ov["activities"]:
        scores = [m["score"] for m in a["models"]]
        assert scores == sorted(scores, reverse=True)
        b = a["best_here"]
        assert b is None or b["fit"]["where"] in ("gpu", "split", "cpu")
        if b and b["fit"]["tps"] and b["fit"]["tps"] < arena.READ_TPS:                 # a slow pick is only allowed when nothing comfortable exists
            assert not any(m["fit"] and m["fit"]["tps"] and m["fit"]["tps"] >= arena.READ_TPS for m in a["models"])
        q = a["best_quality"]
        assert q is None or (q["score"] >= b["score"] and q["name"] != b["name"])      # a slower, stronger option is reported separately
    chat = next(a for a in ov["activities"] if a["id"] == "chat")
    assert chat["best_here"]["fit"]["tps"] >= arena.READ_TPS
    assert ov["license"] == DATA["license"] and "ollama" in chat["models"][0]


def test_for_family_reports_scores_and_fit_per_size():
    fam = next(m for c in DATA["categories"] for m in c["models"] if m["hf"] and arena.ollama_key(m["hf"]["repo"])[0])
    key = arena.ollama_key(fam["hf"]["repo"])[0]
    rows = arena.for_family(DATA, HW_12, key)
    assert rows and all(r["score"] > 0 and r["rank"] > 0 for r in rows)
    assert arena.for_family(DATA, HW_12, "no-such-family") == []


# ---- API
def test_api_overview_detail_family_and_validation(client, monkeypatch):
    async def lib(db, force=False):
        return {"models": LIB}
    monkeypatch.setattr("app.modelconnectors.ollama.library.fetch_library", lib)
    r = client.get("/api/arena?ctx=4096")
    assert r.status_code == 200
    j = r.json()
    assert j["state"] == "bundled" and "CC BY 4.0" in j["license"] and j["hardware"]["ram_gb"] >= 0 and j["ctx"] == 4096
    assert client.get("/api/arena?ctx=1").json()["ctx"] == 2048                              # clamped
    repo = next(m["repo"] for a in j["activities"] for m in a["models"] if m["repo"] and m["fit"])
    d = client.get("/api/arena/detail", params={"repo": repo}).json()
    assert len(d["fit"]["quants"]) == 7
    assert client.get("/api/arena/detail", params={"repo": "nope/none"}).status_code == 404
    assert client.get("/api/arena/family/qwen3-coder").json()["results"] is not None


def test_refresh_failure_falls_back_to_the_bundled_snapshot(client, monkeypatch):
    import httpx

    class Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ConnectError("offline")
    arena._mem.clear()
    monkeypatch.setattr(arena.httpx, "AsyncClient", Boom)
    monkeypatch.setattr("app.modelconnectors.ollama.library.fetch_library", lambda db, force=False: _lib())
    j = client.get("/api/arena?refresh=true").json()
    assert j["state"] == "bundled" and "freellmarena.com" in j["error"] and j["activities"]


async def _lib():
    return {"models": LIB}


def test_live_refresh_is_validated_and_cached(client, monkeypatch):
    import httpx
    raw = json.loads(json.dumps({"aggiornato": "2030-01-01", "categorie": [{"id": "code", "totale": 1, "data": "d", "modelli": [
        {"nome": f"M{i}", "aperto": True, "pos": i + 1, "punti": 1400 - i, "voti": 5, "hf": {"repo": f"o/M{i}-7B", "b": 7}} for i in range(60)]}],
        "schede": {"schede": [{"id": "g", "nome": "RTX 4090", "gb": 24, "banda": 1008}], "ram": [{"id": "ddr4", "nome": "d", "banda": 51}]}}))

    class Ok:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k):
            assert url == arena.URL and not k.get("params")                                   # nothing but the plain public URL
            return httpx.Response(200, json=raw, request=httpx.Request("GET", url))
    arena._mem.clear()
    monkeypatch.setattr(arena.httpx, "AsyncClient", Ok)
    monkeypatch.setattr("app.modelconnectors.ollama.library.fetch_library", lambda db, force=False: _lib())
    j = client.get("/api/arena?refresh=true").json()
    assert j["state"] == "live" and j["updated"] == "2030-01-01" and len(j["activities"]) == 1
    arena._mem.clear()
    assert client.get("/api/arena").json()["state"] in ("cached", "live")                      # the last good copy survives a restart
    arena._mem.clear()


def test_best_here_prefers_a_fast_moe_over_a_tiny_model_that_merely_fits():
    data = {"updated": "d", "license": "l", "source": "u", "categories": [{"id": "code", "total": 5, "date": "d", "top_paid": [], "models": [
        {"name": "Big dense", "org": "o", "license": "", "open": True, "rank": 1, "score": 1600, "votes": 1, "hf": {**DENSE_8B, "repo": "o/Big", "b": 70.0, "att": 70.0}},
        {"name": "Fast MoE", "org": "o", "license": "", "open": True, "rank": 2, "score": 1500, "votes": 1, "hf": {**MOE_30B, "repo": "o/Moe"}},
        {"name": "Tiny", "org": "o", "license": "", "open": True, "rank": 3, "score": 1100, "votes": 1, "hf": {**DENSE_8B, "repo": "o/Tiny", "b": 4.0, "att": 4.0}}]}]}
    a = arena.overview(data, HW_12, [])["activities"][0]
    assert a["best_here"]["name"] == "Fast MoE"                                         # strong and fast beats tiny-but-fits
    assert a["best_quality"]["name"] == "Big dense" and a["best_quality"]["fit"]["tps"] < arena.READ_TPS
