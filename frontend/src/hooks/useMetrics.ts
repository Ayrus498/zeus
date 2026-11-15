import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { Metrics } from "../services/types";

export const HISTORY = 90;           // samples kept (≈ 2 minutes at 1.5 s)
const INTERVAL_MS = 1500;

// Module-level so the history survives switching tabs while the app is open.
let history: Metrics[] = [];
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let failed = 0;

async function tick() {
  try {
    const m = await api.metrics();
    failed = 0;
    history = [...history, m].slice(-HISTORY);
  } catch { failed++; }
  listeners.forEach((l) => l());
}

/** Polls /api/metrics only while at least one component is subscribed (i.e. the Usage tab is open). */
export function useMetrics(): { history: Metrics[]; latest: Metrics | null; error: boolean } {
  const [, force] = useState(0);
  useEffect(() => {
    const l = () => force((n) => n + 1);
    listeners.add(l);
    if (!timer) { void tick(); timer = setInterval(() => void tick(), INTERVAL_MS); }
    return () => {
      listeners.delete(l);
      if (!listeners.size && timer) { clearInterval(timer); timer = null; }
    };
  }, []);
  return { history, latest: history[history.length - 1] ?? null, error: failed >= 2 };
}
