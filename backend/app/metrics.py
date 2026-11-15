"""Live resource metrics: system CPU/RAM, the Ollama process, GPUs, and `ollama ps` state.

Only real readings are reported (/proc, nvidia-smi, amdgpu sysfs). Anything unavailable is None - never invented.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path

_TICKS = os.sysconf("SC_CLK_TCK")
_NCPU = os.cpu_count() or 1


def _read_cpu_total() -> tuple[int, int] | None:
    try:
        f = open("/proc/stat").readline().split()[1:]
        v = [int(x) for x in f]
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        return sum(v[:8]), idle
    except Exception:
        return None


def _meminfo() -> dict:
    out = {}
    try:
        for l in open("/proc/meminfo"):
            k, _, rest = l.partition(":")
            out[k] = int(rest.split()[0]) * 1024
    except Exception:
        pass
    return out


def _ollama_pids() -> list[int]:
    pids = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            comm = Path(d, "comm").read_text().strip()
            # `ollama serve` plus its model runner, which is a separate process (ollama runner / llama-server)
            if comm.startswith("ollama") or ("llama" in comm and b"ollama" in Path(d, "cmdline").read_bytes()):
                pids.append(int(os.path.basename(d)))
        except Exception:
            continue
    return pids


def _proc_stat(pid: int) -> tuple[int, int] | None:
    """(cpu ticks, rss bytes)."""
    try:
        s = Path(f"/proc/{pid}/stat").read_text()
        f = s[s.rindex(")") + 2:].split()
        ticks = int(f[11]) + int(f[12])
        rss = int(f[21]) * os.sysconf("SC_PAGE_SIZE")
        return ticks, rss
    except Exception:
        return None


def _nvidia() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    q = "index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw"
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
    except Exception:
        return []
    gpus = []

    def num(x, cast=float):
        try:
            return cast(x)
        except Exception:
            return None
    for line in out:
        p = [x.strip() for x in line.split(",")]
        if len(p) < 8:
            continue
        mu, mt = num(p[4]), num(p[5])
        gpus.append({"vendor": "nvidia", "index": num(p[0], int), "name": p[1], "util_pct": num(p[2]), "mem_util_pct": num(p[3]),
                     "mem_used": int(mu * 2**20) if mu is not None else None, "mem_total": int(mt * 2**20) if mt is not None else None,
                     "temp_c": num(p[6]), "power_w": num(p[7])})
    return gpus


def _amd() -> list[dict]:
    gpus = []
    for d in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        try:
            if Path(d, "vendor").read_text().strip() != "0x1002":
                continue
            busy = Path(d, "gpu_busy_percent")
            used, total = Path(d, "mem_info_vram_used"), Path(d, "mem_info_vram_total")
            gpus.append({"vendor": "amd", "index": len(gpus), "name": "AMD GPU",
                         "util_pct": float(busy.read_text()) if busy.exists() else None, "mem_util_pct": None,
                         "mem_used": int(used.read_text()) if used.exists() else None,
                         "mem_total": int(total.read_text()) if total.exists() else None, "temp_c": None, "power_w": None})
        except Exception:
            continue
    return gpus


def processor_label(size: int, size_vram: int) -> str:
    """Mirrors the PROCESSOR column of `ollama ps`."""
    if not size:
        return "unknown"
    if size_vram >= size:
        return "100% GPU"
    if size_vram <= 0:
        return "100% CPU"
    gpu = round(size_vram * 100 / size)
    return f"{100 - gpu}%/{gpu}% CPU/GPU"


def describe_ps(models: list[dict]) -> list[dict]:
    out = []
    for m in models:
        size, vram = int(m.get("size") or 0), int(m.get("size_vram") or 0)
        out.append({"name": m.get("name") or m.get("model"), "size": size, "size_vram": vram,
                    "gpu_pct": round(vram * 100 / size) if size else None, "processor": processor_label(size, vram),
                    "context_length": m.get("context_length"), "expires_at": m.get("expires_at"),
                    "details": m.get("details") or {}})
    return out


class Sampler:
    """Stateful: CPU percentages are deltas between consecutive calls."""

    def __init__(self) -> None:
        self._cpu = _read_cpu_total()
        self._proc: dict[int, int] = {}
        self._t = time.monotonic()

    def sample(self) -> dict:
        now = time.monotonic()
        dt = max(now - self._t, 1e-3)
        self._t = now
        cur = _read_cpu_total()
        cpu_pct = None
        if cur and self._cpu:
            dtot, didle = cur[0] - self._cpu[0], cur[1] - self._cpu[1]
            if dtot > 0:
                cpu_pct = round(100 * (dtot - didle) / dtot, 1)
        self._cpu = cur

        proc_pct, rss, seen = 0.0, 0, {}
        pids = _ollama_pids()
        for pid in pids:
            st = _proc_stat(pid)
            if not st:
                continue
            seen[pid] = st[0]
            rss += st[1]
            if pid in self._proc:
                proc_pct += 100 * (st[0] - self._proc[pid]) / _TICKS / dt   # 100 = one full core
        first = not self._proc
        self._proc = seen

        mi = _meminfo()
        total, avail = mi.get("MemTotal"), mi.get("MemAvailable")
        gpus = _nvidia() or _amd()
        return {"ts": time.time(), "cpu": {"percent": cpu_pct, "cores": _NCPU, "load1": os.getloadavg()[0]},
                "ram": {"total": total, "used": (total - avail) if total and avail is not None else None},
                "ollama_proc": {"running": bool(pids), "cpu_percent": None if first or not pids else round(proc_pct, 1),
                                "cpu_percent_of_total": None if first or not pids else round(proc_pct / _NCPU, 1), "rss": rss if pids else None},
                "gpus": gpus}
