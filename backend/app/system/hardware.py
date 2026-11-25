"""What this machine can offer a model: free GPU memory (nvidia-smi) and available RAM, the memory a model's conversation cache needs, and whether a context size fits.

Generic facts about the computer, used by the agent's Auto mode and by the API; they belong to no particular engine."""
from __future__ import annotations

import shutil

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
