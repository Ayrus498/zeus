import json
import os
from pathlib import Path

import pytest

from app.modelconnectors.llamacpp import ollama_models as os_

REAL_STORE_DIRS = os_.store_dirs          # the conftest replaces os_.store_dirs for every test; this module tests the real one
HEX_A, HEX_B, HEX_C = "a" * 64, "b" * 64, "c" * 64


def store(tmp_path, models, blobs=None):
    """models: {manifest path under manifests/: layers}; blobs: {hex: size in bytes} (default: one big file per model layer)."""
    root = tmp_path / "store"
    (root / "blobs").mkdir(parents=True)
    (root / "manifests").mkdir()
    for rel, layers in models.items():
        mf = root / "manifests" / rel
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"schemaVersion": 2, "layers": layers}))
    for hexd, size in (blobs or {}).items():
        (root / "blobs" / f"sha256-{hexd}").write_bytes(b"G" * size)
    return root


def layer(kind, hexd, size):
    return {"mediaType": f"application/vnd.ollama.image.{kind}", "digest": f"sha256:{hexd}", "size": size}


BIG = 2 * 2**20


def test_lists_models_with_ollamas_own_names(tmp_path):
    root = store(tmp_path, {"registry.ollama.ai/library/qwen3.5/4b": [layer("model", HEX_A, BIG), layer("template", "d" * 64, 13)],
                            "registry.ollama.ai/library/qwen3-coder/30b": [layer("model", HEX_B, BIG)],
                            "registry.ollama.ai/someone/my-model/q4": [layer("model", HEX_C, BIG)],
                            "hf.co/owner/repo/latest": [layer("model", "e" * 64, BIG)]},
                 {HEX_A: BIG, HEX_B: BIG, HEX_C: BIG, "e" * 64: BIG})
    rows = os_.scan([root])
    assert [r["name"] for r in rows] == ["hf.co/owner/repo:latest", "qwen3-coder:30b", "qwen3.5:4b", "someone/my-model:q4"]
    r = next(x for x in rows if x["name"] == "qwen3.5:4b")
    assert r["path"] == str(root / "blobs" / f"sha256-{HEX_A}") and r["size"] == BIG and r["store"] == str(root) and r["projector"] is False


def test_two_tags_of_the_same_file_are_one_model_with_an_alias(tmp_path):
    root = store(tmp_path, {"registry.ollama.ai/library/deepseek-r1/14b": [layer("model", HEX_A, BIG)],
                            "registry.ollama.ai/library/deepseek-r1/latest": [layer("model", HEX_A, BIG)]}, {HEX_A: BIG})
    rows = os_.scan([root])
    assert len(rows) == 1 and rows[0]["name"] == "deepseek-r1:14b" and rows[0]["also"] == ["deepseek-r1:latest"]


def test_a_vision_projector_layer_is_noted_but_is_never_the_model(tmp_path):
    root = store(tmp_path, {"registry.ollama.ai/library/qwen3.5/4b": [layer("projector", HEX_B, BIG), layer("model", HEX_A, BIG)]}, {HEX_A: BIG, HEX_B: BIG})
    rows = os_.scan([root])
    assert len(rows) == 1 and rows[0]["path"].endswith(HEX_A) and rows[0]["projector"] is True


def test_models_without_a_usable_local_file_are_skipped(tmp_path):
    root = store(tmp_path, {"registry.ollama.ai/library/cloud/latest": [layer("template", HEX_A, 13)],                       # a cloud model: no model layer
                            "registry.ollama.ai/library/missing/latest": [layer("model", HEX_B, BIG)],                       # blob not on disk
                            "registry.ollama.ai/library/partial/latest": [layer("model", HEX_C, BIG)],                       # unfinished download: smaller than stated
                            "registry.ollama.ai/library/tiny/latest": [layer("model", "d" * 64, 100)],                       # not a model
                            "registry.ollama.ai/library/good/latest": [layer("model", "e" * 64, BIG)]},
                 {HEX_C: BIG - 5, "d" * 64: 100, "e" * 64: BIG})
    assert [r["name"] for r in os_.scan([root])] == ["good:latest"]


@pytest.mark.parametrize("digest", ["sha256:../../etc/passwd", "sha256:" + "A" * 64, "sha256:" + "a" * 63, "md5:" + "a" * 32, "", "../" + "a" * 64, None, 5])
def test_a_malformed_digest_never_reaches_the_file_system(tmp_path, digest):
    root = store(tmp_path, {"registry.ollama.ai/library/evil/latest": [{"mediaType": os_.MODEL_LAYER, "digest": digest, "size": BIG}]}, {HEX_A: BIG})
    assert os_.scan([root]) == []


def test_a_blob_that_is_a_link_to_somewhere_else_is_refused(tmp_path):
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"S" * BIG)
    root = store(tmp_path, {"registry.ollama.ai/library/evil/latest": [layer("model", HEX_A, BIG)]})
    (root / "blobs" / f"sha256-{HEX_A}").symlink_to(secret)
    assert os_.scan([root]) == []


def test_junk_manifests_are_ignored(tmp_path):
    root = store(tmp_path, {})
    base = root / "manifests" / "registry.ollama.ai" / "library"
    for n, text in (("a/1", "not json"), ("b/1", "[]"), ("c/1", '{"layers": 5}'), ("d/1", "{" + '"x":' * 10)):
        (base / n).parent.mkdir(parents=True, exist_ok=True)
        (base / n).write_text(text)
    (root / "manifests" / "short").write_text("{}")                                                                           # too few path parts
    assert os_.scan([root]) == []


def test_store_directories_come_from_the_environment_the_home_folder_and_the_service_install(tmp_path):
    env_store = store(tmp_path / "e", {})
    home = tmp_path / "home"
    (home / ".ollama/models/manifests").mkdir(parents=True)
    (home / ".ollama/models/blobs").mkdir(parents=True)
    assert REAL_STORE_DIRS(home, {"OLLAMA_MODELS": str(env_store)}) == [env_store]          # as for Ollama itself: OLLAMA_MODELS replaces the default places
    assert REAL_STORE_DIRS(home, {})[0] == home / ".ollama/models"                            # a per-user store first (then the system-wide ones, if present)
    assert REAL_STORE_DIRS(tmp_path / "nohome", {"OLLAMA_MODELS": str(tmp_path / "nothing")}) == []     # set but empty/missing: nothing, not the system store
    nothing = REAL_STORE_DIRS(tmp_path / "nohome", {})
    assert all(str(p).startswith(("/usr/share/ollama", "/var/lib/ollama")) for p in nothing)


def test_the_scan_never_modifies_the_store(tmp_path):
    root = store(tmp_path, {"registry.ollama.ai/library/m/1": [layer("model", HEX_A, BIG)]}, {HEX_A: BIG})
    before = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*"))
    os_.scan([root])
    assert sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*")) == before
