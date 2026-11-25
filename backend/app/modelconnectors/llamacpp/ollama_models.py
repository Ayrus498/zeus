"""Ollama's downloaded models, seen from llama.cpp: no copy, no second download.

Ollama keeps each model as a manifest (`manifests/<host>/<namespace>/<model>/<tag>`, JSON) that lists layers, and the layers as files named by their
digest (`blobs/sha256-<hex>`). The layer of type `application/vnd.ollama.image.model` is an ordinary GGUF file, so llama.cpp's `llama-server` can load
that blob directly (with the chat template stored inside it). This module only READS the store: it lists what is there, and never changes, moves or
deletes anything, so Ollama keeps working exactly as before. Removing such a model is Ollama's job (Models window, or `ollama rm`).

Where the store is: `$OLLAMA_MODELS` (when set, only that folder, as for Ollama itself), otherwise `~/.ollama/models` (a per-user Ollama), or `/usr/share/ollama/.ollama/models` (the Linux service install, owned by the
`ollama` user but readable by everyone). The manifest is treated as untrusted text: the digest must be 64 hex digits, the blob must be a regular file
(not a link) of the size the manifest states, and a model without a local blob (a cloud model, an unfinished download) is skipped.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

MODEL_LAYER = "application/vnd.ollama.image.model"
PROJECTOR_LAYER = "application/vnd.ollama.image.projector"
DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
MIN_BYTES = 1 << 20
MAX_MANIFEST = 1 << 20
DEFAULT_HOST = "registry.ollama.ai"


def store_dirs(home: Path | None = None, environ: dict | None = None) -> list[Path]:
    env = os.environ if environ is None else environ
    home = home or Path.home()
    if env.get("OLLAMA_MODELS"):                                  # as for Ollama itself: when it is set, that folder is THE store
        cands = [env["OLLAMA_MODELS"]]
    else:
        cands = [str(home / ".ollama" / "models"), "/usr/share/ollama/.ollama/models", "/var/lib/ollama/models"]
    out, seen = [], set()
    for c in cands:
        if not c:
            continue
        p = Path(c)
        try:
            key = str(p.resolve())
        except OSError:
            continue
        if key not in seen and (p / "manifests").is_dir() and (p / "blobs").is_dir():
            seen.add(key)
            out.append(p)
    return out


def _name(parts: tuple[str, ...]) -> str | None:
    """manifests/<host>/<namespace>/<model>/<tag> -> 'qwen3:8b', 'someone/model:tag' or 'host/ns/model:tag'."""
    if len(parts) < 4:
        return None
    host, ns, tag = parts[0], parts[1], parts[-1]
    model = "/".join(parts[2:-1])
    if host == DEFAULT_HOST:
        return f"{model}:{tag}" if ns == "library" else f"{ns}/{model}:{tag}"
    return f"{host}/{ns}/{model}:{tag}"


def scan(dirs: list[Path] | None = None) -> list[dict]:
    """[{"name", "path", "size", "store", "projector", "also"}] one per distinct model file (a second tag of the same file is listed under `also`)."""
    by_blob: dict[str, dict] = {}
    for store in dirs if dirs is not None else store_dirs():
        mdir, bdir = store / "manifests", store / "blobs"
        try:
            blobs_real = bdir.resolve()
        except OSError:
            continue
        for dirpath, _, files in os.walk(mdir, followlinks=False):
            for fn in sorted(files):
                mf = Path(dirpath) / fn
                try:
                    if mf.is_symlink() or mf.stat().st_size > MAX_MANIFEST:
                        continue
                    doc = json.loads(mf.read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                name = _name(mf.relative_to(mdir).parts)
                layers = doc.get("layers") if isinstance(doc, dict) else None
                if not name or not isinstance(layers, list):
                    continue
                model = next((l for l in layers if isinstance(l, dict) and l.get("mediaType") == MODEL_LAYER), None)
                m = DIGEST.match(str((model or {}).get("digest", "")))
                if not m:
                    continue
                blob = bdir / f"sha256-{m.group(1)}"
                try:
                    if blob.is_symlink() or not blob.is_file() or blob.resolve().parent != blobs_real:
                        continue
                    size = blob.stat().st_size
                except OSError:
                    continue
                want = model.get("size")
                if size < MIN_BYTES or (isinstance(want, int) and want != size):               # an unfinished download is not a model
                    continue
                projector = any(isinstance(l, dict) and l.get("mediaType") == PROJECTOR_LAYER for l in layers)
                row = by_blob.get(str(blob))
                if row:
                    row["also"].append(name)
                else:
                    by_blob[str(blob)] = {"name": name, "path": str(blob), "size": size, "store": str(store), "projector": projector, "also": []}
    rows = sorted(by_blob.values(), key=lambda r: r["name"].lower())
    for r in rows:
        r["also"].sort()
    return rows
