import type { Overview } from "../services/types";

export type Placement = "gpu" | "split" | "cpu" | "toolarge" | "unknown";

/** Rough placement of a model on this machine. Estimate: download size + ~10% + 1 GB for KV cache/runtime. */
export function placement(sizeGb: number, hw?: Overview["hardware"]): Placement {
  if (!hw) return "unknown";
  const need = sizeGb * 1e9 * 1.1 + 1e9;
  const gpu = hw.gpu?.total ?? 0;
  const ram = hw.ram_available ?? hw.ram_total ?? 0;
  if (!gpu && !ram) return "unknown";
  if (gpu && need <= gpu * 0.95) return "gpu";
  if (need <= (gpu * 0.95 + ram * 0.85)) return gpu ? "split" : "cpu";
  return "toolarge";
}

export const PLACEMENT_LABEL: Record<Placement, { text: string; cls: string; hint: string }> = {
  gpu: { text: "fits GPU", cls: "bg-ok/15 text-ok", hint: "Estimated to fit entirely in GPU memory (fastest)." },
  split: { text: "GPU+CPU", cls: "bg-warn/15 text-warn", hint: "Too big for GPU memory alone; Ollama will split it between GPU and RAM (slower)." },
  cpu: { text: "CPU only", cls: "bg-warn/15 text-warn", hint: "No GPU detected; will run from system RAM (slow)." },
  toolarge: { text: "too large", cls: "bg-danger/15 text-danger", hint: "Estimated larger than your GPU memory plus free RAM; likely to fail or crawl." },
  unknown: { text: "", cls: "", hint: "" },
};

export const fmtGb = (gb: number) => (gb < 1 ? `${Math.round(gb * 1000)} MB` : `${gb % 1 === 0 ? gb : gb.toFixed(1)} GB`);
