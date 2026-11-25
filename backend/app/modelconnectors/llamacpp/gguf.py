"""Reads the header metadata of a GGUF model file (architecture, quantization, trained context length, chat template, ...).

Only the key/value header is read, never the tensors, so a 20 GB file is described in milliseconds. Large arrays (the tokenizer
vocabulary) are skipped, not loaded. Results are cached per file (path, size, modification time).

Format: magic "GGUF", uint32 version (2 or 3), uint64 tensor count, uint64 key/value count, then the key/value pairs:
string key (uint64 length + bytes), uint32 value type, value. See the GGUF specification in the ggml repository.
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"GGUF"
_SCALAR = {0: ("<B", 1), 1: ("<b", 1), 2: ("<H", 2), 3: ("<h", 2), 4: ("<I", 4), 5: ("<i", 4), 6: ("<f", 4), 7: ("<?", 1), 10: ("<Q", 8), 11: ("<q", 8), 12: ("<d", 8)}
T_STRING, T_ARRAY = 8, 9
MAX_STRING = 1 << 20                 # a chat template is a few KB; refuse absurd strings in a corrupt file
WANTED_SUFFIXES = ("context_length", "block_count", "embedding_length", "attention.head_count", "attention.head_count_kv", "attention.key_length", "pooling_type")

# llama_ftype values -> the name people use for the quantization
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
              14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS",
              24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16"}
_QUANT_IN_NAME = re.compile(r"(IQ\d_[A-Z]+|Q\d_K(?:_[SML])?|Q\d_[01]|MXFP4|BF16|F16|F32)", re.I)
EMBEDDING_ARCHS = {"bert", "nomic-bert", "nomic-bert-moe", "jina-bert-v2", "jina-bert-v3", "modern-bert", "neo-bert", "t5encoder"}
_cache: dict[tuple[str, int, int], dict] = {}


class GGUFError(ValueError):
    pass


def _read(f: BinaryIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise GGUFError("file ends inside the header")
    return b


def _string(f: BinaryIO) -> str:
    n = struct.unpack("<Q", _read(f, 8))[0]
    if n > MAX_STRING:
        raise GGUFError("string too long")
    return _read(f, n).decode("utf-8", "replace")


def _skip_string(f: BinaryIO) -> None:
    n = struct.unpack("<Q", _read(f, 8))[0]
    f.seek(n, os.SEEK_CUR)


def _value(f: BinaryIO, t: int, keep: bool) -> Any:
    if t in _SCALAR:
        fmt, size = _SCALAR[t]
        raw = _read(f, size)
        return struct.unpack(fmt, raw)[0] if keep else None
    if t == T_STRING:
        if keep:
            return _string(f)
        _skip_string(f)
        return None
    if t == T_ARRAY:
        et = struct.unpack("<I", _read(f, 4))[0]
        n = struct.unpack("<Q", _read(f, 8))[0]
        if et in _SCALAR:
            f.seek(n * _SCALAR[et][1], os.SEEK_CUR)
        elif et == T_STRING:
            for _ in range(n):
                _skip_string(f)
        else:
            raise GGUFError(f"unsupported array element type {et}")
        return None
    raise GGUFError(f"unknown value type {t}")


def read_header(path: str | Path) -> dict[str, Any]:
    """The key/value metadata we use, plus `_version`, `_tensors`. Raises GGUFError for files that are not GGUF."""
    with open(path, "rb") as f:
        if _read(f, 4) != MAGIC:
            raise GGUFError("not a GGUF file")
        version = struct.unpack("<I", _read(f, 4))[0]
        if version not in (2, 3):
            raise GGUFError(f"unsupported GGUF version {version}")
        tensors, count = struct.unpack("<QQ", _read(f, 16))
        if count > 100_000:
            raise GGUFError("implausible metadata size")
        out: dict[str, Any] = {"_version": version, "_tensors": tensors}
        for _ in range(count):
            key = _string(f)
            t = struct.unpack("<I", _read(f, 4))[0]
            keep = key.startswith("general.") or key == "tokenizer.chat_template" or key.endswith(WANTED_SUFFIXES)
            v = _value(f, t, keep)
            if keep and v is not None:
                out[key] = v
        return out


def quantization_of(meta: dict, filename: str = "") -> str | None:
    ft = meta.get("general.file_type")
    if isinstance(ft, int) and ft in FILE_TYPES:
        return FILE_TYPES[ft]
    m = _QUANT_IN_NAME.search(filename)
    return m.group(1).upper() if m else None


def describe(path: str | Path) -> dict[str, Any]:
    """What Zeus-Agent needs to know about a model file. Cached per (path, size, mtime); never raises (returns `{"error": ...}`)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError as e:
        return {"error": str(e)}
    key = (str(p), st.st_size, int(st.st_mtime))
    if key in _cache:
        return _cache[key]
    try:
        meta = read_header(p)
    except (GGUFError, OSError, struct.error) as e:
        return {"error": str(e), "size": st.st_size}
    arch = str(meta.get("general.architecture") or "")

    def num(suffix):
        v = meta.get(f"{arch}.{suffix}")
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    heads, emb = num("attention.head_count"), num("embedding_length")
    kv = num("attention.head_count_kv") or heads
    template = meta.get("tokenizer.chat_template") if isinstance(meta.get("tokenizer.chat_template"), str) else ""
    low = template.lower()
    embedding = arch in EMBEDDING_ARCHS or num("pooling_type") not in (None, 0) and not template
    out = {
        "name": meta.get("general.name") if isinstance(meta.get("general.name"), str) else None,
        "architecture": arch, "size_label": meta.get("general.size_label") if isinstance(meta.get("general.size_label"), str) else None,
        "quantization": quantization_of(meta, p.name), "context_length": int(num("context_length")) if num("context_length") else None,
        "block_count": int(num("block_count")) if num("block_count") else None, "kv_heads": int(kv) if kv else None,
        "head_dim": int(num("attention.key_length") or (emb / heads if emb and heads else 0)) or None,
        "embedding_length": int(emb) if emb else None, "template": template, "size": st.st_size,
        "tools": bool(template) and ("tool" in low), "thinking": "<think>" in low or "enable_thinking" in low, "embedding": bool(embedding),
    }
    _cache[key] = out
    return out
