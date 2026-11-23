import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Globe, Loader2, PowerOff, RefreshCw } from "lucide-react";
import { api } from "../../services/api";
import { CopyButton } from "../../components/ui/copy";
import { Button } from "../../components/ui/button";
import { ExternalLink } from "../../components/ui/external-link";
import { useApp } from "../../stores/app";
import { fmtBytes } from "../../lib/utils";
import type { Catalog, LibraryInfo, ModelInfo, Overview } from "../../services/types";
import type { Item } from "../../lib/modelGroups";
import { ACTIVE, Chip, FIT, norm, TagRow, UninstallButton } from "./ModelParts";

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
      <UninstallButton name={m.name} size={m.size} />
    </div>
  );
}

const MD_COMPONENTS: Components = {
  a: ({ href, children }) => (href ? <ExternalLink href={href}>{children}</ExternalLink> : <>{children}</>),
  img: () => null,
};
const REMARK = [remarkGfm];

const SOURCE_LABEL: Record<LibraryInfo["source"], string> = {
  live: "Fetched just now from ollama.com", cache: "From ollama.com (cached for a few hours)",
  "offline-cache": "Offline: showing the last copy fetched from ollama.com", offline: "Offline",
};

/** Latest public details for a model family, fetched from ollama.com when the model is selected. */
function LiveDetails({ family }: { family: string }) {
  const [info, setInfo] = useState<LibraryInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async (refresh: boolean) => {
    setBusy(true); setErr(null);
    try { setInfo(await api.library(family, refresh)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, [family]);
  useEffect(() => { setInfo(null); void load(false); }, [load]);
  const none = info && !info.summary && !info.readme && !info.tags.length;
  return (
    <section data-testid="live-details">
      <div className="mb-1 flex items-center gap-2 text-xs font-medium text-muted">
        <Globe size={12} /> Latest from ollama.com
        <Button size="sm" variant="ghost" className="ml-auto h-6" disabled={busy} title="Fetch the newest details now" onClick={() => void load(true)}>
          {busy ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} Refresh</Button>
      </div>
      <div className="space-y-2 rounded-md border border-border bg-surface px-3 py-2 text-xs">
        {busy && !info && <div className="text-muted">Fetching the latest details…</div>}
        {err && <div className="text-danger">Could not load details: {err}</div>}
        {info && (
          <>
            <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted">
              <span>{SOURCE_LABEL[info.source]}</span>{info.updated && <span>· model updated {info.updated}</span>}
              {info.capabilities.map((c) => <Chip key={c} cls="bg-accent/15 text-accent">{c}</Chip>)}
            </div>
            {info.error && <div className="text-warn">{info.error}{info.source !== "offline" ? "" : " Showing the built-in guide only."}</div>}
            {info.summary && <p className="text-sm">{info.summary}</p>}
            {none && !info.error && <div className="text-muted">ollama.com has no extra details for this model.</div>}
            {info.tags.length > 0 && (
              <details>
                <summary className="cursor-pointer text-muted hover:text-foreground">Available versions on ollama.com ({info.tags.length})</summary>
                <table className="mt-1 w-full text-[11px]"><tbody>
                  {info.tags.map((t) => (
                    <tr key={t.tag} className="border-t border-border/50"><td className="py-0.5 pr-2 font-mono">{t.tag}</td>
                      <td className="pr-2 text-muted">{t.size ?? ""}</td><td className="pr-2 text-muted">{t.context ? `${t.context} ctx` : ""}</td>
                      <td className="text-muted">{t.kind ?? ""}</td></tr>))}
                </tbody></table>
              </details>)}
            {info.readme && (
              <details data-testid="live-readme">
                <summary className="cursor-pointer text-muted hover:text-foreground">Model notes (README from ollama.com)</summary>
                <div className="prose-zeus mt-1 max-h-72 overflow-y-auto pr-1">
                  <ReactMarkdown remarkPlugins={REMARK} components={MD_COMPONENTS}>{info.readme}</ReactMarkdown></div>
              </details>)}
          </>)}
      </div>
    </section>
  );
}

/** Library family for an installed-only model ("qwen3.5:9b" -> "qwen3.5"); namespaced / hf.co models are not in the library. */
const libraryFamily = (name: string) => { const b = name.split(":")[0]; return /^[a-z0-9][a-z0-9._-]*$/.test(b) ? b : null; };

function Examples({ items }: { items: { prompt: string; shows: string }[] }) {
  return (
    <section data-testid="model-examples">
      <div className="mb-1 text-xs font-medium text-muted">Examples <span className="font-normal">(paste into the Zeus-Agent prompt)</span></div>
      <div className="space-y-1.5">
        {items.map((e) => (
          <div key={e.prompt} className="rounded-md border border-border bg-surface px-3 py-2 text-xs">
            <div className="flex items-start gap-2"><code className="min-w-0 flex-1 whitespace-pre-wrap break-words font-mono text-[11px]">{e.prompt}</code><CopyButton getText={() => e.prompt} /></div>
            <div className="mt-1 text-[11px] text-muted">→ {e.shows}</div>
          </div>))}
      </div>
    </section>
  );
}

export function ModelDetail({ item, ov, catalog }: { item: Item; ov: Overview; catalog: Catalog }) {
  if (item.kind === "installed") {
    const m = item.info;
    return (
      <div className="space-y-3" data-testid="model-detail">
        <div><div className="flex flex-wrap items-center gap-2"><h3 className="font-mono text-base">{m.name}</h3><Chip cls="bg-ok/15 text-ok"><Check size={10} className="mr-0.5 inline" />installed</Chip></div>
          <p className="mt-1 text-xs text-muted">This model is installed on your machine but is not part of Zeus-Agent's model guide, so there is no curated description. Zeus-Agent still reads its capabilities from Ollama when you use it.</p></div>
        <InstalledRow m={m} ov={ov} notChat={false} />
        {libraryFamily(m.name) && <LiveDetails family={libraryFamily(m.name)!} />}
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
      <p className="text-sm leading-relaxed" data-testid="model-about">{m.about}</p>
      <div className="text-xs"><span className="text-muted">Best for: </span>{m.best_for}</div>
      <div className="flex flex-wrap gap-1">
        {m.purposes.map((p) => <Chip key={p}>{catalog.purposes[p] ?? p}</Chip>)}
        {m.capabilities.map((c) => <Chip key={c} cls="bg-accent/15 text-accent">{c}</Chip>)}
        {m.context && <Chip title="Maximum context window">{m.context} ctx</Chip>}
      </div>
      {m.note && <div className="text-[11px] text-warn">{m.note}</div>}
      <Examples items={m.examples} />
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
      <LiveDetails family={m.name} />
      <div className="text-[11px]"><ExternalLink href={m.url}>{`ollama.com/library/${m.name}`}</ExternalLink></div>
    </div>
  );
}
