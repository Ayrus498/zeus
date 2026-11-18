"""Small pure helpers for formatting numbers and paths."""
from __future__ import annotations

import os
from pathlib import Path


def fmt_bytes(n: float | int | None) -> str:
    if n is None:
        return "unavailable"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_ctx(n: int | None) -> str:
    if not n:
        return "unknown"
    return f"{round(n / 1024)}K" if n >= 1024 and n % 1024 < 64 else f"{n:,}"


def fmt_num(n: int | None) -> str:
    return f"{n:,}" if n is not None else "unknown"


def short_path(p: str | Path, width: int = 48) -> str:
    s = str(p)
    home = str(Path.home())
    if s == home or s.startswith(home + os.sep):
        s = "~" + s[len(home):]
    if len(s) <= width:
        return s
    return "…" + s[-(width - 1):]


def clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_ctx(text: str) -> int | None:
    """'32k' -> 32768, '16384' -> 16384, '1m' -> 1048576. None when not a number."""
    t = text.strip().lower().replace(",", "").replace("_", "")
    mult = 1
    if t.endswith("k"):
        mult, t = 1024, t[:-1]
    elif t.endswith("m"):
        mult, t = 1024 * 1024, t[:-1]
    try:
        v = int(float(t) * mult)
    except ValueError:
        return None
    return v if 512 <= v <= 4_194_304 else None
