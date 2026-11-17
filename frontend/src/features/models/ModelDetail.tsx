import { Check, PowerOff } from "lucide-react";
import { Button } from "../../components/ui/button";
import { ExternalLink } from "../../components/ui/external-link";
import { useApp } from "../../stores/app";
import { fmtBytes } from "../../lib/utils";
import type { Catalog, ModelInfo, Overview } from "../../services/types";
import type { Item } from "../../lib/modelGroups";
import { ACTIVE, Chip, FIT, norm, TagRow } from "./ModelParts";

/** Installed model row used inside a detail view: size info plus Unload / Use. */
function InstalledRow({ m, ov, notChat }: { m: ModelInfo; ov: Overview; notChat: boolean }) {
  const { unloadModel, setModel, session, project } = useApp();
  const l = ov.loaded.find((x) => norm(x.name) === norm(m.name));
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-2 text-xs">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5"><span className="truncate font-mono">{m.name}</span>
          {l && <Chip cls="bg-accent text-accent-foreground" title="Currently in memory">loaded · {l.processor}</Chip>}
          {session?.model === m.name && <Chip cls="bg-ok/15 text-ok">this session</Chip>}</div>
        <div className="truncate text-[10px] text-muted">{fmtBytes(m.size)}{m.parameter_size ? ` · ${m.parameter_size}` : ""}{m.quantization ? ` · ${m.quantization}` : ""}</div>
      </div>
      {l && <Button size="sm" variant="outline" title="Free this model's memory now" onClick={() => void unloadModel(m.name)}><PowerOff size={11} /> Unload</Button>}
      <Button size="sm" variant="secondary" disabled={!project || notChat || session?.model === m.name}
        title={!project ? "Open a project first" : notChat ? "Embedding model: cannot chat" : "Use this model for the current session"} onClick={() => void setModel(m.name)}>Use</Button>
    </div>
  );
}

export function ModelDetail({ item, ov, catalog }: { item: Item; ov: Overview; catalog: Catalog }) {
  if (item.kind === "installed") {
    const m = item.info;
    return (
      <div className="space-y-3" data-testid="model-detail">
        <div><div className="flex flex-wrap items-center gap-2"><h3 className="font-mono text-base">{m.name}</h3><Chip cls="bg-ok/15 text-ok"><Check size={10} className="mr-0.5 inline" />installed</Chip></div>
          <p className="mt-1 text-xs text-muted">This model is installed on your machine but is not part of ZEUS's model guide, so there is no curated description. ZEUS still reads its capabilities from Ollama when you use it.</p></div>
        <InstalledRow m={m} ov={ov} notChat={false} />
      </div>
    );
  }
  const m = item.model, f = FIT[m.fit];
  const dls = ov.downloads;
  return (
    <div className="space-y-3" data-testid="model-detail">
      <div>
        <div className="flex flex-wrap items-center gap-2"><h3 className="text-base font-semibold">{m.title}</h3><span className="text-xs text-muted">{m.maker}</span><Chip cls={f.cls} title={f.hint}>{f.text}</Chip></div>
        <p className="mt-1 text-sm text-muted">{m.description}</p>
      </div>
      <div className="text-xs"><span className="text-muted">Best for: </span>{m.best_for}</div>
      <div className="flex flex-wrap gap-1">
        {m.purposes.map((p) => <Chip key={p}>{catalog.purposes[p] ?? p}</Chip>)}
        {m.capabilities.map((c) => <Chip key={c} cls="bg-accent/15 text-accent">{c}</Chip>)}
        {m.context && <Chip title="Maximum context window">{m.context} ctx</Chip>}
      </div>
      {m.note && <div className="text-[11px] text-warn">{m.note}</div>}
      <section>
        <div className="mb-1 text-xs font-medium text-muted">Sizes <span className="font-normal">(download size, fit on this machine)</span></div>
        <div className="divide-y divide-border/50 rounded-md border border-border bg-surface px-3">
          {m.tags.map((t) => <TagRow key={t.tag} t={t} family={m.name} ov={ov} dl={dls.find((d) => d.model === t.tag && ACTIVE.concat(["failed", "interrupted"]).includes(d.status)) ?? dls.find((d) => d.model === t.tag)} />)}
        </div>
      </section>
      {item.installed.length > 0 && (
        <section>
          <div className="mb-1 text-xs font-medium text-muted">Installed on this machine <span className="font-mono text-[10px]">({item.installed.length})</span></div>
          <div className="space-y-1.5">{item.installed.map((i) => <InstalledRow key={i.name} m={i} ov={ov} notChat={m.fit === "no"} />)}</div>
        </section>)}
      <div className="text-[11px]"><ExternalLink href={m.url}>{`ollama.com/library/${m.name}`}</ExternalLink></div>
    </div>
  );
}
