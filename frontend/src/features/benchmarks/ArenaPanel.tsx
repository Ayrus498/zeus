import { useCallback, useEffect, useMemo, useState } from "react";
import { Award, ChevronDown, ChevronRight, Cpu, Download, Loader2, MemoryStick, RefreshCw } from "lucide-react";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { Button } from "../../components/ui/button";
import { ExternalLink } from "../../components/ui/external-link";
import { cn, fmtCtx } from "../../lib/utils";
import { Chip } from "../models/ModelParts";
import { CopyButton } from "../../components/ui/copy";
import type { ArenaActivity, ArenaFamily, ArenaFit, ArenaHardware, ArenaModel, ArenaOverview, ArenaQuant } from "../../services/types";

const CTXS = [4096, 8192, 16384, 32768, 65536];
const LEVEL: Record<string, string> = { great: "bg-ok/15 text-ok", ok: "bg-accent/15 text-accent", slow: "bg-warn/15 text-warn", no: "bg-danger/15 text-danger" };
const SOURCE: Record<string, string> = { bundled: "Built-in snapshot", cached: "Saved copy", live: "Just refreshed" };
const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function FitChip({ fit }: { fit: ArenaFit | null }) {
  if (!fit) return <Chip title="The size of this model is not published, so Zeus-Agent cannot tell whether it fits">size unknown</Chip>;
  return <span title={fit.verdict.text}><Chip cls={LEVEL[fit.verdict.level]}>{fit.verdict.label}{fit.tps ? ` · ~${fit.tps >= 10 ? Math.round(fit.tps) : fit.tps} tok/s` : ""}</Chip></span>;
}

/** What Zeus-Agent read from this computer, in words: used to explain every verdict below it. */
function HardwareCard({ hw }: { hw: ArenaHardware }) {
  return (
    <div className="grid gap-2 rounded-lg border border-border bg-surface px-3 py-2.5 text-xs sm:grid-cols-3" data-testid="arena-hardware">
      <div className="flex items-start gap-2"><Cpu size={15} className="mt-0.5 shrink-0 text-accent" />
        <div><div className="font-medium">{hw.gpu ?? "No GPU detected"}</div>
          <div className="text-[11px] text-muted">{hw.vram_gb ? `${hw.vram_gb} GB GPU memory` : "Models run from system RAM, which is slower"}{hw.bandwidth ? ` · ~${hw.bandwidth} GB/s` : hw.vram_gb ? " · speed unknown for this card" : ""}</div></div></div>
      <div className="flex items-start gap-2"><MemoryStick size={15} className="mt-0.5 shrink-0 text-accent" />
        <div><div className="font-medium">{hw.ram_gb} GB system RAM</div><div className="text-[11px] text-muted">Used when a model is larger than GPU memory (about {hw.ram_bandwidth} GB/s)</div></div></div>
      <div className="text-[11px] text-muted">Speeds are estimates (about ±30%) for 4-bit (Q4_K_M) files, from your hardware's memory bandwidth. {hw.gpu_catalogue_match ? `Matched your card to ${hw.gpu_catalogue_match}; laptop parts are assumed slower.` : ""}</div>
    </div>
  );
}

export function QuantTable({ quants, best }: { quants: ArenaQuant[]; best?: string | null }) {
  return (
    <table className="w-full text-[11px]" data-testid="quant-table">
      <thead className="text-left text-muted"><tr><th className="py-1 pr-2 font-normal">Quantization</th><th className="pr-2 font-normal">File</th><th className="pr-2 font-normal">Runs</th><th className="pr-2 font-normal">Speed</th><th className="font-normal">Quality vs full precision</th></tr></thead>
      <tbody>
        {quants.map((q) => (
          <tr key={q.quant} className={cn("border-t border-border/50", q.quant === best && "bg-accent/10")}>
            <td className="py-1 pr-2 font-mono">{q.quant}{q.quant === best && <span className="ml-1 rounded bg-accent px-1 text-[9px] text-accent-foreground">suggested</span>}</td>
            <td className="pr-2 font-mono">{q.size_gb} GB</td>
            <td className={cn("pr-2", q.where === "no" ? "text-danger" : q.where === "gpu" ? "text-ok" : "text-warn")}>{{ gpu: "on GPU", split: "GPU + RAM", cpu: "CPU", no: "does not fit" }[q.where] ?? q.where}</td>
            <td className="pr-2 font-mono">{q.tps ? `~${q.tps >= 10 ? Math.round(q.tps) : q.tps} tok/s` : "–"}</td>
            <td className="text-muted">{q.quality}{q.loss >= 0.1 ? ` (+${q.loss} perplexity)` : ""}</td>
          </tr>))}
      </tbody>
    </table>
  );
}

function ModelRow({ m, ctx, activity }: { m: ArenaModel; ctx: number; activity: ArenaActivity }) {
  const { pullModel, engine } = useApp();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ArenaFit | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const top = activity.models[0]?.score ?? m.score;
  const low = activity.models[Math.min(activity.models.length - 1, 40)]?.score ?? m.score - 100;
  const pct = Math.max(6, Math.min(100, ((m.score - low) / Math.max(1, top - low)) * 100));
  useEffect(() => {
    if (open && !detail && m.repo) api.arenaDetail(m.repo, ctx).then((d) => setDetail(d.fit)).catch((e) => setMsg(err(e)));
  }, [open, detail, m.repo, ctx]);
  useEffect(() => setDetail(null), [ctx]);
  const canPull = !!m.ollama?.local && !!m.fit && m.fit.where !== "no";
  const hf = m.repo ? `https://huggingface.co/${m.repo}` : null;
  const gguf = `https://huggingface.co/models?search=${encodeURIComponent(m.name + " gguf")}`;
  return (
    <div className="border-b border-border/50 last:border-b-0" data-testid={`arena-row-${m.name}`}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full cursor-pointer items-center gap-2 px-2 py-1.5 text-left text-xs hover:bg-surface-2/60">
        {open ? <ChevronDown size={12} className="shrink-0 text-muted" /> : <ChevronRight size={12} className="shrink-0 text-muted" />}
        <span className="w-9 shrink-0 text-right font-mono text-[10px] text-muted" title={`Rank ${m.rank} of ${activity.total} models in Arena's ${activity.arena} ranking (paid models included)`}>#{m.rank}</span>
        <span className="min-w-0 flex-1 truncate font-medium">{m.name}<span className="ml-1.5 font-normal text-muted">{m.org}</span></span>
        <span className="hidden w-24 shrink-0 sm:block" title={`${m.score} Arena score · ${m.votes.toLocaleString()} votes`}><span className="block h-1.5 rounded bg-surface-2"><span className="block h-1.5 rounded bg-accent" style={{ width: `${pct}%` }} /></span></span>
        <span className="w-11 shrink-0 text-right font-mono">{m.score}</span>
        <span className="hidden w-20 shrink-0 text-right font-mono text-[10px] text-muted md:block" title={m.active_b && m.params_b && m.active_b < m.params_b * 0.6 ? "Mixture of experts: total / parameters used per token" : "Parameters"}>
          {m.params_b ? `${m.params_b >= 100 ? Math.round(m.params_b) : m.params_b}B${m.active_b && m.params_b && m.active_b < m.params_b * 0.6 ? ` / ${m.active_b}B` : ""}` : "–"}</span>
        <span className="w-44 shrink-0 text-right"><FitChip fit={m.fit} /></span>
      </button>
      {open && (
        <div className="space-y-2 bg-surface/50 px-4 py-3 text-xs" data-testid="arena-detail">
          <div className="text-muted">Arena {activity.arena} score <b className="text-foreground">{m.score}</b> from {m.votes.toLocaleString()} blind votes · rank {m.rank} of {activity.total}
            {activity.top_paid[0] && <> · the top paid model scores {activity.top_paid[0].score}</>} · licence: {m.license || "unknown"}{m.context ? ` · up to ${fmtCtx(m.context)} context` : ""}</div>
          {m.fit && <div className="text-muted">{m.fit.verdict.text} At {fmtCtx(ctx)} context the 4-bit file is {m.fit.weights_gb} GB plus {m.fit.kv_gb} GB of conversation memory.</div>}
          {m.repo ? (detail?.quants ? <QuantTable quants={detail.quants} best={detail.best_quant} /> : msg ? <div className="text-warn">{msg}</div> : <div className="flex items-center gap-1 text-muted"><Loader2 size={11} className="animate-spin" />Working out every quantization…</div>)
            : <div className="text-muted">The model's size is not published, so quantization sizes are not available.</div>}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {m.ollama && (m.ollama.local
              ? <><code className="rounded border border-border bg-surface px-2 py-1 font-mono">ollama pull {m.ollama.tag}</code><CopyButton getText={() => `ollama pull ${m.ollama!.tag}`} />
                  {canPull && engine() === "ollama" && <Button size="sm" disabled={busy} onClick={() => { setBusy(true); void pullModel(m.ollama!.tag).finally(() => setBusy(false)); }}><Download size={11} /> Download with Ollama</Button>}</>
              : <Chip title="Ollama lists this model without local sizes: it is cloud-hosted or very large">listed on Ollama (no local size)</Chip>)}
            {!m.ollama && <Chip title="Not found in the Ollama library under this name">not on Ollama</Chip>}
            {hf && <ExternalLink href={hf}>Hugging Face</ExternalLink>}
            <ExternalLink href={gguf}>find GGUF files (llama.cpp)</ExternalLink>
          </div>
        </div>)}
    </div>
  );
}

/** "Best for your hardware": Arena-ranked open models for each activity, with a verdict for this computer. */
export function ArenaPanel() {
  const [data, setData] = useState<ArenaOverview | null>(null);
  const [act, setAct] = useState("agentcode");
  const [ctx, setCtx] = useState(8192);
  const [onlyFit, setOnlyFit] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback((refresh: boolean) => {
    setBusy(true);
    api.arena(refresh, ctx).then((d) => { setData(d); setError(d.error ?? null); }).catch((e) => setError(err(e))).finally(() => setBusy(false));
  }, [ctx]);
  useEffect(() => load(false), [load]);
  const cur = data?.activities.find((a) => a.id === act) ?? data?.activities[0];
  const rows = useMemo(() => (cur ? cur.models.filter((m) => !onlyFit || (m.fit && m.fit.where !== "no")).slice(0, 60) : []), [cur, onlyFit]);
  const best = cur?.best_here;
  const quality = cur?.best_quality;
  return (
    <div className="space-y-4" data-testid="arena-panel">
      <div>
        <h3 className="flex items-center gap-2 text-base font-semibold"><Award size={16} className="text-accent" />Best models for your hardware</h3>
        <p className="mt-1 text-xs text-muted">Open models ranked by millions of blind human votes in Arena, checked against this computer. Pick the activity; models that do not fit are hidden unless you choose otherwise.</p>
      </div>
      {data && <HardwareCard hw={data.hardware} />}
      <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Activity">
        {data?.activities.map((a) => (
          <button key={a.id} role="tab" aria-selected={cur?.id === a.id} data-testid={`arena-tab-${a.id}`} onClick={() => setAct(a.id)} title={a.blurb}
            className={cn("cursor-pointer rounded-full border px-3 py-1 text-xs", cur?.id === a.id ? "border-accent bg-accent/20 text-foreground" : "border-border text-muted hover:text-foreground")}>{a.label}</button>))}
        <span className="ml-auto flex items-center gap-2 text-[11px] text-muted">
          <label className="flex cursor-pointer items-center gap-1"><input type="checkbox" checked={onlyFit} onChange={(e) => setOnlyFit(e.target.checked)} />Only what runs here</label>
          <label className="flex items-center gap-1">Context
            <select aria-label="Context" className="h-7 rounded border border-border bg-surface-2 px-1 text-xs" value={ctx} onChange={(e) => setCtx(Number(e.target.value))}>{CTXS.map((c) => <option key={c} value={c}>{fmtCtx(c)}</option>)}</select></label>
        </span>
      </div>
      {error && <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">{error} Showing {data ? SOURCE[data.state].toLowerCase() : "the built-in snapshot"}.</div>}
      {!data ? <div className="flex items-center gap-2 text-xs text-muted"><Loader2 size={12} className="animate-spin" />Reading your hardware and the rankings…</div> : cur && (
        <>
          <p className="text-xs text-muted">{cur.blurb}</p>
          {best ? (
            <div className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-2.5 text-xs" data-testid="arena-best">
              <div className="flex flex-wrap items-center gap-2"><span className="font-semibold text-accent">Best for you: {best.name}</span><FitChip fit={best.fit} />
                <span className="text-muted">Arena score {best.score} · rank #{best.rank} of {cur.total}</span></div>
              <div className="mt-1 text-muted">{(best.fit?.tps ?? 99) < 8 ? "Nothing ranked here reaches a comfortable speed (about 8 tokens/s); this is the best of what runs, and it will feel slow." : best.fit?.where === "gpu" ? "The highest-scoring model that runs entirely on your GPU at a comfortable speed." : "The highest-scoring model that is comfortable to use here (about 8 tokens/s or faster); part of it lives in system RAM."}
                {best.ollama?.local && <> Get it with <code className="font-mono text-foreground">ollama pull {best.ollama.tag}</code>.</>}</div>
              {quality && <div className="mt-1.5 text-muted" data-testid="arena-quality">Highest quality that runs at all: <b className="text-foreground">{quality.name}</b> (score {quality.score}, rank #{quality.rank}) <FitChip fit={quality.fit} />, slower than most people like to wait.</div>}
            </div>) : <div className="rounded-md border border-border px-3 py-2 text-xs text-muted">No ranked open model for this activity fits this computer.</div>}
          <div className="overflow-hidden rounded-lg border border-border">
            <div className="flex items-center gap-2 bg-surface-2 px-2 py-1.5 text-[10px] uppercase tracking-wide text-muted">
              <span className="w-3" /><span className="w-9 text-right">Rank</span><span className="flex-1">Model</span><span className="hidden w-24 sm:block" /><span className="w-11 text-right">Score</span><span className="hidden w-20 text-right md:block">Size</span><span className="w-44 text-right">On your computer</span></div>
            {rows.length ? rows.map((m) => <ModelRow key={m.name} m={m} ctx={ctx} activity={cur} />) : <div className="px-3 py-6 text-center text-xs text-muted">Nothing to show with these filters.</div>}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted" data-testid="arena-attribution">
            <span>{SOURCE[data.state]} · data as of {data.updated}. {data.license}. Scores are human votes on answers, not a test suite. Not affiliated with Arena or FreeLLMArena.</span>
            <ExternalLink href="https://freellmarena.com">freellmarena.com</ExternalLink>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => load(true)} title="Fetch the newest rankings from freellmarena.com (a plain request; nothing about you is sent)"><RefreshCw size={11} className={busy ? "animate-spin" : ""} />Refresh</Button>
          </div>
        </>)}
    </div>
  );
}

/** Inside a model family's page: how Arena voters rated it and whether each size runs on this computer. */
export function ArenaFamilySection({ family }: { family: string }) {
  const [d, setD] = useState<ArenaFamily | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setD(null); setFailed(false); api.arenaFamily(family).then(setD).catch(() => setFailed(true)); }, [family]);
  if (failed || !d || d.results.length === 0) return null;
  return (
    <section data-testid="arena-family">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted"><Award size={12} className="text-accent" />Benchmarks and your hardware <span className="font-normal">(Arena human votes)</span></div>
      <div className="divide-y divide-border/50 rounded-md border border-border bg-surface px-3 text-xs">
        {d.results.map((r) => (
          <div key={`${r.activity}-${r.name}`} className="flex flex-wrap items-center gap-2 py-1.5">
            <span className="w-32 shrink-0 font-medium">{r.label}</span>
            <span className="min-w-0 flex-1 truncate text-muted" title={r.name}>{r.name}{r.params_b ? ` · ${r.params_b}B` : ""}</span>
            <span className="font-mono" title={`${r.votes.toLocaleString()} votes`}>{r.score}</span>
            <span className="w-24 text-right font-mono text-[10px] text-muted">#{r.rank} of {r.of}</span>
            <span className="w-44 text-right"><FitChip fit={r.fit} /></span>
          </div>))}
      </div>
      <div className="mt-1 text-[10px] text-muted">{d.license}. Speeds are estimates for 4-bit files on {d.hardware.gpu ?? "this computer's CPU"}.</div>
    </section>
  );
}
