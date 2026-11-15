import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Check, Download, Loader2, PowerOff, RotateCw, Search, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { ExternalLink } from "../../components/ui/external-link";
import { CopyButton } from "../../components/ui/copy";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { cn, fmtBytes } from "../../lib/utils";
import { fmtGb, placement, PLACEMENT_LABEL } from "../../lib/modelFit";
import type { Catalog, CatalogModel, DownloadRow, Fit, Overview } from "../../services/types";
import { SetupGuide } from "./SetupGuide";
import { AutoModeCard } from "./AutoModeCard";

const FIT: Record<Fit, { text: string; cls: string; hint: string }> = {
  best: { text: "Best for ZEUS", cls: "bg-accent text-accent-foreground", hint: "Strong coding / agent model with native tool calling." },
  good: { text: "Good", cls: "bg-ok/20 text-ok", hint: "Works well for chat or reasoning; tool use is fair." },
  limited: { text: "Limited", cls: "bg-warn/15 text-warn", hint: "Usable, but weak at multi-step tool use (tiny, older or vision-only models)." },
  no: { text: "Not a chat model", cls: "bg-surface-2 text-muted", hint: "Embedding model: cannot drive the agent." },
};
const ACTIVE = ["queued", "running"];
const norm = (n: string) => (n.includes(":") ? n : `${n}:latest`);
const fmtEta = (d: DownloadRow) => {
  if (!d.total) return d.detail;
  return `${fmtBytes(d.completed)} / ${fmtBytes(d.total)}`;
};

function Chip({ children, cls, title }: { children: React.ReactNode; cls?: string; title?: string }) {
  return <span title={title} className={cn("rounded px-1.5 py-0.5 text-[10px]", cls ?? "bg-surface-2 text-muted")}>{children}</span>;
}

/* ---------------------------------------------------------------- downloads */
function Downloads({ rows }: { rows: DownloadRow[] }) {
  const { pullModel, cancelDownload, dismissDownload } = useApp();
  if (!rows.length) return null;
  return (
    <section>
      <div className="mb-1.5 text-xs font-medium text-muted">Downloads</div>
      <div className="space-y-2">
        {rows.map((d) => {
          const active = ACTIVE.includes(d.status);
          const bad = d.status === "failed" || d.status === "interrupted";
          return (
            <div key={d.id} data-testid={`download-${d.status}`} className={cn("rounded-lg border p-2.5 text-xs", bad ? "border-danger/50 bg-danger/5" : "border-border bg-surface")}>
              <div className="flex items-center gap-2">
                {active ? <Loader2 size={13} className="animate-spin text-accent" /> : d.status === "completed" ? <Check size={13} className="text-ok" /> : bad ? <AlertTriangle size={13} className="text-danger" /> : <X size={13} className="text-muted" />}
                <span className="min-w-0 truncate font-mono">{d.model}</span>
                <Chip cls={bad ? "bg-danger/15 text-danger" : d.status === "completed" ? "bg-ok/15 text-ok" : undefined}>{d.status}</Chip>
                <span className="ml-auto flex shrink-0 gap-1">
                  {active && <Button size="sm" variant="outline" onClick={() => void cancelDownload(d.id)}>Cancel</Button>}
                  {(bad || d.status === "cancelled") && <Button size="sm" variant="secondary" onClick={() => void pullModel(d.model)}><RotateCw size={11} /> Retry</Button>}
                  {!active && <button aria-label="Dismiss" title="Remove from list" className="cursor-pointer rounded p-1 text-muted hover:text-foreground" onClick={() => void dismissDownload(d.id)}><X size={12} /></button>}
                </span>
              </div>
              {active && (
                <div className="mt-2">
                  <div className="h-1.5 overflow-hidden rounded bg-surface-2"><div className={cn("h-full bg-accent transition-all", d.percent == null && "w-1/4 animate-pulse")} style={d.percent != null ? { width: `${d.percent}%` } : undefined} /></div>
                  <div className="mt-1 flex justify-between font-mono text-[10px] text-muted"><span>{d.percent != null ? `${d.percent}%` : "starting…"} · {fmtEta(d)}</span><span className="truncate pl-2">{d.total ? d.detail : ""}</span></div>
                </div>)}
              {d.status === "completed" && <div className="mt-1 text-[11px] text-muted">{fmtBytes(d.total)} downloaded. The model is ready to select.</div>}
              {d.error && (
                <div className="mt-2 flex items-start gap-1 rounded border border-danger/30 bg-background p-2 font-mono text-[11px] text-[#f2b8b3]">
                  <span className="min-w-0 flex-1 select-text break-words">{d.error}</span><CopyButton getText={() => d.error ?? ""} className="shrink-0" />
                </div>)}
            </div>);
        })}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- installed */
function Installed({ ov, catalog }: { ov: Overview; catalog: Catalog | null }) {
  const { unloadModel, setModel, session, project } = useApp();
  const known = useMemo(() => {
    const m = new Map<string, CatalogModel>();
    catalog?.models.forEach((c) => m.set(c.name, c));
    return m;
  }, [catalog]);
  const loaded = new Map(ov.loaded.map((l) => [norm(l.name), l]));
  if (!ov.installed.length) return null;
  return (
    <section>
      <div className="mb-1.5 text-xs font-medium text-muted">Installed on this machine <span className="font-mono text-[10px]">({ov.installed.length})</span></div>
      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        {ov.installed.map((m) => {
          const l = loaded.get(norm(m.name));
          const fam = known.get(m.name.split(":")[0]);
          const notChat = fam?.fit === "no";
          return (
            <div key={m.name} className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs last:border-b-0">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5"><span className="truncate font-mono">{m.name}</span>
                  {l && <Chip cls="bg-accent text-accent-foreground" title="Currently in memory">loaded · {l.processor}</Chip>}
                  {session?.model === m.name && <Chip cls="bg-ok/15 text-ok">this session</Chip>}</div>
                <div className="truncate text-[10px] text-muted">{fmtBytes(m.size)}{m.parameter_size ? ` · ${m.parameter_size}` : ""}{m.quantization ? ` · ${m.quantization}` : ""}{fam ? ` · ${fam.title}` : " · not in the guide"}</div>
              </div>
              {l && <Button size="sm" variant="outline" title="Free this model's memory now" onClick={() => void unloadModel(m.name)}><PowerOff size={11} /> Unload</Button>}
              <Button size="sm" variant="secondary" disabled={!project || notChat || session?.model === m.name}
                title={!project ? "Open a project first" : notChat ? "Embedding model: cannot chat" : "Use this model for the current session"} onClick={() => void setModel(m.name)}>Use</Button>
            </div>);
        })}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- guide */
function TagRow({ t, family, ov, dl }: { t: CatalogModel["tags"][number]; family: string; ov: Overview; dl?: DownloadRow }) {
  const { pullModel, unloadModel } = useApp();
  const installedNames = new Set(ov.installed.map((m) => norm(m.name)));
  const installed = installedNames.has(norm(t.tag)) || (t.latest && installedNames.has(`${family}:latest`));
  const loadedHit = ov.loaded.some((l) => norm(l.name) === norm(t.tag) || (t.latest && norm(l.name) === `${family}:latest`));
  const loadedName = ov.loaded.find((l) => norm(l.name) === norm(t.tag) || (t.latest && norm(l.name) === `${family}:latest`))?.name ?? t.tag;
  const place = placement(t.size_gb, ov.hardware);
  const pl = PLACEMENT_LABEL[place];
  const active = dl && ACTIVE.includes(dl.status);
  return (
    <div className="flex items-center gap-2 py-1 text-xs">
      <span className="min-w-0 flex-1 truncate font-mono">{t.tag.slice(family.length + 1)}{t.latest && <span className="ml-1 text-[10px] text-muted">(latest)</span>}</span>
      <span className="w-14 shrink-0 text-right font-mono text-muted">{fmtGb(t.size_gb)}</span>
      <span className="w-[62px] shrink-0 text-center">{pl.text && !installed ? <Chip cls={pl.cls} title={pl.hint}>{pl.text}</Chip> : null}</span>
      <span className="flex w-[132px] shrink-0 justify-end gap-1">
        {active ? <span className="flex items-center gap-1 font-mono text-[11px] text-accent"><Loader2 size={11} className="animate-spin" />{dl!.percent != null ? `${Math.round(dl!.percent)}%` : "…"}</span>
          : installed ? <>
              {loadedHit ? <Button size="sm" variant="outline" onClick={() => void unloadModel(loadedName)}><PowerOff size={11} /> Unload</Button> : <Chip cls="bg-ok/15 text-ok"><Check size={10} className="mr-0.5 inline" />installed</Chip>}
            </>
          : <Button size="sm" disabled={!ov.connected} title={ov.connected ? `Download ${t.tag} (~${fmtGb(t.size_gb)})` : "Ollama is not running"} onClick={() => void pullModel(t.tag)}><Download size={11} /> Download</Button>}
      </span>
    </div>
  );
}

function Card({ m, ov, catalog, defaultOpen }: { m: CatalogModel; ov: Overview; catalog: Catalog; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const dls = ov.downloads;
  const f = FIT[m.fit];
  return (
    <div className="rounded-lg border border-border bg-surface" data-testid={`model-${m.name}`}>
      <button className="flex w-full cursor-pointer items-start gap-2 p-3 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-muted" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-muted" />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5"><span className="text-sm font-medium">{m.title}</span><span className="text-[10px] text-muted">{m.maker}</span>
            <Chip cls={f.cls} title={f.hint}>{f.text}</Chip></div>
          <div className="mt-0.5 text-xs text-muted">{m.description}</div>
        </div>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-3 pb-3 pt-2">
          <div className="text-xs"><span className="text-muted">Best for: </span>{m.best_for}</div>
          <div className="flex flex-wrap gap-1">
            {m.purposes.map((p) => <Chip key={p}>{catalog.purposes[p] ?? p}</Chip>)}
            {m.capabilities.map((c) => <Chip key={c} cls="bg-accent/15 text-accent">{c}</Chip>)}
            {m.context && <Chip title="Maximum context window">{m.context} ctx</Chip>}
          </div>
          {m.note && <div className="text-[11px] text-warn">{m.note}</div>}
          <div className="divide-y divide-border/50">
            {m.tags.map((t) => <TagRow key={t.tag} t={t} family={m.name} ov={ov} dl={dls.find((d) => d.model === t.tag)} />)}
          </div>
          <div className="text-[11px]"><ExternalLink href={m.url}>{`ollama.com/library/${m.name}`}</ExternalLink></div>
        </div>)}
    </div>
  );
}

function Guide({ ov, catalog }: { ov: Overview; catalog: Catalog }) {
  const [q, setQ] = useState("");
  const [purpose, setPurpose] = useState<string>("all");
  const [onlyInstalled, setOnlyInstalled] = useState(false);
  const installed = new Set(ov.installed.map((m) => norm(m.name)));
  const isInstalled = (m: CatalogModel) => m.tags.some((t) => installed.has(norm(t.tag)) || (t.latest && installed.has(`${m.name}:latest`)));
  const shown = catalog.models.filter((m) =>
    (purpose === "all" || m.purposes.includes(purpose)) && (!onlyInstalled || isInstalled(m)) &&
    (!q.trim() || `${m.name} ${m.title} ${m.maker} ${m.description} ${m.best_for}`.toLowerCase().includes(q.trim().toLowerCase())));
  const order: Record<Fit, number> = { best: 0, good: 1, limited: 2, no: 3 };
  shown.sort((a, b) => order[a.fit] - order[b.fit]);
  return (
    <section>
      <div className="mb-1.5 flex items-baseline justify-between"><div className="text-xs font-medium text-muted">Model guide <span className="font-mono text-[10px]">({shown.length}/{catalog.models.length})</span></div>
        <ExternalLink href={catalog.library_url} className="text-[11px]">Full library</ExternalLink></div>
      <div className="relative mb-2"><Search size={13} className="absolute left-2 top-2 text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search models, e.g. coder, vision, reasoning…" aria-label="Search models"
          className="h-8 w-full rounded-md border border-border bg-surface-2 pl-7 pr-2 text-xs outline-none focus:border-accent" /></div>
      <div className="mb-2 flex flex-wrap gap-1">
        {["all", ...Object.keys(catalog.purposes)].map((p) => (
          <button key={p} onClick={() => setPurpose(p)} className={cn("cursor-pointer rounded-full border px-2 py-0.5 text-[11px]", purpose === p ? "border-accent bg-accent text-accent-foreground" : "border-border text-muted hover:text-foreground")}>
            {p === "all" ? "All" : catalog.purposes[p]}</button>))}
        <button onClick={() => setOnlyInstalled(!onlyInstalled)} className={cn("cursor-pointer rounded-full border px-2 py-0.5 text-[11px]", onlyInstalled ? "border-ok bg-ok/20 text-ok" : "border-border text-muted hover:text-foreground")}>Installed only</button>
      </div>
      <div className="space-y-2">
        {shown.map((m) => <Card key={m.name} m={m} ov={ov} catalog={catalog} defaultOpen={isInstalled(m) || ov.downloads.some((d) => ACTIVE.includes(d.status) && d.model.startsWith(m.name + ":"))} />)}
        {!shown.length && <div className="rounded-lg border border-dashed border-border p-4 text-center text-xs text-muted">No models match. Try another filter, or browse the <ExternalLink href={catalog.library_url}>full Ollama library</ExternalLink>.</div>}
      </div>
      <div className="mt-3 text-[11px] text-muted">{catalog.note} “Fits GPU” and “GPU+CPU” are estimates from your detected memory{ov.hardware.gpu ? ` (${ov.hardware.gpu.name}, ${fmtBytes(ov.hardware.gpu.total)})` : ""}.</div>
    </section>
  );
}

/* ---------------------------------------------------------------- panel */
export function ModelsPanel() {
  const { overview: ov, refreshOverview } = useApp();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catErr, setCatErr] = useState<string | null>(null);
  useEffect(() => { void refreshOverview(); api.catalog().then(setCatalog).catch((e) => setCatErr(String(e instanceof Error ? e.message : e))); }, [refreshOverview]);

  if (!ov) return <div className="p-4 text-xs text-muted">Reading Ollama state…</div>;
  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center gap-2 text-xs">
        <span className={cn("h-2 w-2 rounded-full", ov.connected ? "bg-ok" : "bg-danger")} />
        <span>{ov.connected ? `Ollama ${ov.version ?? ""} connected` : "Ollama not detected"}</span>
        <span className="ml-auto truncate font-mono text-[10px] text-muted">{ov.endpoint}</span>
      </div>
      {(!ov.connected || ov.installed.length === 0) && <SetupGuide connected={ov.connected} error={ov.error} endpoint={ov.endpoint} hasModels={ov.installed.length > 0} />}
      <AutoModeCard />
      <Downloads rows={ov.downloads} />
      <Installed ov={ov} catalog={catalog} />
      {catalog ? <Guide ov={ov} catalog={catalog} />
        : <div className="text-xs text-muted">{catErr ? <span className="text-danger">Could not load the model guide: {catErr}</span> : "Loading model guide…"}</div>}
    </div>
  );
}
