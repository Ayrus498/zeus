import { useRef, useState } from "react";
import { HISTORY } from "../../hooks/useMetrics";

interface Props {
  values: (number | null)[];          // oldest → newest
  max: number;                        // y-axis ceiling
  format: (v: number) => string;
  color?: string;
  height?: number;
}

const W = 300;

/** Interactive area chart: hover/move to read the exact value and time offset at any point. */
export function Sparkline({ values, max, format, color = "var(--color-accent)", height = 56 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const n = HISTORY;
  const offset = n - values.length;                       // right-align so the newest point sits at the edge
  const x = (i: number) => ((i + offset) / (n - 1)) * W;
  const y = (v: number) => height - 2 - (Math.min(Math.max(v, 0), max) / (max || 1)) * (height - 4);
  const pts = values.map((v, i) => (v == null ? null : [x(i), y(v)] as const));
  const segs: (readonly [number, number])[][] = [];
  pts.forEach((p) => { if (!p) segs.push([]); else { if (!segs.length) segs.push([]); segs[segs.length - 1].push(p); } });
  const line = (s: (readonly [number, number])[]) => s.map(([a, b], i) => `${i ? "L" : "M"}${a.toFixed(1)},${b.toFixed(1)}`).join(" ");

  const onMove = (e: React.MouseEvent) => {
    const r = box.current!.getBoundingClientRect();
    const idx = Math.round(((e.clientX - r.left) / r.width) * (n - 1)) - offset;
    setHover(idx >= 0 && idx < values.length ? idx : null);
  };
  const hv = hover != null ? values[hover] : null;
  const secondsAgo = hover != null ? Math.round(((values.length - 1 - hover) * 1.5)) : 0;

  return (
    <div ref={box} className="relative cursor-crosshair select-none" style={{ height }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" className="h-full w-full overflow-visible rounded bg-surface-2/60">
        {[0.25, 0.5, 0.75].map((f) => <line key={f} x1="0" x2={W} y1={height * f} y2={height * f} stroke="var(--color-border)" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />)}
        {segs.filter((s) => s.length > 1).map((s, i) => (
          <g key={i}>
            <path d={`${line(s)} L${s[s.length - 1][0]},${height} L${s[0][0]},${height} Z`} fill={color} opacity="0.18" />
            <path d={line(s)} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </g>
        ))}
        {hover != null && hv != null && <line x1={x(hover)} x2={x(hover)} y1="0" y2={height} stroke="var(--color-muted)" strokeWidth="1" vectorEffect="non-scaling-stroke" strokeDasharray="3 3" />}
      </svg>
      {hover != null && hv != null && (
        <>
          <div className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white" style={{ left: `${(x(hover) / W) * 100}%`, top: y(hv), background: color }} />
          <div className="pointer-events-none absolute z-10 -translate-x-1/2 whitespace-nowrap rounded border border-border bg-background px-1.5 py-0.5 font-mono text-[10px] shadow"
            style={{ left: `clamp(28px, ${(x(hover) / W) * 100}%, calc(100% - 28px))`, top: -22 }}>
            {format(hv)} · {secondsAgo === 0 ? "now" : `${secondsAgo}s ago`}
          </div>
        </>
      )}
    </div>
  );
}
