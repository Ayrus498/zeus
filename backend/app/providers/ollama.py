"""Ollama provider: model discovery, metadata, streaming chat."""
from __future__ import annotations

import json
import os
import shutil
from typing import AsyncIterator

import httpx


class OllamaError(Exception):
    pass


class OllamaProvider:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _client(self, timeout=10.0):
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def status(self) -> dict:
        try:
            async with self._client(3.0) as c:
                r = await c.get("/api/version")
                r.raise_for_status()
                return {"connected": True, "version": r.json().get("version"), "endpoint": self.base_url}
        except Exception as e:
            return {"connected": False, "endpoint": self.base_url, "error": str(e) or type(e).__name__}

    async def list_models(self) -> list[dict]:
        try:
            async with self._client() as c:
                r = await c.get("/api/tags")
                r.raise_for_status()
        except Exception as e:
            raise OllamaError(f"cannot reach Ollama at {self.base_url}: {e}")
        out = []
        for m in r.json().get("models", []):
            d = m.get("details", {})
            out.append({"name": m["name"], "size": m.get("size", 0), "modified": m.get("modified_at"),
                        "family": d.get("family"), "parameter_size": d.get("parameter_size"),
                        "quantization": d.get("quantization_level")})
        return out

    async def show(self, name: str) -> dict:
        """Metadata incl. native context length, capabilities and KV-cache geometry."""
        try:
            async with self._client(20.0) as c:
                r = await c.post("/api/show", json={"model": name})
                if r.status_code == 404:
                    raise OllamaError(f"model '{name}' is not installed (try: ollama pull {name})")
                r.raise_for_status()
        except OllamaError:
            raise
        except Exception as e:
            raise OllamaError(str(e))
        j = r.json()
        info = j.get("model_info", {})
        arch = info.get("general.architecture", "")

        def g(suffix):
            v = info.get(f"{arch}.{suffix}")
            return v if isinstance(v, (int, float)) else None

        heads, kv = g("attention.head_count"), g("attention.head_count_kv") or g("attention.head_count")
        emb = g("embedding_length")
        key_len = g("attention.key_length") or (emb / heads if emb and heads else None)
        return {"name": name, "architecture": arch, "context_length": g("context_length"), "template": j.get("template", ""),
                "block_count": g("block_count"), "kv_heads": kv, "head_dim": key_len,
                "capabilities": j.get("capabilities", []), "parameters": j.get("parameters", ""),
                "details": j.get("details", {})}

    async def running(self) -> list[dict]:
        try:
            async with self._client() as c:
                r = await c.get("/api/ps")
                return r.json().get("models", [])
        except Exception:
            return []

    async def chat_once(self, model: str, messages: list[dict], num_ctx: int | None = None, options: dict | None = None, think=None) -> str:
        """Non-agent call (planning / verification): returns the final content only, thinking text excluded."""
        out = ""
        async for chunk in self.chat_stream(model, messages, None, num_ctx, options, think):
            out += chunk.get("message", {}).get("content", "") or ""
        return out

    async def pull_stream(self, model: str) -> AsyncIterator[dict]:
        """Streams /api/pull progress objects: {status, digest?, total?, completed?} or raises OllamaError."""
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(None, connect=10.0)) as c:
                async with c.stream("POST", "/api/pull", json={"model": model, "stream": True}) as r:
                    if r.status_code >= 400:
                        body = (await r.aread()).decode("utf-8", "replace")
                        try:
                            body = json.loads(body).get("error", body)
                        except Exception:
                            pass
                        raise OllamaError(body.strip() or f"HTTP {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if obj.get("error"):
                            raise OllamaError(str(obj["error"]))
                        yield obj
        except OllamaError:
            raise
        except httpx.HTTPError as e:
            raise OllamaError(f"connection to Ollama lost: {str(e) or type(e).__name__}")

    async def unload(self, model: str) -> None:
        """Ask Ollama to free the model from memory now (keep_alive=0)."""
        try:
            async with self._client(30.0) as c:
                r = await c.post("/api/generate", json={"model": model, "keep_alive": 0})
                if r.status_code >= 400:
                    try:
                        msg = r.json().get("error", r.text)
                    except Exception:
                        msg = r.text
                    raise OllamaError(msg)
        except OllamaError:
            raise
        except Exception as e:
            raise OllamaError(f"cannot reach Ollama: {str(e) or type(e).__name__}")

    async def chat_stream(self, model: str, messages: list[dict], tools: list[dict] | None, num_ctx: int | None,
                          options: dict | None = None, think=None) -> AsyncIterator[dict]:
        """`options`: sampling parameters (temperature, top_p, top_k, ...); `think`: True/False/"low"|"medium"|"high" for
        models that report the `thinking` capability (never send it to others: Ollama rejects it)."""
        body = {"model": model, "messages": messages, "stream": True, "options": {"temperature": 0.2, **(options or {})}}
        if tools:
            body["tools"] = tools
        if num_ctx:
            body["options"]["num_ctx"] = num_ctx
        if think is not None:
            body["think"] = think
        async with httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(600.0, connect=5.0)) as c:
            async with c.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode(errors="replace")
                    try:
                        text = json.loads(text).get("error", text)
                    except Exception:
                        pass
                    raise OllamaError(f"Ollama error {r.status_code}: {text}")
                async for line in r.aiter_lines():
                    if line.strip():
                        j = json.loads(line)
                        if "error" in j:
                            raise OllamaError(j["error"])
                        yield j


def kv_bytes_per_token(meta: dict) -> float | None:
    """f16 KV cache: 2 (K+V) * layers * kv_heads * head_dim * 2 bytes."""
    try:
        return 2 * meta["block_count"] * meta["kv_heads"] * meta["head_dim"] * 2
    except Exception:
        return None


def system_memory() -> dict:
    """Free GPU VRAM (nvidia-smi) and available RAM, in bytes. Missing values are None."""
    gpu = None
    if shutil.which("nvidia-smi"):
        import subprocess
        try:
            o = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.free,name", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5).stdout.strip().splitlines()[0].split(",")
            gpu = {"total": int(o[0]) * 2**20, "free": int(o[1]) * 2**20, "name": o[2].strip()}
        except Exception:
            gpu = None
    ram = None
    try:
        for l in open("/proc/meminfo"):
            if l.startswith("MemAvailable:"):
                ram = int(l.split()[1]) * 1024
    except Exception:
        pass
    return {"gpu": gpu, "ram_available": ram}


def context_warning(meta: dict, model_size: int, num_ctx: int) -> dict:
    """Estimate (clearly labelled) whether model weights + KV cache fit in memory."""
    bpt = kv_bytes_per_token(meta)
    mem = system_memory()
    if bpt is None:
        return {"estimate": True, "warning": None, "kv_bytes": None, "memory": mem}
    kv = int(bpt * num_ctx)
    need = model_size + kv
    gpu_total = mem["gpu"]["total"] if mem["gpu"] else 0
    ram = mem["ram_available"] or 0
    warning = None
    if mem["gpu"] and need > gpu_total:
        if need > gpu_total + ram:
            warning = f"Estimated {need/2**30:.1f} GiB needed (model + KV cache) exceeds GPU VRAM and available RAM; this may fail or be very slow."
        else:
            warning = f"Estimated {need/2**30:.1f} GiB needed exceeds GPU VRAM ({gpu_total/2**30:.1f} GiB); layers will spill to CPU and run slower."
    elif not mem["gpu"] and ram and need > ram:
        warning = f"Estimated {need/2**30:.1f} GiB needed exceeds available RAM ({ram/2**30:.1f} GiB)."
    return {"estimate": True, "warning": warning, "kv_bytes": kv, "needed_bytes": need, "memory": mem}
