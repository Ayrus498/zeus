"""Writes small, valid GGUF files for tests (metadata only; there are no tensors)."""
import struct
from pathlib import Path


def _s(x: str) -> bytes:
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def _kv(key: str, v) -> bytes:
    k = _s(key)
    if isinstance(v, bool):
        return k + struct.pack("<I?", 7, v)
    if isinstance(v, int):
        return k + struct.pack("<II", 4, v)
    if isinstance(v, float):
        return k + struct.pack("<If", 6, v)
    if isinstance(v, str):
        return k + struct.pack("<I", 8) + _s(v)
    if isinstance(v, list) and all(isinstance(i, str) for i in v):       # string array (a tokenizer vocabulary)
        return k + struct.pack("<IIQ", 9, 8, len(v)) + b"".join(_s(i) for i in v)
    if isinstance(v, list):                                              # int32 array
        return k + struct.pack("<IIQ", 9, 5, len(v)) + b"".join(struct.pack("<i", i) for i in v)
    raise TypeError(key)


def make_gguf(path, arch="qwen2", name="Test Model", size_label="1.5B", file_type=15, ctx=32768, template="{% if tools %}<tool_call>{% endif %}",
              extra=None, pad=0) -> Path:
    kvs = {"general.architecture": arch, "general.name": name, "general.size_label": size_label, "general.file_type": file_type,
           f"{arch}.context_length": ctx, f"{arch}.block_count": 28, f"{arch}.embedding_length": 1536, f"{arch}.attention.head_count": 12,
           f"{arch}.attention.head_count_kv": 2, "tokenizer.ggml.tokens": ["<a>", "<b>", "hello"], "tokenizer.ggml.token_type": [1, 1, 1]}
    if template is not None:
        kvs["tokenizer.chat_template"] = template
    kvs.update(extra or {})
    body = b"".join(_kv(k, v) for k, v in kvs.items())
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, len(kvs)) + body + b"\0" * pad)
    return p
