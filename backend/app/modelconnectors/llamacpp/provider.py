"""llama.cpp engine: runs GGUF models through the `llama-server` program that ships with llama.cpp.

Zeus-Agent does not bundle llama.cpp (the right build depends on your GPU). It finds `llama-server` (a path you set, $LLAMA_SERVER,
the PATH, or a few usual places), lists the `.gguf` model files in your model folders, starts the server for the model you
choose, talks to it over its OpenAI-compatible API on 127.0.0.1 and stops it again when you switch model or quit. One model is
loaded at a time. The provider offers the same methods as the Ollama provider (status, list_models, show, chat_stream, ...), so
the agent loop does not care which engine runs.

The stream is translated into the chunk shape the agent loop already understands:
  {"message": {"content", "thinking", "tool_calls": [{"function": {"name", "arguments": {...}}}]}, "done", "prompt_eval_count",
   "eval_count", "eval_duration"}.
Everything stays on this machine. The only network use is the optional model download from huggingface.co, started by the user.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import httpx

from . import finder as llamafind
from . import ollama_models as ollamastore

from ...core import config
from . import gguf
from ..base import ConnectorError

SETTING = "llamacpp"
HF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,95}/[A-Za-z0-9][A-Za-z0-9._\-]{0,95}$")
HF_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,200}\.gguf$")
SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)
LOAD_TIMEOUT = 900.0
MAX_SCAN_DEPTH = 4
DEFAULT_SETTINGS = {"ctx": 0, "ngl": "auto", "threads": 0, "flash_attn": "auto", "cache_type_k": "", "cache_type_v": "", "batch": 0, "ubatch": 0, "extra_args": ""}
SETTING_CHOICES = {"flash_attn": ("auto", "on", "off"), "cache_type_k": ("", "f16", "q8_0", "q4_0"), "cache_type_v": ("", "f16", "q8_0", "q4_0")}


class LlamaCppError(ConnectorError):
    """llama.cpp could not do what was asked (no llama-server, a model that will not load, a request the server refused)."""


HF_BASE = os.environ.get("ZEUS_HF_BASE", "https://huggingface.co").rstrip("/")          # (a test or a mirror can point it elsewhere)
OLLAMA_PREFIX = "ollama/"                                                                # models taken from Ollama's store are listed as ollama/<name>


def managed_dir() -> Path:
    return config.DATA_DIR / "models"


def default_model_dirs() -> list[Path]:
    home = Path.home()
    return [managed_dir(), home / ".cache" / "llama.cpp", home / ".cache" / "huggingface" / "hub"]


def default_config() -> dict:
    return {"binary": "", "models_dirs": [], "ollama_models": True, "defaults": dict(DEFAULT_SETTINGS), "models": {}, "last_model": ""}


def clean_settings(s: dict | None) -> dict:
    """Only known keys with sane types; anything else is dropped. Used for both the defaults and per-model overrides."""
    out: dict = {}
    for k, v in (s or {}).items():
        if k not in DEFAULT_SETTINGS:
            continue
        if k in ("ctx", "threads", "batch", "ubatch"):
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            out[k] = max(0, min(n, 4_194_304))
        elif k == "ngl":
            sv = str(v).strip().lower()
            if sv in ("auto", "all") or (sv.isdigit() and int(sv) <= 999):
                out[k] = sv
        elif k in SETTING_CHOICES:
            if str(v) in SETTING_CHOICES[k]:
                out[k] = str(v)
        elif k == "extra_args":
            out[k] = str(v)[:500]
    return out


def merged_config(raw: str | None) -> dict:
    cfg = default_config()
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    if isinstance(doc, dict):
        if isinstance(doc.get("binary"), str):
            cfg["binary"] = doc["binary"][:500]
        if isinstance(doc.get("models_dirs"), list):
            cfg["models_dirs"] = [d for d in doc["models_dirs"] if isinstance(d, str) and d][:20]
        if isinstance(doc.get("ollama_models"), bool):
            cfg["ollama_models"] = doc["ollama_models"]
        cfg["defaults"].update(clean_settings(doc.get("defaults")))
        if isinstance(doc.get("models"), dict):
            cfg["models"] = {str(k)[:300]: clean_settings(v) for k, v in list(doc["models"].items())[:500] if isinstance(v, dict)}
        if isinstance(doc.get("last_model"), str):
            cfg["last_model"] = doc["last_model"]
    return cfg


def build_args(binary: str, path: str, port: int, ctx: int, s: dict, caps: dict, has_gpu: bool, alias: str) -> list[str]:
    """The llama-server command line for one model. Pure function (unit tested)."""
    a = [binary, "-m", path, "--host", "127.0.0.1", "--port", str(port), "-c", str(ctx), "-np", "1", "--alias", alias]
    if caps.get("jinja", True):
        a.append("--jinja")                                      # needed for the model's own tool-calling template
    ngl = str(s.get("ngl", "auto"))
    if ngl == "all":
        a += ["-ngl", "999"]
    elif ngl.isdigit():
        a += ["-ngl", ngl]
    elif not caps.get("fit"):                                     # "auto": newer builds fit the model to the device themselves
        a += ["-ngl", "99" if has_gpu else "0"]
    if s.get("threads"):
        a += ["-t", str(int(s["threads"]))]
    if s.get("batch"):
        a += ["-b", str(int(s["batch"]))]
    if s.get("ubatch"):
        a += ["-ub", str(int(s["ubatch"]))]
    if s.get("flash_attn") in ("on", "off"):
        a += ["--flash-attn", s["flash_attn"]]
    if s.get("cache_type_k"):
        a += ["--cache-type-k", s["cache_type_k"]]
    if s.get("cache_type_v"):
        a += ["--cache-type-v", s["cache_type_v"]]
    if s.get("extra_args"):
        try:
            a += shlex.split(s["extra_args"])
        except ValueError:
            pass
    return a


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """Ollama-style history -> OpenAI chat messages: tool calls get ids and JSON-string arguments, tool results point at them."""
    out: list[dict] = []
    pending: list[tuple[str, str]] = []          # (call id, tool name) of the assistant's calls not yet answered
    n = 0
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            pending = []
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                n += 1
                cid = f"call_{n}"
                args = fn.get("arguments", {})
                calls.append({"id": cid, "type": "function", "function": {"name": fn.get("name", ""), "arguments": args if isinstance(args, str) else json.dumps(args)}})
                pending.append((cid, fn.get("name", "")))
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            idx = next((i for i, (_, name) in enumerate(pending) if name == m.get("tool_name")), 0 if pending else None)
            cid = pending.pop(idx)[0] if idx is not None else "call_0"
            out.append({"role": "tool", "tool_call_id": cid, "content": m.get("content") or ""})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def flatten_messages(messages: list[dict], fold_system: bool = False) -> list[dict]:
    """A plain-text history for chat templates that only accept strictly alternating user / assistant turns (Gemma, many older models).

    Used for models without native tool calling, which were taught the text tool protocol: the assistant's stored tool call becomes the JSON text it
    wrote, a tool result becomes a user turn ("Tool result (name): ..."), and neighbours with the same role are merged, so the turns alternate.
    `fold_system` also moves the system prompt into the first user turn (for templates that reject a system role)."""
    out: list[dict] = []

    def push(role: str, text: str):
        if out and out[-1]["role"] == role and role != "system":
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    for m in messages:
        role, c = m.get("role"), m.get("content") or ""
        if role == "assistant":
            parts = [c] if c.strip() else []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                a = fn.get("arguments", {})
                parts.append(json.dumps({"name": fn.get("name", ""), "arguments": a if isinstance(a, dict) else parse_arguments(a)}, ensure_ascii=False))
            push("assistant", "\n".join(parts) if parts else "(no output)")
        elif role == "tool":
            push("user", f"Tool result ({m.get('tool_name') or 'tool'}):\n{c}")
        elif role == "system":
            out.append({"role": "system", "content": c})
        else:
            push("user", c)
    if fold_system:
        sysmsgs = [m for m in out if m["role"] == "system"]
        out = [m for m in out if m["role"] != "system"]
        if sysmsgs:
            text = "\n\n".join(m["content"] for m in sysmsgs)
            first = next((m for m in out if m["role"] == "user"), None)
            if first:
                first["content"] = f"{text}\n\n---\n\n{first['content']}"
            else:
                out.insert(0, {"role": "user", "content": text})
    return out


class _TemplateRejected(Exception):
    """The server refused the request because the model's chat template cannot take this conversation shape."""


TEMPLATE_COMPLAINT = re.compile(r"alternate|roles?\b|system|parser|template|jinja", re.I)


def parse_arguments(s: str) -> dict:
    if not s or not s.strip():
        return {}
    try:
        v = json.loads(s)
    except ValueError:
        return {"_unparsed_arguments": s[:2000]}
    return v if isinstance(v, dict) else {"_unparsed_arguments": s[:2000]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(proc) -> bool:
    return proc is not None and proc.returncode is None


class LlamaCppProvider:
    """One instance per backend. Holds the llama-server process (at most one model loaded)."""

    name = "llamacpp"

    def __init__(self, db):
        self.db = db
        self.proc: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.model: str | None = None            # name of the loaded model
        self.path: str | None = None
        self.ctx: int = 0
        self.sig: tuple | None = None
        self.started_at: float | None = None
        self.starting: str | None = None         # name of the model being loaded right now
        self.ready = False                       # the loaded model answers /health (so `running` means usable, not just launched)
        self.lock = asyncio.Lock()
        self.busy = 0                            # streams in flight: the model is never swapped under them
        self._probe: dict[tuple, dict] = {}
        self._found: list[dict] | None = None    # llama-server programs found on this computer (see `candidates`)
        self._found_at = 0.0
        self.log_path = config.DATA_DIR / "llama-server.log"

    # ------------------------------------------------------------------ configuration
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}" if self.port and _alive(self.proc) else ""

    def config(self) -> dict:
        return merged_config(self.db.get_setting(SETTING))

    def save_config(self, patch: dict) -> dict:
        cfg = self.config()
        if "binary" in patch:
            b = str(patch["binary"] or "").strip()
            if b and not os.path.isabs(b):
                raise LlamaCppError("The llama-server path must be absolute (for example /home/you/llama.cpp/build/bin/llama-server).")
            cfg["binary"] = b
        if "models_dirs" in patch:
            dirs = []
            for d in patch["models_dirs"] or []:
                d = str(d).strip()
                if d and not os.path.isabs(d):
                    raise LlamaCppError(f"Model folders must be absolute paths: {d}")
                if d and d not in dirs:
                    dirs.append(d)
            cfg["models_dirs"] = dirs[:20]
        if "ollama_models" in patch:
            cfg["ollama_models"] = bool(patch["ollama_models"])
        if "defaults" in patch:
            cfg["defaults"].update(clean_settings(patch["defaults"]))
        if "model" in patch and isinstance(patch["model"], dict):          # {"name": ..., "settings": {...} | null to reset}
            name, st = str(patch["model"].get("name") or ""), patch["model"].get("settings")
            if name:
                if st is None:
                    cfg["models"].pop(name, None)
                else:
                    cfg["models"][name] = clean_settings(st)
        if "last_model" in patch:
            cfg["last_model"] = str(patch["last_model"] or "")
        self.db.set_setting(SETTING, json.dumps(cfg))
        return cfg

    def effective(self, name: str) -> dict:
        cfg = self.config()
        return {**cfg["defaults"], **cfg["models"].get(name, {})}

    # ------------------------------------------------------------------ the llama-server program
    # How long a search result is trusted, and how soon a failed search is repeated (a program installed meanwhile is found within this time).
    FOUND_TTL = 300.0
    RETRY_TTL = 20.0

    def _valid(self, path: str) -> bool:
        return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)

    def _standard(self) -> list[tuple[str, str]]:
        """Places that are searched first and trusted as they are: the env variable, PATH and the usual install folders."""
        home = Path.home()
        out = [(os.environ.get("LLAMA_SERVER", ""), "LLAMA_SERVER"), (shutil.which("llama-server") or "", "PATH")]
        out += [(str(home / ".local/bin/llama-server"), "~/.local/bin"), ("/usr/local/bin/llama-server", "/usr/local/bin"),
                (str(home / "llama.cpp/build/bin/llama-server"), "~/llama.cpp"), (str(config.DATA_DIR / "llama.cpp/llama-server"), "Zeus-Agent folder")]
        return [(p, src) for p, src in out if self._valid(p) and not llamafind.is_ollama_path(p)]

    def candidates(self, force: bool = False) -> list[dict]:
        """Every usable llama-server on this computer with its version, build and the GPUs it can use, best first (what automatic mode would pick is
        marked `auto`). Cached; `force` searches again."""
        now = time.monotonic()
        if not force and self._found is not None and now - self._found_at < (self.FOUND_TTL if self._found else self.RETRY_TTL) and all(self._valid(c["path"]) for c in self._found):
            return self._found
        raw: list[dict] = [{"path": p, "source": src} for p, src in self._standard()]
        known = {os.path.realpath(r["path"]) for r in raw}
        for f in llamafind.discover(Path.home(), config.DATA_DIR):
            if f["real"] not in known:
                known.add(f["real"])
                raw.append({"path": f["path"], "source": f["source"]})
        out = []
        for r in raw[:8]:                                                   # probing runs the program briefly: a handful is plenty
            info = self.probe(r["path"])
            if info:
                out.append({"path": r["path"], "source": r["source"], "version": info.get("version"), "build": info.get("build"),
                            "devices": [d["name"] for d in info.get("devices", [])], "gpu": bool(info.get("devices"))})
        out.sort(key=lambda c: (not c["gpu"], -(c["build"] or 0)))        # a build that can use the GPU first, then the newest
        for i, c in enumerate(out):
            c["auto"] = i == 0
        self._found, self._found_at = out, now
        return out

    def rescan(self) -> list[dict]:
        self._found = None
        return self.candidates(force=True)

    def configured_problem(self) -> str | None:
        """Set when a path was entered in the settings but is not a usable program (automatic mode then takes over)."""
        b = self.config()["binary"]
        return f"The path you set ({b}) does not exist or is not executable; using the automatically found one." if b and not self._valid(b) else None

    def binary(self) -> str | None:
        """The program to use: the path set in the settings if it works, otherwise the best one found (see `candidates`)."""
        b = self.config()["binary"]
        if self._valid(b):
            return b
        found = self.candidates()
        return found[0]["path"] if found else None

    def _run(self, args: list[str], timeout: float = 12.0) -> str:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return ""
        return (r.stdout or "") + (r.stderr or "")

    def probe(self, path: str | None = None) -> dict:
        """Facts about the binary, cached per file: version text, build number, flags it supports, GPU devices it can see."""
        b = path or self.binary()
        if not b:
            return {}
        try:
            st = os.stat(b)
        except OSError:
            return {}
        key = (b, st.st_mtime_ns, st.st_size)
        if key not in self._probe:
            ver = self._run([b, "--version"])
            m = re.search(r"build\s+(\d+)", ver) or re.search(r"version:\s*(\d+)\b", ver)
            helptext = self._run([b, "--help"], 20.0)
            devs = []
            for line in self._run([b, "--list-devices"]).splitlines():
                d = re.match(r"\s*([A-Za-z]+\d*):\s*(.+?)\s*\((\d+)\s*MiB(?:,\s*(\d+)\s*MiB free)?\)", line)
                if d and not d.group(1).upper().startswith("CPU"):
                    devs.append({"id": d.group(1), "name": d.group(2), "memory_mib": int(d.group(3)), "free_mib": int(d.group(4)) if d.group(4) else None})
            self._probe[key] = {"binary": b, "version": f"b{m.group(1)}" if m else (ver.strip().splitlines()[0][:80] if ver.strip() else None),
                                "build": int(m.group(1)) if m else None,
                                "caps": {"jinja": "--jinja" in helptext or not helptext, "fit": "--fit" in helptext, "flash_attn": "--flash-attn" in helptext},
                                "devices": devs}
        return self._probe[key]

    # ------------------------------------------------------------------ models
    def _roots(self) -> list[Path]:
        seen, out = set(), []
        for d in [*default_model_dirs(), *map(Path, self.config()["models_dirs"])]:
            r = str(d)
            if r not in seen and d.is_dir():
                seen.add(r)
                out.append(d)
        return out

    def scan(self) -> list[dict]:
        """Every usable .gguf file in the model folders (embedding-free names, first shard of split models, no mmproj files)."""
        found: dict[str, dict] = {}
        for root in self._roots():
            base = len(root.parts)
            for dirpath, dirs, files in os.walk(root, followlinks=True):
                if len(Path(dirpath).parts) - base >= MAX_SCAN_DEPTH:
                    dirs[:] = []
                for fn in sorted(files):
                    if not fn.lower().endswith(".gguf") or "mmproj" in fn.lower():
                        continue
                    sm = SHARD_RE.search(fn)
                    if sm and sm.group(1) != "00001":
                        continue
                    p = Path(dirpath) / fn
                    try:
                        real = str(p.resolve())
                    except OSError:
                        continue
                    if real in found:
                        continue
                    info = gguf.describe(p)
                    if info.get("error"):
                        continue
                    stem = SHARD_RE.sub("", fn) if sm else fn[:-5]
                    found[real] = {"name": stem, "path": str(p), "root": str(root), "info": info, "shard": bool(sm)}
        # make names unique: a second file with the same name is listed as folder/name
        counts: dict[str, int] = {}
        for e in found.values():
            counts[e["name"]] = counts.get(e["name"], 0) + 1
        rows = []
        for real, e in found.items():
            name = e["name"] if counts[e["name"]] == 1 else f"{Path(e['path']).parent.name}/{e['name']}"
            i = e["info"]
            try:
                mtime = datetime.fromtimestamp(Path(e["path"]).stat().st_mtime, timezone.utc).isoformat()
            except OSError:
                mtime = None
            src = self.source_of(e["path"])
            rows.append({"name": name, "path": e["path"], "size": i.get("size", 0), "modified": mtime, "family": i.get("architecture"),
                         "parameter_size": i.get("size_label"), "quantization": i.get("quantization"), "context_length": i.get("context_length"),
                         "tools": i.get("tools", False), "thinking": i.get("thinking", False), "embedding": i.get("embedding", False),
                         "managed": src == "managed", "source": src, "removable": self.removal_plan(e["path"], src)})
        if self.config()["ollama_models"]:                                  # models Ollama already downloaded: the GGUF blob is used as it is (read only)
            have = set(found)
            for o in ollamastore.scan():
                if o["path"] in have:
                    continue
                i = gguf.describe(o["path"])
                if i.get("error"):
                    continue
                try:
                    mtime = datetime.fromtimestamp(Path(o["path"]).stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    mtime = None
                rows.append({"name": OLLAMA_PREFIX + o["name"], "path": o["path"], "size": o["size"], "modified": mtime, "family": i.get("architecture"),
                             "parameter_size": i.get("size_label"), "quantization": i.get("quantization"), "context_length": i.get("context_length"),
                             "tools": i.get("tools", False), "thinking": i.get("thinking", False), "embedding": i.get("embedding", False),
                             "managed": False, "source": "ollama", "removable": None, "ollama_name": o["name"], "also": o["also"], "projector": o["projector"]})
        return sorted(rows, key=lambda r: r["name"].lower())

    # ------------------------------------------------------------------ where a model file came from, and whether Zeus-Agent may delete it
    def _cache_roots(self) -> dict[str, Path]:
        home = Path.home()
        return {"managed": managed_dir(), "llama_cache": home / ".cache" / "llama.cpp", "hf_cache": home / ".cache" / "huggingface" / "hub"}

    def source_of(self, path: str) -> str:
        """managed (downloaded by Zeus-Agent), hf_cache (Hugging Face's cache), llama_cache (llama.cpp's own cache) or folder (the user's)."""
        for kind, root in self._cache_roots().items():
            for cand in (path, os.path.realpath(path)):
                try:
                    if os.path.commonpath([os.path.abspath(cand), str(root.resolve() if root.exists() else root)]) == str(root.resolve() if root.exists() else root):
                        return kind
                except ValueError:
                    continue
        return "folder"

    def removal_plan(self, path: str, source: str) -> dict | None:
        """What deleting this model would remove: {"kind", "files": [...], "bytes"}, or None when Zeus-Agent must not delete it (a user's folder, Ollama's store)."""
        p = Path(path)
        try:
            if source in ("managed", "llama_cache"):
                files = [p]
                sm = SHARD_RE.search(p.name)
                if sm:                                                       # a split model: every part goes
                    base = SHARD_RE.sub("", p.name)
                    files = sorted(f for f in p.parent.glob("*.gguf") if SHARD_RE.sub("", f.name) == base)
                return {"kind": source, "files": [str(f) for f in files], "bytes": sum(f.stat().st_size for f in files if f.is_file())}
            if source == "hf_cache":
                blob = Path(os.path.realpath(p))
                files = [p]
                size = 0
                repo = blob.parent.parent
                if p.is_symlink() and blob.parent.name == "blobs" and repo.name.startswith("models--"):
                    others = [q for q in (repo / "snapshots").rglob("*") if q != p and q.is_symlink() and os.path.realpath(q) == str(blob)]
                    if not others:                                           # the blob goes only when no other snapshot file uses it
                        files.append(blob)
                        size = blob.stat().st_size
                elif p.is_file():
                    size = p.stat().st_size
                return {"kind": source, "files": [str(f) for f in files], "bytes": size}
        except OSError:
            return None
        return None

    def resolve(self, name: str) -> dict | None:
        for r in self.scan():
            if r["name"] == name:
                return r
        if name and os.path.isabs(name) and name.lower().endswith(".gguf") and os.path.isfile(name):
            i = gguf.describe(name)
            if not i.get("error"):
                return {"name": Path(name).stem, "path": name, "size": i.get("size", 0), "context_length": i.get("context_length"),
                        "tools": i.get("tools", False), "thinking": i.get("thinking", False), "embedding": i.get("embedding", False)}
        return None

    # ------------------------------------------------------------------ the same surface as the Ollama provider
    async def status(self) -> dict:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, self.probe)
        if not info:
            return {"connected": False, "endpoint": "", "error": "llama-server was not found. Install llama.cpp and set its path in the llama.cpp settings.",
                    "running": False, "model": None}
        return {"connected": True, "version": info.get("version"), "endpoint": self.base_url or "starts when you pick a model", "binary": info["binary"],
                "running": _alive(self.proc) and self.ready, "model": self.model if _alive(self.proc) and self.ready else None, "devices": info.get("devices", []),
                "starting": self.starting}

    async def list_models(self) -> list[dict]:
        return await asyncio.get_running_loop().run_in_executor(None, self.scan)

    async def show(self, name: str) -> dict:
        r = await asyncio.get_running_loop().run_in_executor(None, self.resolve, name)
        if not r:
            raise LlamaCppError(f"model '{name}' was not found in your model folders")
        i = await asyncio.get_running_loop().run_in_executor(None, gguf.describe, r["path"])
        caps = ["embedding"] if i.get("embedding") else ["completion"]
        if i.get("tools"):
            caps.append("tools")
        if i.get("thinking"):
            caps.append("thinking")
        return {"name": name, "architecture": i.get("architecture", ""), "context_length": i.get("context_length"), "template": i.get("template", ""),
                "block_count": i.get("block_count"), "kv_heads": i.get("kv_heads"), "head_dim": i.get("head_dim"), "capabilities": caps, "parameters": "",
                "details": {"family": i.get("architecture"), "parameter_size": i.get("size_label"), "quantization_level": i.get("quantization")}}

    async def running(self) -> list[dict]:
        if not _alive(self.proc) or not self.ready or not self.model:
            return []
        size = Path(self.path).stat().st_size if self.path and os.path.exists(self.path) else 0
        return [{"name": self.model, "model": self.model, "size": size, "size_vram": 0, "context_length": self.ctx}]

    async def unload(self, name: str | None = None) -> None:
        async with self.lock:
            if name is None or name == self.model:
                await self._stop_locked()

    async def delete(self, name: str) -> dict:
        """Delete a model Zeus-Agent downloaded, or one in Hugging Face's / llama.cpp's cache. Never a model in the user's own folders, never Ollama's."""
        r = self.resolve(name)
        if not r:
            raise LlamaCppError(f"model '{name}' was not found")
        if r["source"] == "ollama":
            raise LlamaCppError(f"{r['ollama_name']} belongs to Ollama, and Zeus-Agent only reads Ollama's store. Remove it in Ollama (Models window, or `ollama rm {r['ollama_name']}`).")
        plan = r.get("removable")
        if not plan:
            raise LlamaCppError(f"Zeus-Agent only deletes models it downloaded or that sit in a download cache. This file is in your own folder; remove it by hand: {r['path']}")
        root = self._cache_roots()[plan["kind"]]
        if self.model == name or self.path == r["path"]:
            await self.unload(self.model or name)
        removed, freed = [], 0
        for f in plan["files"]:
            fp = Path(f)
            if not self._deletable_file(fp, plan["kind"], root):                # re-checked at the moment of deleting, not trusted from the plan
                continue
            try:
                size = 0 if fp.is_symlink() else fp.stat().st_size
                fp.unlink()
                removed.append(f)
                freed += size
            except OSError as e:
                raise LlamaCppError(f"could not delete {fp.name}: {e}")
        if plan["kind"] == "hf_cache":
            repo = self.hf_repo_of(Path(plan["files"][0]))
            if repo:
                self._prune_hf_repo(repo)
        return {"removed": removed, "bytes": freed}

    @staticmethod
    def hf_repo_of(path: Path) -> Path | None:
        """The `models--owner--repo` folder of a Hugging Face cache file, or None."""
        for parent in path.parents:
            if parent.name.startswith("models--"):
                return parent
        return None

    def _deletable_file(self, fp: Path, kind: str, root: Path) -> bool:
        """A model file inside the cache folder it was found in (or, for Hugging Face, that cache entry's blob). Anything else is never deleted."""
        try:
            if not (fp.is_symlink() or fp.is_file()) or not root.exists():
                return False
            inside = os.path.commonpath([os.path.realpath(fp.parent), str(root.resolve())]) == str(root.resolve())
        except (OSError, ValueError):
            return False
        if not inside:
            return False
        if kind == "hf_cache" and fp.parent.name == "blobs":
            return not fp.is_symlink() and (fp.parent.parent.name.startswith("models--"))
        return fp.name.lower().endswith(".gguf")

    @staticmethod
    def _prune_hf_repo(repo: Path) -> None:
        """After the last model file of a Hugging Face cache entry is gone, remove the entry's empty folders and its refs, so no husk is left."""
        try:
            if not repo.name.startswith("models--") or repo.is_symlink() or not repo.is_dir():
                return
            if any(f.is_file() or f.is_symlink() for f in (repo / "snapshots").rglob("*")) or any((repo / "blobs").glob("*")):
                return
            for sub in ("snapshots", "refs", "blobs", ".no_exist"):
                d = repo / sub
                if d.is_dir() and not d.is_symlink():
                    for dirpath, dirs, files in os.walk(d, topdown=False):
                        for fn in files:
                            if sub == "refs":
                                os.unlink(os.path.join(dirpath, fn))
                        try:
                            os.rmdir(dirpath)
                        except OSError:
                            pass
            repo.rmdir()
        except OSError:
            pass

    async def chat_once(self, model: str, messages: list[dict], num_ctx: int | None = None, options: dict | None = None, think=None) -> str:
        out = ""
        async for chunk in self.chat_stream(model, messages, None, num_ctx, options, think):
            out += chunk.get("message", {}).get("content", "") or ""
        return out

    # ------------------------------------------------------------------ the server process
    def log_tail(self, n: int = 40) -> str:
        try:
            data = self.log_path.read_text("utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(data.splitlines()[-n:])

    async def _stop_locked(self) -> None:
        proc, self.proc = self.proc, None
        if _alive(proc):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), 8.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                await proc.wait()
        self.model = self.path = self.sig = self.port = None
        self.ctx = 0
        self.started_at = None
        self.ready = False

    async def shutdown(self) -> None:
        async with self.lock:
            await self._stop_locked()

    async def _healthy(self, port: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"http://127.0.0.1:{port}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure(self, name: str, want_ctx: int | None = None) -> str:
        """Make sure llama-server is running the named model with enough context; returns its base URL."""
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, self.probe)
        binary = info.get("binary") if info else None
        if not binary:
            raise LlamaCppError("llama-server was not found. Install llama.cpp (see the llama.cpp settings, Help me choose) and set its path.")
        r = await loop.run_in_executor(None, self.resolve, name)
        if not r:
            raise LlamaCppError(f"The model '{name}' was not found in your model folders. Open the llama.cpp settings to add a folder or download one.")
        s = self.effective(name)
        trained = r.get("context_length") or 0
        ctx = int(s.get("ctx") or want_ctx or 8192)
        if trained and not s.get("ctx"):
            ctx = min(ctx, trained)
        sig = (r["path"], str(s.get("ngl")), s.get("threads"), s.get("flash_attn"), s.get("cache_type_k"), s.get("cache_type_v"), s.get("batch"), s.get("ubatch"), s.get("extra_args"))
        while True:
            async with self.lock:
                ok = _alive(self.proc) and self.ready and self.sig == sig and self.port is not None and (self.ctx >= ctx if not s.get("ctx") else self.ctx == ctx)
                if ok and await self._healthy(self.port):
                    return self.base_url
                if self.busy and _alive(self.proc):          # another conversation is mid-answer on the loaded model: wait for it
                    pass
                else:
                    await self._stop_locked()
                    return await self._start_locked(binary, info, r, s, ctx, sig)
            await asyncio.sleep(0.4)

    async def _start_locked(self, binary: str, info: dict, r: dict, s: dict, ctx: int, sig: tuple) -> str:
        port = _free_port()
        args = build_args(binary, r["path"], port, ctx, s, info.get("caps", {}), bool(info.get("devices")), r["name"])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(self.log_path, "wb")
        log.write(("$ " + " ".join(shlex.quote(a) for a in args) + "\n").encode())
        log.flush()
        self.starting = r["name"]
        try:
            try:
                self.proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            except OSError as e:
                raise LlamaCppError(f"could not start llama-server: {e}")
            finally:
                log.close()
            self.port, self.model, self.path, self.sig, self.ctx, self.started_at = port, r["name"], r["path"], sig, ctx, time.time()
            deadline = time.monotonic() + LOAD_TIMEOUT
            while time.monotonic() < deadline:
                if self.proc.returncode is not None:
                    tail = self.log_tail(12)
                    await self._stop_locked()
                    raise LlamaCppError("llama-server stopped while loading the model.\n" + tail)
                if await self._healthy(port):
                    self.ready = True
                    return self.base_url
                await asyncio.sleep(0.4)
            tail = self.log_tail(8)
            await self._stop_locked()
            raise LlamaCppError("llama-server did not become ready in time.\n" + tail)
        except asyncio.CancelledError:
            await self._stop_locked()
            raise
        finally:
            self.starting = None

    def server_status(self) -> dict:
        alive = _alive(self.proc) and self.ready
        return {"running": alive, "starting": self.starting, "model": self.model if alive else None, "path": self.path if alive else None,
                "port": self.port if alive else None, "pid": self.proc.pid if alive else None, "ctx": self.ctx if alive else None,
                "uptime": round(time.time() - self.started_at) if alive and self.started_at else None, "busy": self.busy}

    # ------------------------------------------------------------------ chat
    async def chat_stream(self, model: str, messages: list[dict], tools: list[dict] | None, num_ctx: int | None,
                          options: dict | None = None, think=None) -> AsyncIterator[dict]:
        url = await self.ensure(model, num_ctx)
        opts = {"temperature": 0.2, **(options or {})}
        temperature = opts.pop("temperature")

        def build(strict: bool) -> dict:
            # No tools sent = the model works with the text tool protocol: give it a plain, alternating history. Native tool calling keeps the OpenAI shape.
            msgs = flatten_messages(messages, fold_system=strict) if (strict or not tools) else to_openai_messages(messages)
            body: dict = {"model": model, "messages": msgs, "stream": True, "stream_options": {"include_usage": True}, "temperature": temperature}
            for k, dst in (("top_p", "top_p"), ("top_k", "top_k"), ("min_p", "min_p"), ("repeat_penalty", "repeat_penalty"), ("seed", "seed"), ("num_predict", "max_tokens")):
                if opts.get(k) is not None:
                    body[dst] = opts[k]
            if tools:
                body["tools"] = tools
            if think is False:
                body["chat_template_kwargs"] = {"enable_thinking": False}
            return body
        self.busy += 1
        try:
            for strict in (False, True):
                try:
                    async for chunk in self._stream(url, build(strict), retry_ok=not strict):
                        yield chunk
                    return
                except _TemplateRejected:
                    continue                                   # this model's chat template is picky: once more as plain, strictly alternating turns with the system prompt folded in
        except httpx.HTTPError as e:
            raise LlamaCppError(f"lost the connection to llama-server: {str(e) or type(e).__name__}\n{self.log_tail(6)}")
        finally:
            self.busy = max(0, self.busy - 1)

    async def _stream(self, url: str, body: dict, retry_ok: bool) -> AsyncIterator[dict]:
        """One request to llama-server, translated to the Ollama chunk shape. Raises _TemplateRejected (before yielding anything) when `retry_ok` and the chat template refuses the messages."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=5.0)) as c:
            async with c.stream("POST", f"{url}/v1/chat/completions", json=body) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode(errors="replace")
                    try:
                        err = json.loads(text).get("error", text)
                        text = err.get("message", text) if isinstance(err, dict) else str(err)
                    except Exception:
                        pass
                    if retry_ok and r.status_code == 400 and TEMPLATE_COMPLAINT.search(text):
                        raise _TemplateRejected(text)
                    raise LlamaCppError(f"llama-server error {r.status_code}: {text[:600]}")
                pending: dict[int, dict] = {}
                usage, timings, finish = None, None, None
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(obj.get("error"), (dict, str)):
                        e = obj["error"]
                        raise LlamaCppError(str(e.get("message") if isinstance(e, dict) else e))
                    usage = obj.get("usage") or usage
                    timings = obj.get("timings") or timings
                    for ch in obj.get("choices") or []:
                        d = ch.get("delta") or {}
                        if d.get("reasoning_content"):
                            yield {"message": {"role": "assistant", "content": "", "thinking": d["reasoning_content"]}}
                        if d.get("content"):
                            yield {"message": {"role": "assistant", "content": d["content"]}}
                        for tc in d.get("tool_calls") or []:
                            slot = pending.setdefault(int(tc.get("index", 0) or 0), {"name": "", "args": ""})
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"] if not slot["name"] else slot["name"] + fn["name"]
                            a = fn.get("arguments")
                            if a:
                                slot["args"] += a if isinstance(a, str) else json.dumps(a)
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
        calls = [{"function": {"name": v["name"], "arguments": parse_arguments(v["args"])}} for _, v in sorted(pending.items()) if v["name"]]
        usage, timings = usage or {}, timings or {}
        msg = {"role": "assistant", "content": ""}
        if calls:
            msg["tool_calls"] = calls
        yield {"message": msg, "done": True, "done_reason": finish, "prompt_eval_count": usage.get("prompt_tokens") or timings.get("prompt_n") or 0,
               "eval_count": usage.get("completion_tokens") or timings.get("predicted_n") or 0,
               "eval_duration": int((timings.get("predicted_ms") or 0) * 1e6)}

    # ------------------------------------------------------------------ Hugging Face: search, list and download GGUF files
    _search_cache: dict[str, tuple[float, dict]] = {}

    async def hf_search(self, q: str = "", limit: int = 20) -> dict:
        """GGUF model repositories on Hugging Face, the most downloaded first (or matching `q`). The only thing sent to huggingface.co is the text typed."""
        q = (q or "").strip()[:100]
        hit = self._search_cache.get(q)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        params = {"filter": "gguf", "sort": "downloads", "direction": "-1", "limit": str(max(1, min(limit, 40)))}
        if q:
            params["search"] = q
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": "zeus-agent"}) as c:
                r = await c.get(f"{HF_BASE}/api/models", params=params)
        except httpx.HTTPError as e:
            raise LlamaCppError(f"Could not reach huggingface.co ({type(e).__name__}).")
        if r.status_code >= 400:
            raise LlamaCppError(f"huggingface.co answered {r.status_code}.")
        results = []
        for m in r.json() if isinstance(r.json(), list) else []:
            rid = m.get("id") or m.get("modelId")
            if isinstance(rid, str) and HF_RE.match(rid):
                results.append({"repo": rid, "downloads": int(m.get("downloads") or 0), "likes": int(m.get("likes") or 0), "updated": m.get("lastModified")})
        out = {"query": q, "results": results}
        self._search_cache[q] = (time.time(), out)
        return out

    async def hf_files(self, repo: str) -> dict:
        """GGUF files of a Hugging Face repository with their sizes. Only the repository name is sent to huggingface.co."""
        if not HF_RE.match(repo or ""):
            raise LlamaCppError("Use the form owner/name, for example Qwen/Qwen2.5-Coder-7B-Instruct-GGUF.")
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": "zeus-agent"}) as c:
                r = await c.get(f"{HF_BASE}/api/models/{repo}", params={"blobs": "true"})
        except httpx.HTTPError as e:
            raise LlamaCppError(f"Could not reach huggingface.co ({type(e).__name__}).")
        if r.status_code == 404:
            raise LlamaCppError("That repository was not found on huggingface.co.")
        if r.status_code >= 400:
            raise LlamaCppError(f"huggingface.co answered {r.status_code}.")
        files, shards = [], {}
        for s in r.json().get("siblings", []):
            fn = s.get("rfilename", "")
            if not fn.lower().endswith(".gguf") or "mmproj" in fn.lower() or not HF_FILE_RE.match(fn):
                continue
            size = int(s.get("size") or 0)
            sm = SHARD_RE.search(fn)
            if sm:
                shards[SHARD_RE.sub("", fn)] = shards.get(SHARD_RE.sub("", fn), 0) + size
                continue
            files.append({"file": fn, "size": size, "quant": gguf.quantization_of({}, fn), "split": False})
        for base, total in shards.items():
            files.append({"file": base + ".gguf", "size": total, "quant": gguf.quantization_of({}, base), "split": True})
        have = {m["name"] for m in self.scan()}
        for f in files:
            f["installed"] = Path(f["file"]).stem in have or (managed_dir() / Path(f["file"]).name).exists()
        return {"repo": repo, "files": sorted(files, key=lambda f: (f["split"], f["size"])), "url": f"{HF_BASE}/{repo}"}

    async def pull_stream(self, model: str) -> AsyncIterator[dict]:
        """Download `hf:owner/repo/file.gguf` into Zeus-Agent's own model folder (resumable). Yields Ollama-style progress objects."""
        if not model.startswith("hf:"):
            raise LlamaCppError("Models for llama.cpp are downloaded from Hugging Face (hf:owner/repo/file.gguf).")
        parts = model[3:].split("/", 2)
        if len(parts) != 3 or not HF_RE.match("/".join(parts[:2])) or not HF_FILE_RE.match(parts[2]) or ".." in parts[2]:
            raise LlamaCppError("invalid Hugging Face file name")
        repo, rel = "/".join(parts[:2]), parts[2]
        dest = managed_dir() / Path(rel).name
        part = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and gguf.describe(dest).get("error") is None:
            yield {"status": "success"}
            return
        have = part.stat().st_size if part.exists() else 0
        url = f"{HF_BASE}/{repo}/resolve/main/{rel}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=15.0), follow_redirects=True, headers={"User-Agent": "zeus-agent"}) as c:
                async with c.stream("GET", url, headers={"Range": f"bytes={have}-"} if have else {}) as r:
                    if r.status_code == 416:                      # the partial file is already complete
                        have = 0
                    if r.status_code >= 400 and r.status_code != 416:
                        raise LlamaCppError(f"huggingface.co answered {r.status_code} for this file (it may need you to accept a licence or log in).")
                    if r.status_code == 200 and have:             # the server ignored the range: start again
                        have = 0
                    total = have + int(r.headers.get("content-length") or 0)
                    free = shutil.disk_usage(dest.parent).free
                    if total and total - have > free - 256 * 2**20:
                        raise LlamaCppError(f"Not enough free disk space ({free / 2**30:.1f} GiB free, {(total - have) / 2**30:.1f} GiB needed).")
                    mode = "ab" if have else "wb"
                    done, last = have, 0.0
                    with open(part, mode) as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                            done += len(chunk)
                            if time.monotonic() - last > 0.5:
                                last = time.monotonic()
                                yield {"status": "downloading", "digest": dest.name, "total": total, "completed": done}
            yield {"status": "downloading", "digest": dest.name, "total": done, "completed": done}
        except httpx.HTTPError as e:
            raise LlamaCppError(f"download interrupted: {str(e) or type(e).__name__}. Press Retry to resume.")
        if gguf.describe(part).get("error"):
            part.unlink(missing_ok=True)
            raise LlamaCppError("The downloaded file is not a valid GGUF model.")
        os.replace(part, dest)
        yield {"status": "success"}
