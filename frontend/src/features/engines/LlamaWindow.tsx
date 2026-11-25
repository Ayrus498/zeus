import { useCallback, useEffect, useRef, useState } from "react";
import * as D from "@radix-ui/react-dialog";
import { Check, CircleHelp, Cpu, Download, FolderPlus, Loader2, Play, RefreshCw, Save, Settings2, Square, Trash2, X } from "lucide-react";
import { api } from "../../services/api";
import { Button } from "../../components/ui/button";
import { ExternalLink } from "../../components/ui/external-link";
import { useApp } from "../../stores/app";
import { cn, fmtBytes, fmtCtx } from "../../lib/utils";
import type { DownloadRow, HfFiles, HfSearch, LlamaModel, LlamaOverview, LlamaSettings } from "../../services/types";
import { FitChip } from "../benchmarks/ArenaPanel";

type Tab = "models" | "engine" | "download" | "server";
const TABS: { id: Tab; label: string }[] = [{ id: "models", label: "Models" }, { id: "engine", label: "Engine & folders" }, { id: "download", label: "Download" }, { id: "server", label: "Server" }];
const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

// ------------------------------------------------------------------ settings form (defaults and per-model overrides)
interface Field { key: keyof LlamaSettings; label: string; kind: "number" | "ngl" | "select" | "text"; options?: string[]; hint: string }
const FIELDS: Field[] = [
  { key: "ctx", label: "Context window", kind: "number", hint: "Tokens the server reserves. 0 follows the conversation's context setting; a fixed value never restarts the model." },
  { key: "ngl", label: "GPU layers", kind: "ngl", hint: "auto lets llama.cpp decide, all offloads everything, a number offloads that many layers. Lower it if you run out of GPU memory." },
  { key: "threads", label: "CPU threads", kind: "number", hint: "0 = automatic." },
  { key: "flash_attn", label: "Flash attention", kind: "select", options: ["auto", "on", "off"], hint: "Faster attention; needed for a quantized V cache. Requires a recent llama.cpp." },
  { key: "cache_type_k", label: "KV cache type (K)", kind: "select", options: ["", "f16", "q8_0", "q4_0"], hint: "Quantizing the cache fits longer context in less memory. q8_0 is nearly lossless. Empty = llama.cpp default." },
  { key: "cache_type_v", label: "KV cache type (V)", kind: "select", options: ["", "f16", "q8_0", "q4_0"], hint: "Same for the V cache; quantizing it needs flash attention on." },
  { key: "batch", label: "Batch size", kind: "number", hint: "Tokens processed per step while reading the prompt. 0 = default." },
  { key: "ubatch", label: "Micro-batch size", kind: "number", hint: "Physical batch size. 0 = default." },
  { key: "extra_args", label: "Extra arguments", kind: "text", hint: "Any other llama-server flags, for example --no-mmap or --rope-scaling yarn." },
];
const inputCls = "h-8 w-full rounded-md border border-border bg-surface-2 px-2 text-xs outline-none focus:border-accent";

function SettingsForm({ value, onChange, base }: { value: Partial<LlamaSettings>; onChange: (p: Partial<LlamaSettings>) => void; base?: LlamaSettings }) {
  const get = (k: keyof LlamaSettings) => (value[k] ?? "") as string | number;
  return (
    <div className="grid gap-x-4 gap-y-2 sm:grid-cols-2" data-testid="llama-settings-form">
      {FIELDS.map((f) => {
        const inherited = base && value[f.key] === undefined ? `default: ${String(base[f.key] === "" ? "llama.cpp" : base[f.key])}` : undefined;
        return (
          <label key={f.key} className={cn("block text-[11px]", f.kind === "text" && "sm:col-span-2")} title={f.hint}>
            <span className="mb-0.5 block text-muted">{f.label}</span>
            {f.kind === "select" ? (
              <select aria-label={f.label} className={inputCls} value={String(get(f.key))} onChange={(e) => onChange({ [f.key]: e.target.value })}>
                {base && <option value="" hidden>{inherited}</option>}
                {f.options!.map((o) => <option key={o} value={o}>{o === "" ? "llama.cpp default" : o}</option>)}
              </select>
            ) : f.kind === "ngl" ? (
              <input aria-label={f.label} className={inputCls} placeholder={inherited ?? "auto, all or a number"} value={String(get(f.key))} onChange={(e) => onChange({ ngl: e.target.value.trim().toLowerCase() })} />
            ) : f.kind === "number" ? (
              <input aria-label={f.label} type="number" min={0} className={inputCls} placeholder={inherited ?? "0"} value={String(get(f.key))}
                onChange={(e) => onChange({ [f.key]: e.target.value === "" ? 0 : Number(e.target.value) })} />
            ) : (
              <input aria-label={f.label} className={cn(inputCls, "font-mono")} placeholder={inherited ?? "--flag value"} value={String(get(f.key))} onChange={(e) => onChange({ extra_args: e.target.value })} />
            )}
            <span className="mt-0.5 block text-[10px] text-muted/80">{f.hint}</span>
          </label>);
      })}
    </div>
  );
}

// ------------------------------------------------------------------ models tab
function ModelRow({ m, ov, refresh }: { m: LlamaModel; ov: LlamaOverview; refresh: () => Promise<void> }) {
  const { engine, setEngine, setModel, session, project, toast, setLlamaOpen } = useApp();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Partial<LlamaSettings>>({});
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const own = ov.config.models[m.name];
  const running = ov.server.running && ov.server.model === m.name;
  const loading = ov.server.starting === m.name;
  const inUse = engine() === "llamacpp" && session?.model === m.name;
  useEffect(() => { if (open) setDraft(own ?? {}); }, [open]);        // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (fn: () => Promise<unknown>, ok?: string) => { setBusy(true); try { await fn(); if (ok) toast(ok, "success"); await refresh(); } catch (e) { toast(err(e), "error"); } finally { setBusy(false); } };
  const use = () => run(async () => {
    if (!project) throw new Error("Open a project first, then choose a model for it.");
    if (engine() !== "llamacpp") await setEngine("llamacpp");
    await setModel(m.name);
    setLlamaOpen(false);
  }, `${m.name} selected.`);
  return (
    <div className="rounded-md border border-border bg-surface" data-testid={`llama-model-${m.name}`}>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="truncate font-mono text-sm">{m.name}</span>
            {inUse && <Chip cls="bg-ok/15 text-ok">this conversation</Chip>}
            {running && <Chip cls="bg-accent text-accent-foreground">loaded</Chip>}
            {loading && <Chip cls="bg-warn/15 text-warn"><Loader2 size={10} className="mr-1 inline animate-spin" />loading…</Chip>}
            {m.tools && <Chip cls="bg-accent/15 text-accent" title="The chat template supports tool calling">tools</Chip>}
            {m.thinking && <Chip cls="bg-accent/15 text-accent">thinking</Chip>}
            {m.embedding && <Chip cls="bg-warn/15 text-warn" title="Embedding models cannot chat">embedding</Chip>}
            {m.source === "managed" && <Chip title={`Downloaded by Zeus-Agent: ${m.path}`}>downloaded</Chip>}
            {m.source === "hf_cache" && <Chip title={`In Hugging Face's cache: ${m.path}`}>Hugging Face cache</Chip>}
            {m.source === "llama_cache" && <Chip title={`In llama.cpp's cache: ${m.path}`}>llama.cpp cache</Chip>}
            {m.source === "folder" && <Chip title={`In your folder: ${m.path}`}>your folder</Chip>}
            {m.source === "ollama" && <Chip cls="bg-accent/15 text-accent" title="This is the file Ollama downloaded. llama.cpp reads it in place: no copy, no extra disk space.">from Ollama · shared</Chip>}
            {own && Object.keys(own).length > 0 && <Chip title="This model has its own settings">custom settings</Chip>}
          </div>
          <div className="truncate text-[10px] text-muted" title={m.path}>
            {[fmtBytes(m.size), m.parameter_size, m.quantization, m.family, m.context_length ? `${fmtCtx(m.context_length)} trained context` : null].filter(Boolean).join(" · ")} · {m.path}
          </div>
        </div>
        <Button size="sm" variant="secondary" disabled={busy || m.embedding} onClick={() => void use()} title={m.embedding ? "Embedding models cannot chat" : "Switch this conversation to llama.cpp and use this model"}>
          <Check size={11} /> Use</Button>
        {running
          ? <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(() => api.llamaStop(), `${m.name} unloaded.`)} title="Free the model's memory now"><Square size={11} /> Unload</Button>
          : <Button size="sm" variant="outline" disabled={busy || loading || m.embedding} onClick={() => void run(() => api.llamaStart(m.name))} title="Load it now instead of on the first message"><Play size={11} /> Load</Button>}
        <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)} title="Settings for this model"><Settings2 size={11} /></Button>
        {m.removable && !confirm && <Button size="sm" variant="ghost" onClick={() => setConfirm(true)} title="Uninstall this model (delete its file)" aria-label={`Uninstall ${m.name}`} data-testid={`uninstall-${m.name}`}><Trash2 size={11} /></Button>}
      </div>
      {m.source === "ollama" && <div className="border-t border-border/60 px-3 py-1.5 text-[11px] text-muted" data-testid="ollama-note">Uses Ollama's copy of <span className="font-mono">{m.ollama_name}</span> in place (no extra disk space).
        To uninstall it, use Ollama: <span className="font-mono">ollama rm {m.ollama_name}</span> or Models → Uninstall.</div>}
      {confirm && m.removable && (
        <div className="space-y-1.5 border-t border-danger/40 bg-danger/5 px-3 py-2 text-[11px]" data-testid="uninstall-confirm">
          <div>Delete <span className="font-mono">{m.name}</span> from this computer? This frees <b>{fmtBytes(m.removable.bytes || m.size)}</b> and cannot be undone (you can download it again).</div>
          <ul className="max-h-20 overflow-auto font-mono text-[10px] text-muted">{m.removable.files.map((f) => <li key={f} className="truncate" title={f}>{f}</li>)}</ul>
          <div className="flex gap-2"><Button size="sm" variant="danger" disabled={busy} data-testid="uninstall-yes" onClick={() => void run(async () => { const r = await api.llamaDelete(m.name); setConfirm(false); toast(`${m.name} uninstalled, ${fmtBytes(r.deleted.bytes)} freed.`, "success"); }).then(() => setConfirm(false))}>Delete</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button></div>
        </div>)}
      {open && (
        <div className="space-y-2 border-t border-border/60 px-3 py-3" data-testid="llama-model-settings">
          <div className="text-[11px] text-muted">Settings for <span className="font-mono">{m.name}</span> override the defaults from Engine &amp; folders. Changing them reloads the model the next time it runs.</div>
          <SettingsForm value={draft} base={ov.config.defaults} onChange={(p) => setDraft({ ...draft, ...p })} />
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={() => void run(() => api.llamaConfig({ model: { name: m.name, settings: draft } }), "Saved.")}><Save size={11} /> Save</Button>
            <Button size="sm" variant="ghost" disabled={busy || !own} onClick={() => void run(async () => { await api.llamaConfig({ model: { name: m.name, settings: null } }); setDraft({}); }, "Back to the defaults.")}>Use the defaults</Button>
          </div>
        </div>)}
    </div>
  );
}

const Chip = ({ children, cls, title }: { children: React.ReactNode; cls?: string; title?: string }) =>
  <span title={title} className={cn("rounded px-1.5 py-0.5 text-[10px]", cls ?? "bg-surface-2 text-muted")}>{children}</span>;

function ModelsTab({ ov, refresh, goto }: { ov: LlamaOverview; refresh: () => Promise<void>; goto: (t: Tab) => void }) {
  if (!ov.models.length) {
    return (
      <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm" data-testid="llama-no-models">
        <div className="font-medium">No GGUF models found yet</div>
        <p className="mx-auto mt-1 max-w-md text-xs text-muted">Zeus-Agent looks in {ov.folders.length} folder{ov.folders.length === 1 ? "" : "s"}. Add a folder that holds .gguf files, or download a model from Hugging Face.</p>
        <div className="mt-3 flex justify-center gap-2"><Button size="sm" onClick={() => goto("download")}><Download size={12} /> Download a model</Button>
          <Button size="sm" variant="outline" onClick={() => goto("engine")}><FolderPlus size={12} /> Add a folder</Button></div>
      </div>);
  }
  return <div className="space-y-2">{ov.models.map((m) => <ModelRow key={m.name} m={m} ov={ov} refresh={refresh} />)}</div>;
}

// ------------------------------------------------------------------ engine tab
function EngineTab({ ov, refresh }: { ov: LlamaOverview; refresh: () => Promise<void> }) {
  const { toast, engines } = useApp();
  const [bin, setBin] = useState<string | null>(null);
  const [defaults, setDefaults] = useState<Partial<LlamaSettings> | null>(null);
  const [dir, setDir] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async (patch: Record<string, unknown>, ok: string) => { setBusy(true); try { await api.llamaConfig(patch); toast(ok, "success"); await refresh(); } catch (e) { toast(err(e), "error"); } finally { setBusy(false); } };
  const st = ov.status;
  const devices = st.devices ?? [];
  return (
    <div className="space-y-5 text-xs">
      <section>
        <div className="mb-1 font-medium text-muted">llama-server</div>
        <div className="flex gap-2">
          <input aria-label="llama-server path" className={cn(inputCls, "font-mono")} placeholder="Leave empty to find it automatically (PATH, Downloads, Documents, /opt, build folders…)"
            value={bin ?? ov.config.binary} onChange={(e) => setBin(e.target.value)} />
          <Button size="sm" disabled={busy || bin === null} onClick={() => void save({ binary: (bin ?? "").trim() }, "Saved.").then(() => setBin(null))}><Save size={11} /> Save</Button>
        </div>
        <div className={cn("mt-1.5 text-[11px]", st.connected ? "text-ok" : "text-warn")} data-testid="llama-binary-status">
          {st.connected ? <>Found {st.binary} · {st.version ?? "version unknown"}</> : <>{st.error ?? "llama-server was not found."}</>}
        </div>
        {ov.binary_problem && <div className="mt-1 rounded-md border border-warn/40 bg-warn/10 px-2 py-1 text-[11px] text-warn" data-testid="llama-binary-problem">{ov.binary_problem}</div>}
        <div className="mt-3" data-testid="llama-found">
          <div className="mb-1 flex items-center gap-2 font-medium text-muted">Found on this computer
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setBusy(true); api.llamaRescan().then(refresh).catch((e) => toast(err(e), "error")).finally(() => setBusy(false)); }} title="Search the computer again (after installing or building llama.cpp)" data-testid="llama-rescan">
              <RefreshCw size={11} className={busy ? "animate-spin" : ""} /> Search again</Button>
            {!ov.binary_auto && <Button size="sm" variant="outline" disabled={busy} onClick={() => void save({ binary: "" }, "Using the automatic choice.")} data-testid="llama-use-auto">Use the automatic choice</Button>}</div>
          {ov.found.length === 0 ? <div className="text-[11px] text-muted">No llama-server was found in your usual places. Install llama.cpp (steps below), or type its full path above.</div> : (
            <div className="divide-y divide-border/50 rounded-md border border-border bg-surface px-3">
              {ov.found.map((f) => {
                const inUse = st.binary === f.path;
                return (
                  <div key={f.path} className="flex flex-wrap items-center gap-2 py-1.5" data-testid="llama-found-row">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono" title={f.path}>{f.path}</div>
                      <div className="text-[10px] text-muted">{f.version ?? "version unknown"} · {f.gpu ? <span className="text-ok">uses your GPU ({f.devices.join(", ")})</span> : "CPU only (no GPU support in this build)"} · in {f.source}</div>
                    </div>
                    {f.auto && ov.binary_auto && <Chip cls="bg-accent text-accent-foreground">automatic choice</Chip>}
                    {inUse ? <Chip cls="bg-ok/15 text-ok">in use</Chip>
                      : <Button size="sm" variant="secondary" disabled={busy} onClick={() => void save({ binary: f.path }, "Saved.")}>Use this one</Button>}
                  </div>);
              })}
            </div>)}
          {ov.found.length > 1 && ov.binary_auto && <div className="mt-1 text-[10px] text-muted">Several copies were found. The automatic choice prefers a build that can use your GPU, then the newest. Choose one to pin it.</div>}
        </div>
        {devices.length > 0 && <div className="mt-2 text-[11px] text-muted">GPU devices: {devices.map((d) => `${d.name} (${fmtBytes(d.memory_mib * 2 ** 20)})`).join(", ")}</div>}
        {st.connected && devices.length === 0 && <div className="mt-0.5 text-[11px] text-muted">No GPU device reported: this build runs on the CPU. A CUDA, Vulkan or ROCm build uses your GPU.</div>}
        {!st.connected && engines?.help && (
          <ol className="mt-2 list-decimal space-y-1 rounded-md border border-border bg-surface px-6 py-2 text-[11px] text-muted" data-testid="llama-install-steps">
            {engines.help.install.map((s) => <li key={s}>{s}</li>)}
            <li className="list-none"><ExternalLink href="https://github.com/ggml-org/llama.cpp/releases">github.com/ggml-org/llama.cpp/releases</ExternalLink></li>
          </ol>)}
      </section>
      <section>
        <div className="mb-1 font-medium text-muted">Model folders</div>
        <ul className="space-y-0.5">
          {ov.folders.map((f) => (
            <li key={f} className="flex items-center gap-2 rounded border border-border/60 bg-surface px-2 py-1 font-mono">
              <span className="min-w-0 flex-1 truncate" title={f}>{f}</span>
              {f === ov.managed_dir ? <Chip title="Where downloads are saved">downloads</Chip> : ov.config.models_dirs.includes(f)
                ? <button type="button" aria-label={`Remove ${f}`} className="cursor-pointer text-muted hover:text-danger" onClick={() => void save({ models_dirs: ov.config.models_dirs.filter((d) => d !== f) }, "Folder removed.")}><X size={12} /></button>
                : <Chip title="Searched automatically">built in</Chip>}
            </li>))}
        </ul>
        <div className="mt-2 flex gap-2">
          <input aria-label="Add a model folder" className={cn(inputCls, "font-mono")} placeholder="/home/you/models" value={dir} onChange={(e) => setDir(e.target.value)} />
          <Button size="sm" variant="secondary" disabled={busy || !dir.trim()} onClick={() => void save({ models_dirs: [...ov.config.models_dirs, dir.trim()] }, "Folder added.").then(() => setDir(""))}><FolderPlus size={11} /> Add</Button>
        </div>
        <div className="mt-1 text-[10px] text-muted">Folders are searched for .gguf files (up to 4 levels deep). Split models are listed once.</div>
      </section>
      <section>
        <label className="flex cursor-pointer items-start gap-2" data-testid="ollama-share">
          <input type="checkbox" className="mt-0.5" checked={ov.config.ollama_models} onChange={(e) => void save({ ollama_models: e.target.checked }, e.target.checked ? "Ollama's models are listed." : "Ollama's models are hidden.")} />
          <span><span className="font-medium text-muted">Also list the models Ollama has downloaded</span>
            <span className="block text-[10px] text-muted">llama.cpp reads Ollama's model files in place, read only: nothing is copied or changed, and Ollama keeps working as before.</span></span>
        </label>
      </section>
      <section>
        <div className="mb-1 font-medium text-muted">Default settings for every model</div>
        <SettingsForm value={defaults ?? ov.config.defaults} onChange={(p) => setDefaults({ ...(defaults ?? ov.config.defaults), ...p })} />
        <div className="mt-2"><Button size="sm" disabled={busy || !defaults} onClick={() => void save({ defaults }, "Defaults saved.").then(() => setDefaults(null))}><Save size={11} /> Save defaults</Button></div>
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ download tab
const REPO_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
const short = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n));

function DownloadTab({ ov, refresh }: { ov: LlamaOverview; refresh: () => Promise<void> }) {
  const { toast, cancelDownload, dismissDownload } = useApp();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<HfSearch | null>(null);
  const [searching, setSearching] = useState(false);
  const [found, setFound] = useState<HfFiles | null>(null);
  const [loadingRepo, setLoadingRepo] = useState<string | null>(null);
  const q = query.trim();
  // The most downloaded GGUF models first; typing searches Hugging Face after a short pause (only the typed text is sent).
  useEffect(() => {
    if (q.length === 1) return;
    setSearching(true);
    let stale = false;
    const t = setTimeout(() => {
      api.hfSearch(q).then((r) => { if (!stale) setResults(r); }).catch((e) => { if (!stale) { setResults({ query: q, results: [] }); toast(err(e), "error"); } }).finally(() => { if (!stale) setSearching(false); });
    }, q ? 450 : 0);
    return () => { stale = true; clearTimeout(t); };
  }, [q]);                                                       // eslint-disable-line react-hooks/exhaustive-deps
  const open = async (repo: string) => {
    setLoadingRepo(repo); setFound(null);
    try { setFound(await api.hfFiles(repo)); } catch (e) { toast(err(e), "error"); } finally { setLoadingRepo(null); }
  };
  const pull = async (file: string) => { try { await api.llamaDownload(found!.repo, file); toast(`Downloading ${file}…`); await refresh(); } catch (e) { toast(err(e), "error"); } };
  const active = (d: DownloadRow) => d.status === "queued" || d.status === "running";
  const exact = REPO_RE.test(q);
  return (
    <div className="space-y-4 text-xs">
      <section>
        <div className="mb-1 font-medium text-muted">Find a model on Hugging Face</div>
        <div className="flex gap-2">
          <input aria-label="Search Hugging Face" className={inputCls} placeholder="Search GGUF models (for example: qwen coder), or type owner/name" value={query} data-testid="hf-query"
            onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && exact) void open(q); }} />
          {exact && <Button size="sm" onClick={() => void open(q)} data-testid="hf-open">Open {q.split("/")[1]}</Button>}
        </div>
        <div className="mt-1 text-[10px] text-muted">{q ? "Results for what you typed" : "Most downloaded GGUF models"}. Only the text you type is sent to huggingface.co. Downloads go to {ov.managed_dir}. Pick a file below: the suggested one fits your hardware.</div>
      </section>
      <section data-testid="hf-results">
        {searching && !results ? <div className="flex items-center gap-2 text-muted"><Loader2 size={12} className="animate-spin" />Searching…</div>
          : results && results.results.length === 0 ? <div className="text-muted">No GGUF models found{results.query ? ` for “${results.query}”` : ""}.</div> : (
          <div className="divide-y divide-border/50 rounded-md border border-border bg-surface px-3">
            {results?.results.map((r) => (
              <button key={r.repo} type="button" onClick={() => void open(r.repo)} data-testid={`hf-repo-${r.repo}`} aria-current={found?.repo === r.repo ? "true" : undefined}
                className={cn("flex w-full cursor-pointer items-center gap-3 py-1.5 text-left hover:text-accent", found?.repo === r.repo && "text-accent")}>
                <span className="min-w-0 flex-1 truncate font-mono">{r.repo}</span>
                {loadingRepo === r.repo && <Loader2 size={11} className="animate-spin" />}
                <span className="w-16 text-right text-[10px] text-muted" title="Downloads">↓ {short(r.downloads)}</span>
                <span className="w-14 text-right text-[10px] text-muted" title="Likes">♥ {short(r.likes)}</span>
              </button>))}
          </div>)}
      </section>
      {found && (
        <section data-testid="hf-files">
          <div className="mb-1 flex items-center gap-2 font-medium text-muted">Files in {found.repo} <ExternalLink href={found.url}>open on huggingface.co</ExternalLink>
            <span className="ml-auto font-normal text-[10px]">{found.hardware.gpu ? `${found.hardware.gpu} · ${found.hardware.vram_gb} GB` : "no GPU"} · {found.hardware.ram_gb} GB RAM</span></div>
          {found.files.length === 0 ? <div className="text-muted">No .gguf files in this repository.</div> : (
            <div className="divide-y divide-border/50 rounded-md border border-border bg-surface px-3">
              {found.files.map((f) => (
                <div key={f.file} className={cn("flex flex-wrap items-center gap-2 py-1.5", f.file === found.suggested && "bg-accent/5")} data-testid={`hf-file-${f.file}`}>
                  <span className="min-w-0 flex-1 truncate font-mono" title={f.file}>{f.file}{f.file === found.suggested && <span className="ml-1.5 rounded bg-accent px-1 font-sans text-[9px] text-accent-foreground" title="The best quantization that runs well on this computer">suggested</span>}</span>
                  <span className="w-16 text-right font-mono text-muted">{fmtBytes(f.size)}</span>
                  <span className="w-16 text-center">{f.quant && <Chip>{f.quant}</Chip>}</span>
                  <span className="w-44 text-right">{f.split ? <Chip cls="bg-warn/15 text-warn" title="Split into several files">split model</Chip> : <FitChip fit={f.fit} />}</span>
                  <span className="flex w-28 justify-end">
                    {f.installed ? <Chip cls="bg-ok/15 text-ok"><Check size={10} className="mr-0.5 inline" />installed</Chip>
                      : f.split ? <Chip title="Download the parts by hand or use llama-server -hf">not here</Chip>
                      : <Button size="sm" variant={f.file === found.suggested ? "default" : "secondary"} onClick={() => void pull(f.file)} data-testid={`hf-download-${f.file}`}><Download size={11} /> Download</Button>}
                  </span>
                </div>))}
            </div>)}
        </section>)}
      {ov.downloads.length > 0 && (
        <section data-testid="llama-downloads">
          <div className="mb-1 font-medium text-muted">Downloads</div>
          <div className="space-y-1">
            {ov.downloads.map((d) => (
              <div key={d.id} className="rounded-md border border-border bg-surface px-3 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono" title={d.model}>{d.model.split("/").pop()}</span>
                  {active(d) ? <><span className="font-mono text-accent">{d.percent != null ? `${Math.round(d.percent)}%` : "…"}</span>
                      <Button size="sm" variant="ghost" onClick={() => void cancelDownload(d.id).then(refresh)}>Cancel</Button></>
                    : <><span className={cn("text-[11px]", d.status === "completed" ? "text-ok" : "text-danger")} title={d.error ?? undefined}>{d.status === "completed" ? "done" : d.error ?? d.status}</span>
                      <Button size="sm" variant="ghost" aria-label="Dismiss" onClick={() => void dismissDownload(d.id).then(refresh)}><X size={11} /></Button></>}
                </div>
                {active(d) && <div className="mt-1 h-1 overflow-hidden rounded bg-surface-2"><div className="h-1 rounded bg-accent transition-all" style={{ width: `${d.percent ?? 3}%` }} /></div>}
              </div>))}
          </div>
        </section>)}
    </div>
  );
}

// ------------------------------------------------------------------ server tab
function ServerTab({ ov, refresh }: { ov: LlamaOverview; refresh: () => Promise<void> }) {
  const { toast } = useApp();
  const [log, setLog] = useState("");
  const s = ov.server;
  const load = useCallback(() => { api.llamaLog(120).then((r) => setLog(r.log)).catch(() => undefined); }, []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);
  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center gap-3 rounded-md border border-border bg-surface px-3 py-2" data-testid="llama-server-status">
        <span className={cn("h-2 w-2 rounded-full", s.running ? "bg-ok" : s.starting ? "bg-warn" : "bg-muted")} />
        <div className="min-w-0 flex-1">
          {s.running ? <><span className="font-mono">{s.model}</span> is loaded · context {s.ctx ? fmtCtx(s.ctx) : "?"} · pid {s.pid} · port {s.port} · up {s.uptime ?? 0}s</>
            : s.starting ? <>Loading <span className="font-mono">{s.starting}</span>…</> : "No model is loaded. The server starts when you choose a model or send a message."}
        </div>
        <Button size="sm" variant="outline" disabled={!s.running && !s.starting} onClick={() => void api.llamaStop().then(refresh).catch((e) => toast(err(e), "error"))}><Square size={11} /> Stop</Button>
      </div>
      {ov.error && <div className="whitespace-pre-wrap rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-danger" data-testid="llama-error">{ov.error}</div>}
      <div>
        <div className="mb-1 flex items-center gap-2 font-medium text-muted">llama-server output <Button size="sm" variant="ghost" onClick={load}><RefreshCw size={11} /></Button></div>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-[#0d1117] p-2 font-mono text-[10px] text-[#c9d1d9]" data-testid="llama-log">{log || "(no output yet)"}</pre>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ the window
/** llama.cpp configuration: models you can run, the llama-server program and model folders, downloads, and the server itself. */
export function LlamaWindow() {
  const { llamaOpen, setLlamaOpen, setEngineHelpOpen, refreshEngines, engines } = useApp();
  const [tab, setTab] = useState<Tab>("models");
  const [ov, setOv] = useState<LlamaOverview | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const first = useRef(true);
  const refresh = useCallback(async () => { try { setOv(await api.llama()); setLoadErr(null); } catch (e) { setLoadErr(err(e)); } }, []);
  useEffect(() => {
    if (!llamaOpen) { first.current = true; return; }
    void refresh(); void refreshEngines();
    const t = setInterval(() => void refresh(), 2000);                       // model loading and downloads change while the window is open
    return () => clearInterval(t);
  }, [llamaOpen, refresh, refreshEngines]);
  useEffect(() => {                                                            // open on the tab that helps: Engine when llama-server is missing
    if (ov && first.current) { first.current = false; setTab(ov.status.connected ? "models" : "engine"); }
  }, [ov]);
  const ll = engines?.engines.find((e) => e.id === "llamacpp");
  return (
    <D.Root open={llamaOpen} onOpenChange={setLlamaOpen}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <D.Content aria-describedby={undefined} data-testid="llama-window"
          className="fixed left-1/2 top-1/2 z-50 flex h-[min(760px,92vh)] w-[min(1000px,96vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-background shadow-2xl">
          <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
            <Cpu size={16} className="text-accent" />
            <D.Title className="text-sm font-semibold">llama.cpp</D.Title>
            <span className="flex items-center gap-1.5 text-xs text-muted"><span className={cn("h-2 w-2 rounded-full", ov?.status.connected ? "bg-ok" : "bg-danger")} />
              {ov ? (ov.status.connected ? `llama-server ${ov.status.version ?? ""} · ${ov.models.length} model${ov.models.length === 1 ? "" : "s"}` : "llama-server not found") : "Reading llama.cpp…"}</span>
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setEngineHelpOpen(true)}><CircleHelp size={12} /> Help me choose</Button>
            <D.Close className="cursor-pointer rounded p-1 text-muted hover:bg-surface-2 hover:text-foreground" aria-label="Close"><X size={16} /></D.Close>
          </header>
          <nav className="flex gap-1 border-b border-border px-3 pt-2" role="tablist">
            {TABS.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} data-testid={`llama-tab-${t.id}`} onClick={() => setTab(t.id)}
                className={cn("cursor-pointer rounded-t-md border-b-2 px-3 py-1.5 text-xs", tab === t.id ? "border-accent text-foreground" : "border-transparent text-muted hover:text-foreground")}>
                {t.label}{t.id === "models" && ov ? ` (${ov.models.length})` : ""}</button>))}
          </nav>
          <main className="min-h-0 flex-1 overflow-auto p-4">
            {loadErr && <div className="mb-3 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">Could not read llama.cpp state: {loadErr}</div>}
            {!ov ? <div className="text-xs text-muted">Reading llama.cpp…</div> : (
              <>
                {ov.error && tab !== "server" && <div className="mb-3 whitespace-pre-wrap rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">{ov.error.split("\n")[0]} <button type="button" className="cursor-pointer underline" onClick={() => setTab("server")}>see the log</button></div>}
                {tab === "models" && <ModelsTab ov={ov} refresh={refresh} goto={setTab} />}
                {tab === "engine" && <EngineTab ov={ov} refresh={refresh} />}
                {tab === "download" && <DownloadTab ov={ov} refresh={refresh} />}
                {tab === "server" && <ServerTab ov={ov} refresh={refresh} />}
              </>)}
          </main>
          <footer className="border-t border-border px-4 py-1.5 text-[10px] text-muted">
            One model is loaded at a time and freed when you switch model or quit. {ll?.available ? "" : "Set the llama-server path in Engine & folders to begin."}
          </footer>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
