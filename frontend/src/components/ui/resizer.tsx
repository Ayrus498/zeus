import { useCallback, useEffect, useState } from "react";
import { cn } from "../../lib/utils";

/** A pane width that is persisted per-viewer, never below `min`, and re-clamped when the window shrinks. */
export function usePaneWidth(key: string, min: number) {
  const [w, setW] = useState<number>(() => {
    try { const v = Number(localStorage.getItem(`zeus.pane.${key}`)); return Number.isFinite(v) && v >= min ? v : min; } catch { return min; }
  });
  const set = useCallback((v: number) => {
    setW(v);
    try { localStorage.setItem(`zeus.pane.${key}`, String(Math.round(v))); } catch { /* storage unavailable */ }
  }, [key]);
  return [w, set] as const;
}

export function useWindowWidth() {
  const [x, setX] = useState(() => window.innerWidth);
  useEffect(() => { const f = () => setX(window.innerWidth); window.addEventListener("resize", f); return () => window.removeEventListener("resize", f); }, []);
  return x;
}

/**
 * Vertical drag handle between two panes. `side` is the pane it resizes:
 * "left" grows when dragged right, "right" grows when dragged left.
 * Double-click resets to the minimum; Arrow keys nudge by 16px (Shift: 64px).
 */
export function Resizer({ side, width, min, max, onChange, label }: {
  side: "left" | "right"; width: number; min: number; max: number; onChange: (w: number) => void; label: string;
}) {
  const [active, setActive] = useState(false);
  const clamp = (v: number) => Math.min(Math.max(v, min), Math.max(min, max));
  const dir = side === "left" ? 1 : -1;

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const el = e.currentTarget; el.setPointerCapture(e.pointerId);
    const startX = e.clientX, startW = width;
    setActive(true);
    const prevSel = document.body.style.userSelect, prevCur = document.body.style.cursor;
    document.body.style.userSelect = "none"; document.body.style.cursor = "col-resize";
    const move = (ev: PointerEvent) => onChange(clamp(startW + dir * (ev.clientX - startX)));
    const up = () => {
      setActive(false);
      document.body.style.userSelect = prevSel; document.body.style.cursor = prevCur;
      el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up);
    };
    el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
  };

  return (
    <div role="separator" aria-orientation="vertical" aria-label={label} aria-valuenow={Math.round(width)} aria-valuemin={min} aria-valuemax={Math.max(min, max)}
      tabIndex={0} title="Drag to resize · double-click to reset"
      onPointerDown={onPointerDown} onDoubleClick={() => onChange(min)}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 64 : 16;
        if (e.key === "ArrowLeft") { e.preventDefault(); onChange(clamp(width - dir * step)); }
        if (e.key === "ArrowRight") { e.preventDefault(); onChange(clamp(width + dir * step)); }
      }}
      className="group relative z-20 w-0 shrink-0 cursor-col-resize outline-none">
      <div className={cn("absolute inset-y-0 -left-[3px] w-[6px] transition-colors group-hover:bg-accent/60 group-focus-visible:bg-accent/60", active && "bg-accent")} />
    </div>
  );
}
