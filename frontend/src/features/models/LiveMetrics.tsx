import { Cpu, MemoryStick, MonitorCog, Activity } from "lucide-react";
import { useMetrics } from "../../hooks/useMetrics";
import { Sparkline } from "../../components/ui/sparkline";
import { fmtBytes } from "../../lib/utils";
import { useApp } from "../../stores/app";
import type { LoadedModel, Metrics } from "../../services/types";

const pct = (v: number) => `${v.toFixed(v < 10 ? 1 : 0)}%`;

function Gauge({ icon, label, value, sub, series, max, format, color }: {
  icon: React.ReactNode; label: string; value: string; sub?: string; series: (number | null)[]; max: number; format: (v: number) => string; color?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      <div className="mb-1 flex items-center gap-1.5 whitespace-nowrap text-xs text-muted">{icon}{label}</div>
      <div className="mb-2 flex items-baseline gap-2">
        <span className="font-mono text-2xl font-semibold tabular-nums leading-none">{value}</span>
        <span className="min-w-0 truncate font-mono text-[10px] text-muted" title={sub}>{sub}</span>
      </div>
      <Sparkline values={series} max={max} format={format} color={color} />
    </div>
  );
}

function until(expires?: string | null): string {
  if (!expires) return "–";
  const ms = Date.parse(expires) - Date.now();
  if (!Number.isFinite(ms)) return "–";
  if (ms > 365 * 24 * 3600 * 1000) return "never unloads";
  if (ms <= 0) return "unloading…";
  const s = Math.round(ms / 1000);
  return s >= 60 ? `unloads in ${Math.floor(s / 60)}m ${s % 60}s` : `unloads in ${s}s`;
}

function LoadedCard({ m }: { m: LoadedModel }) {
  const gpu = m.gpu_pct ?? 0;
  return (
    <div className="rounded-lg border border-border bg-surface p-3 text-xs">
      <div className="flex items-center gap-2"><span className="truncate font-mono text-sm">{m.name}</span><span className="ml-auto shrink-0 rounded bg-accent px-1.5 py-0.5 font-mono text-[10px] text-accent-foreground">{m.processor}</span></div>
      <div className="mt-2 flex h-2 overflow-hidden rounded bg-surface-2" title={`${gpu}% of the model is in GPU memory, ${100 - gpu}% in system RAM`}>
        <div className="h-full bg-accent transition-all" style={{ width: `${gpu}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted"><span>GPU {gpu}% · {fmtBytes(m.size_vram)}</span><span>CPU {100 - gpu}% · {fmtBytes(Math.max(m.size - m.size_vram, 0))}</span></div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
        <span className="text-muted">Size</span><span className="text-right font-mono">{fmtBytes(m.size)}</span>
        <span className="text-muted">Context</span><span className="text-right font-mono">{m.context_length?.toLocaleString() ?? "–"}</span>
        {m.details?.parameter_size && <><span className="text-muted">Params / quant</span><span className="text-right font-mono">{[m.details.parameter_size, m.details.quantization_level].filter((x) => x && x !== "unknown").join(" · ")}</span></>}
        <span className="text-muted">Keep-alive</span><span className="text-right font-mono">{until(m.expires_at)}</span>
      </div>
    </div>
  );
}

const series = (h: Metrics[], f: (m: Metrics) => number | null | undefined) => h.map((m) => f(m) ?? null);

export function LiveMetrics() {
  const { history, latest, error } = useMetrics();
  const modelState = useApp((s) => s.modelState);
  if (!latest) return <div className="p-4 text-xs text-muted">{error ? "Metrics unavailable (backend not responding)." : "Reading system metrics…"}</div>;

  const gpu = latest.gpus[0];
  const loaded = latest.ollama.loaded;
  const working = loaded.length > 0 && ((gpu?.util_pct ?? 0) > 20 || (latest.ollama_proc.cpu_percent_of_total ?? 0) > 5);
  const busy = modelState !== "idle" || working;
  const ramTotal = latest.ram.total ?? 1;
  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center gap-2 text-xs">
        <span className={`h-2 w-2 rounded-full ${busy ? "animate-pulse bg-accent" : latest.ollama.connected ? "bg-ok" : "bg-danger"}`} />
        <span>{!latest.ollama.connected ? "Ollama not connected" : busy ? "Model working…" : "Ollama idle"}</span>
        <span className="ml-auto font-mono text-[10px] text-muted">live · every 1.5 s{latest.ollama.version ? ` · ollama ${latest.ollama.version}` : ""}</span>
      </div>

      <div>
        <div className="mb-1.5 text-xs font-medium text-muted">Loaded models <span className="font-mono text-[10px]">(ollama ps)</span></div>
        {loaded.length ? <div className="space-y-2">{loaded.map((m) => <LoadedCard key={m.name} m={m} />)}</div>
          : <div className="rounded-lg border border-dashed border-border p-3 text-xs text-muted">No model loaded. It loads on the first prompt and unloads after Ollama's keep-alive.</div>}
      </div>

      <div className="grid gap-2">
        {gpu ? (
          <>
            <Gauge icon={<MonitorCog size={13} className="text-accent" />} label="GPU utilization" value={gpu.util_pct == null ? "–" : pct(gpu.util_pct)}
              sub={`${gpu.name}${gpu.temp_c != null ? ` · ${gpu.temp_c}°C` : ""}${gpu.power_w != null ? ` · ${gpu.power_w.toFixed(0)} W` : ""}`}
              series={series(history, (m) => m.gpus[0]?.util_pct)} max={100} format={pct} />
            <Gauge icon={<MonitorCog size={13} className="text-accent" />} label="GPU memory (VRAM)"
              value={gpu.mem_used != null ? fmtBytes(gpu.mem_used) : "–"} sub={gpu.mem_total ? `of ${fmtBytes(gpu.mem_total)}` : undefined}
              series={series(history, (m) => m.gpus[0]?.mem_used)} max={gpu.mem_total ?? 1} format={(v) => fmtBytes(v)} />
          </>
        ) : <div className="rounded-lg border border-dashed border-border p-3 text-xs text-muted">No GPU detected (looked for NVIDIA via nvidia-smi and AMD via amdgpu sysfs). Models run on CPU.</div>}
        <Gauge icon={<Activity size={13} className="text-accent" />} label="Ollama CPU" value={latest.ollama_proc.cpu_percent_of_total == null ? "–" : pct(latest.ollama_proc.cpu_percent_of_total)}
          sub={latest.ollama_proc.running ? `${latest.ollama_proc.cpu_percent?.toFixed(0)}% of one core · ${fmtBytes(latest.ollama_proc.rss)} RSS` : "ollama process not found"}
          series={series(history, (m) => m.ollama_proc.cpu_percent_of_total)} max={100} format={pct} />
        <Gauge icon={<Cpu size={13} className="text-accent" />} label="System CPU" value={latest.cpu.percent == null ? "–" : pct(latest.cpu.percent)}
          sub={`${latest.cpu.cores} cores · load ${latest.cpu.load1.toFixed(2)}`} series={series(history, (m) => m.cpu.percent)} max={100} format={pct} />
        <Gauge icon={<MemoryStick size={13} className="text-accent" />} label="System RAM" value={latest.ram.used != null ? fmtBytes(latest.ram.used) : "–"}
          sub={latest.ram.total ? `of ${fmtBytes(latest.ram.total)}` : undefined} series={series(history, (m) => m.ram.used)} max={ramTotal} format={(v) => fmtBytes(v)} />
      </div>
      <div className="text-[11px] text-muted">Readings come from /proc, nvidia-smi / amdgpu sysfs and Ollama's <span className="font-mono">/api/ps</span>. They are machine-wide; "Ollama CPU" is the Ollama process only.</div>
    </div>
  );
}
