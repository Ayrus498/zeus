"""About / device information for the Settings menu. Only real readings; anything unavailable is None."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, metrics


def cpu_info(cpuinfo: str | None = None) -> dict:
    """Model name, physical cores and logical threads from /proc/cpuinfo text."""
    if cpuinfo is None:
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(errors="replace")
        except Exception:
            cpuinfo = ""
    name = None
    pairs: set[tuple[str, str]] = set()
    phys = core = None
    for line in cpuinfo.splitlines() + [""]:
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k == "model name" and not name:
            name = re.sub(r"\s+", " ", v)
        elif k == "Hardware" and not name:
            name = v
        elif k == "physical id":
            phys = v
        elif k == "core id":
            core = v
        elif not line.strip():
            if core is not None:
                pairs.add((phys or "0", core))
            phys = core = None
    return {"name": name, "cores": len(pairs) or None, "threads": os.cpu_count()}


def os_name(osrelease: str | None = None) -> str:
    if osrelease is None:
        try:
            osrelease = Path("/etc/os-release").read_text()
        except Exception:
            osrelease = ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', osrelease, re.M)
    return m.group(1) if m else platform.system()


def lspci_gpus(text: str) -> list[str]:
    """GPU names from `lspci -mm` output (used when nvidia-smi is not available)."""
    names = []
    for line in text.splitlines():
        if re.search(r'"(VGA compatible controller|3D controller|Display controller)"', line):
            parts = re.findall(r'"([^"]*)"', line)
            if len(parts) >= 3:
                names.append(f"{parts[1]} {parts[2]}".strip())
    return names


def gpu_list() -> list[dict]:
    gpus = metrics._nvidia() or metrics._amd()
    out = [{"name": g["name"], "vendor": g["vendor"], "vram_total": g.get("mem_total"), "vram_used": g.get("mem_used")} for g in gpus]
    if (not out or all(g["name"] == "AMD GPU" for g in out)) and shutil.which("lspci"):
        try:
            names = lspci_gpus(subprocess.run(["lspci", "-mm"], capture_output=True, text=True, timeout=3).stdout)
        except Exception:
            names = []
        if out:
            for g, n in zip(out, names):
                g["name"] = n
        else:
            out = [{"name": n, "vendor": None, "vram_total": None, "vram_used": None} for n in names]
    return out


def memory_info() -> dict:
    mi = metrics._meminfo()
    total, avail = mi.get("MemTotal"), mi.get("MemAvailable")
    st, sf = mi.get("SwapTotal"), mi.get("SwapFree")
    return {"total": total, "available": avail, "used": total - avail if total is not None and avail is not None else None,
            "swap_total": st, "swap_used": st - sf if st is not None and sf is not None else None}


def about(ollama_version: str | None = None) -> dict:
    return {
        "name": "Zeus-Agent", "tagline": "Local AI coding agent powered by Ollama", "version": config.VERSION, "founder": config.FOUNDER,
        "github": config.GITHUB_URL, "website": config.WEBSITE_URL, "license": config.LICENSE_NAME,
        "platform": {"os": os_name(), "kernel": platform.release(), "arch": platform.machine(), "python": sys.version.split()[0],
                     "ollama": ollama_version},
        "cpu": cpu_info(), "gpus": gpu_list(), "memory": memory_info(),
    }
