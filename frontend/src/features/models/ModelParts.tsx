import { useState } from "react";
import { AlertTriangle, Check, Download, Loader2, PowerOff, RotateCw, Trash2, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { CopyButton } from "../../components/ui/copy";
import { useApp } from "../../stores/app";
import { cn, fmtBytes } from "../../lib/utils";
import { fmtGb, placement, PLACEMENT_LABEL } from "../../lib/modelFit";
import { catalogFamily } from "../../lib/modelGroups";
import type { Catalog, CatalogModel, DownloadRow, Fit, Overview } from "../../services/types";

export const FIT: Record<Fit, { text: string; cls: string; hint: string }> = {
  best: { text: "Best for Zeus-Agent", cls: "bg-accent text-accent-foreground", hint: "Strong coding / agent model with native tool calling." },
  good: { text: "Good", cls: "bg-ok/20 text-ok", hint: "Works well for chat or reasoning; tool use is fair." },
  limited: { text: "Limited", cls: "bg-warn/15 text-warn", hint: "Usable, but weak at multi-step tool use (tiny, older or vision-only models)." },
  no: { text: "Not a chat model", cls: "bg-surface-2 text-muted", hint: "Embedding model: cannot drive the agent." },
};
export const ACTIVE = ["queued", "running"];
export const norm = (n: string) => (n.includes(":") ? n : `${n}:latest`);
const fmtEta = (d: DownloadRow) => {
  if (!d.total) return d.detail;
  return `${fmtBytes(d.completed)} / ${fmtBytes(d.total)}`;
};

export function Chip({ children, cls, title }: { children: React.ReactNode; cls?: string; title?: string }) {
  return <span title={title} className={cn("rounded px-1.5 py-0.5 text-[10px]", cls ?? "bg-surface-2 text-muted")}>{children}</span>;
}

/* ---------------------------------------------------------------- downloads */
export function Downloads({ rows }: { rows: DownloadRow[] }) {
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

/* ---------------------------------------------------------------- uninstall */
/** Two-step uninstall: the first click asks, the second deletes the model files from disk. */
export function UninstallButton({ name, size }: { name: string; size?: number }) {
  const { deleteModel, session } = useApp();
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  if (busy) return <Button size="sm" variant="outline" disabled><Loader2 size={11} className="animate-spin" /> Removing…</Button>;
  if (!ask) return <Button size="sm" variant="outline" data-testid={`uninstall-${name}`} className="text-danger hover:text-danger" title={`Delete ${name} from this machine`} onClick={() => setAsk(true)}><Trash2 size={11} /> Uninstall</Button>;
  return (
    <div className="flex items-center gap-1" role="group" aria-label={`Confirm uninstall ${name}`}>
      <span className="max-w-[180px] text-[10px] leading-tight text-danger">
        Delete {size ? fmtBytes(size) : "files"} from disk{session?.model === name ? " (this session uses it)" : ""}?</span>
      <Button size="sm" variant="danger" data-testid={`confirm-uninstall-${name}`} onClick={async () => { setBusy(true); await deleteModel(name); setBusy(false); setAsk(false); }}>Delete</Button>
      <Button size="sm" variant="ghost" onClick={() => setAsk(false)}>Cancel</Button>
    </div>
  );
}

/* ---------------------------------------------------------------- installed */
export function Installed({ ov, catalog }: { ov: Overview; catalog: Catalog | null }) {
  const { unloadModel, setModel, session, project } = useApp();
  const loaded = new Map(ov.loaded.map((l) => [norm(l.name), l]));
  if (!ov.installed.length) return null;
  return (
    <section>
      <div className="mb-1.5 text-xs font-medium text-muted">Installed on this machine <span className="font-mono text-[10px]">({ov.installed.length})</span></div>
      <div className="overflow-hidden rounded-lg border border-border bg-surface">
        {ov.installed.map((m) => {
          const l = loaded.get(norm(m.name));
          const fam = catalog ? catalogFamily(m.name, catalog.models) : null;
          const notChat = fam?.fit === "no";
          return (
            <div key={m.name} className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs last:border-b-0">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5"><span className="truncate font-mono">{m.name}</span>
                  {l && <Chip cls="bg-accent text-accent-foreground" title="Currently in memory">loaded · {l.processor}</Chip>}
                  {session?.model === m.name && <Chip cls="bg-ok/15 text-ok">this session</Chip>}</div>
                <div className="truncate text-[10px] text-muted">{fmtBytes(m.size)}{m.parameter_size ? ` · ${m.parameter_size}` : ""}{m.quantization ? ` · ${m.quantization}` : ""}{fam ? ` · ${fam.title}${fam.name !== m.name.split(":")[0] ? " (variant)" : ""}` : " · not in the guide"}</div>
              </div>
              {l && <Button size="sm" variant="outline" title="Free this model's memory now" onClick={() => void unloadModel(m.name)}><PowerOff size={11} /> Unload</Button>}
              <Button size="sm" variant="secondary" disabled={!project || notChat || session?.model === m.name}
                title={!project ? "Open a project first" : notChat ? "Embedding model: cannot chat" : "Use this model for the current session"} onClick={() => void setModel(m.name)}>Use</Button>
              <UninstallButton name={m.name} size={m.size} />
            </div>);
        })}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- version rows */
export function TagRow({ t, family, ov, dl }: { t: CatalogModel["tags"][number]; family: string; ov: Overview; dl?: DownloadRow }) {
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
