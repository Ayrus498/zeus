import { useEffect, useMemo, useRef, useState } from "react";
import * as D from "@radix-ui/react-dialog";
import { Award, ChevronDown, ChevronRight, Boxes, ChevronsDownUp, ChevronsUpDown, Loader2, RefreshCw, Search, X } from "lucide-react";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";
import { buildItems, groupItems, type Item } from "../../lib/modelGroups";
import type { Catalog, LibraryList, LibrarySearch } from "../../services/types";
import { ArenaPanel } from "../benchmarks/ArenaPanel";
import { AutoModeCard } from "./AutoModeCard";
import { ModelDetail } from "./ModelDetail";
import { ACTIVE, Chip, Downloads, FIT, Installed } from "./ModelParts";
import { SetupGuide } from "./SetupGuide";

const STORE_KEY = "zeus.models.collapsed";
const OVERVIEW = "overview";
const ARENA = "arena";

/** Families start expanded, except the long Ollama library list, until the user chooses otherwise. */
function loadCollapsed(): Set<string> {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw === null) return new Set(["library"]);
    const v = JSON.parse(raw);
    return new Set(Array.isArray(v) ? v.filter((x) => typeof x === "string") : []);
  } catch { return new Set(["library"]); }
}
function saveCollapsed(s: Set<string>) { try { localStorage.setItem(STORE_KEY, JSON.stringify([...s])); } catch { /* storage unavailable */ } }

function Row({ item, selected, onSelect, downloading }: { item: Item; selected: boolean; onSelect: () => void; downloading: boolean }) {
  const fit = item.kind === "catalog" ? item.model.fit : null;
  return (
    <button type="button" onClick={onSelect} aria-current={selected ? "true" : undefined} data-testid={`row-${item.key}`}
      className={cn("flex w-full cursor-pointer items-center gap-2 rounded-md py-1.5 pl-7 pr-2 text-left text-xs outline-none focus-visible:ring-1 focus-visible:ring-accent", selected ? "bg-accent/20 text-foreground" : "text-foreground/90 hover:bg-surface-2")}>
      <span className={cn("min-w-0 flex-1 truncate", item.kind === "installed" && "font-mono")}>{item.label}</span>
      {downloading && <Loader2 size={11} className="shrink-0 animate-spin text-accent" aria-label="downloading" />}
      {item.loaded && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" title="Loaded in memory" />}
      {item.kind === "library" && item.entry.community && <span className="shrink-0 rounded bg-warn/15 px-1 text-[9px] text-warn" title="Published by a Ollama user, not by Ollama">community</span>}
      {item.kind === "library" && item.entry.capabilities.includes("tools") && <span className="shrink-0 text-[9px] text-muted" title="Supports tool calling">tools</span>}
      {item.installed.length > 0 && <span className="shrink-0 text-[10px] text-ok" title={`${item.installed.length} installed`}>✓{item.installed.length > 1 ? item.installed.length : ""}</span>}
      {fit === "best" && <span className="shrink-0 rounded bg-accent px-1 text-[9px] text-accent-foreground" title={FIT.best.hint}>best</span>}
    </button>
  );
}

/** Models window: search top-left, collapsible families on the left, details on the right. Opened from Settings. */
export function ModelsWindow() {
  const { modelsOpen, setModelsOpen, overview: ov, refreshOverview, modelsTarget } = useApp();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catErr, setCatErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [installedOnly, setInstalledOnly] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed);
  const [sel, setSel] = useState<string>(OVERVIEW);
  const [library, setLibrary] = useState<LibraryList | null>(null);
  const [libBusy, setLibBusy] = useState(false);
  const [remote, setRemote] = useState<LibrarySearch | null>(null);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [pinned, setPinned] = useState<Item | null>(null);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => { if (modelsOpen && modelsTarget) { setSel(modelsTarget); useApp.setState({ modelsTarget: null }); } }, [modelsOpen, modelsTarget]);   // /best opens straight at the benchmarks

  useEffect(() => {
    if (!modelsOpen) return;
    void refreshOverview();
    if (!catalog) api.catalog().then(setCatalog).catch((e) => setCatErr(String(e instanceof Error ? e.message : e)));
  }, [modelsOpen, refreshOverview, catalog]);

  // The whole public Ollama library (not only the built-in guide), loaded once per opening and refreshable.
  const loadLibrary = (refresh: boolean) => {
    setLibBusy(true);
    api.libraryList(refresh).then(setLibrary).catch((e) => setLibrary({ models: [], fetched_at: null, source: "offline", error: String(e instanceof Error ? e.message : e) })).finally(() => setLibBusy(false));
  };
  useEffect(() => { if (modelsOpen && !library) loadLibrary(false); }, [modelsOpen]);       // eslint-disable-line react-hooks/exhaustive-deps

  // What is typed here is also searched on ollama.com (official and community models), after a short pause.
  const typed = query.trim();
  useEffect(() => {
    if (!modelsOpen || typed.length < 2) { setRemote(null); setRemoteBusy(false); return; }
    setRemoteBusy(true);
    let stale = false;
    const t = setTimeout(() => {
      api.searchLibrary(typed).then((r) => { if (!stale) setRemote(r); }).catch((e) => { if (!stale) setRemote({ query: typed, models: [], source: "offline", error: String(e instanceof Error ? e.message : e) }); })
        .finally(() => { if (!stale) setRemoteBusy(false); });
    }, 400);
    return () => { stale = true; clearTimeout(t); };
  }, [typed, modelsOpen]);

  const found = useMemo(() => (remote && remote.query === typed ? remote.models : []), [remote, typed]);
  const items = useMemo(() => buildItems(catalog, ov, library?.models ?? [], found), [catalog, ov, library, found]);
  const groups = useMemo(() => groupItems(items, query, installedOnly), [items, query, installedOnly]);
  const searching = query.trim().length > 0;
  const isOpen = (id: string) => searching || !collapsed.has(id);          // a search always shows what it found
  const toggle = (id: string) => setCollapsed((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); saveCollapsed(n); return n; });
  const setAll = (collapse: boolean) => { const n = collapse ? new Set(groups.map((g) => g.id)) : new Set<string>(); setCollapsed(n); saveCollapsed(n); };

  const downloading = useMemo(() => new Set((ov?.downloads ?? []).filter((d) => ACTIVE.includes(d.status)).map((d) => d.model)), [ov]);
  const activeCount = downloading.size;
  const selected = items.find((i) => i.key === sel) ?? (pinned && pinned.key === sel ? pinned : null);   // a search result stays open after the search changes
  const isDownloading = (it: Item) => (it.kind === "catalog" && it.model.tags.some((t) => downloading.has(t.tag)))
    || (it.kind === "library" && [...downloading].some((d) => d === it.entry.name || d.startsWith(`${it.entry.name}:`)));

  return (
    <D.Root open={modelsOpen} onOpenChange={setModelsOpen}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <D.Content aria-describedby={undefined} onOpenAutoFocus={(e) => { e.preventDefault(); search.current?.focus(); }}
          className="fixed left-1/2 top-1/2 z-50 flex h-[min(800px,92vh)] w-[min(1180px,96vw)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-background shadow-2xl" data-testid="models-window">
          <header className="flex items-center gap-3 border-b border-border px-4 py-2.5">
            <Boxes size={16} className="text-accent" />
            <D.Title className="text-sm font-semibold">Models</D.Title>
            <span className="flex items-center gap-1.5 text-xs text-muted"><span className={cn("h-2 w-2 rounded-full", ov?.connected ? "bg-ok" : "bg-danger")} />
              {ov ? (ov.connected ? `Ollama ${ov.version ?? ""} · ${ov.installed.length} installed` : "Ollama not detected") : "Reading Ollama…"}</span>
            <D.Close className="ml-auto cursor-pointer rounded p-1 text-muted hover:bg-surface-2 hover:text-foreground" aria-label="Close"><X size={16} /></D.Close>
          </header>
          <div className="flex min-h-0 flex-1">
            {/* ------------------------------------------------ left: search + families */}
            <nav className="flex w-[300px] shrink-0 flex-col border-r border-border bg-surface/60" aria-label="Model families">
              <div className="space-y-2 border-b border-border p-3">
                <div className="relative">
                  <Search size={13} className="absolute left-2.5 top-2.5 text-muted" />
                  <input ref={search} value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search models" placeholder="Search models, e.g. coder, vision…"
                    className="h-9 w-full rounded-md border border-border bg-surface-2 pl-8 pr-7 text-xs outline-none focus:border-accent" />
                  {query && <button aria-label="Clear search" className="absolute right-2 top-2.5 cursor-pointer text-muted hover:text-foreground" onClick={() => { setQuery(""); search.current?.focus(); }}><X size={13} /></button>}
                </div>
                <div className="flex items-center gap-1.5 text-[11px]">
                  <button onClick={() => setInstalledOnly(!installedOnly)} aria-pressed={installedOnly}
                    className={cn("cursor-pointer rounded-full border px-2 py-0.5", installedOnly ? "border-ok bg-ok/20 text-ok" : "border-border text-muted hover:text-foreground")}>Installed only</button>
                  <span className="ml-auto flex gap-0.5">
                    <button title="Expand all" aria-label="Expand all families" className="cursor-pointer rounded p-1 text-muted hover:bg-surface-2 hover:text-foreground" onClick={() => setAll(false)}><ChevronsUpDown size={13} /></button>
                    <button title="Collapse all" aria-label="Collapse all families" className="cursor-pointer rounded p-1 text-muted hover:bg-surface-2 hover:text-foreground" onClick={() => setAll(true)}><ChevronsDownUp size={13} /></button>
                  </span>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-2">
                <button type="button" onClick={() => setSel(OVERVIEW)} aria-current={sel === OVERVIEW ? "true" : undefined} data-testid="row-overview"
                  className={cn("mb-1 flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium outline-none focus-visible:ring-1 focus-visible:ring-accent", sel === OVERVIEW ? "bg-accent/20" : "hover:bg-surface-2")}>
                  <Boxes size={13} className="text-accent" />Overview · Auto mode
                  {activeCount > 0 && <span className="ml-auto rounded bg-accent px-1.5 text-[10px] text-accent-foreground" title="Downloads in progress">{activeCount}</span>}
                </button>
                <button type="button" onClick={() => setSel(ARENA)} aria-current={sel === ARENA ? "true" : undefined} data-testid="row-arena"
                  className={cn("mb-1 flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium outline-none focus-visible:ring-1 focus-visible:ring-accent", sel === ARENA ? "bg-accent/20 text-foreground" : "text-muted hover:bg-surface-2 hover:text-foreground")}>
                  <Award size={13} className="text-accent" />Best for your hardware · benchmarks
                </button>
                {groups.map((g) => {
                  const open = isOpen(g.id);
                  const inst = g.items.filter((i) => i.installed.length > 0).length;
                  return (
                    <div key={g.id} className="mb-0.5" data-testid={`group-${g.id}`}>
                      <button type="button" onClick={() => toggle(g.id)} aria-expanded={open} disabled={searching}
                        className="flex w-full cursor-pointer items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-muted outline-none hover:bg-surface-2 hover:text-foreground focus-visible:ring-1 focus-visible:ring-accent disabled:cursor-default">
                        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}{g.label}
                        <span className="ml-auto flex items-center gap-1.5 font-mono text-[10px] font-normal normal-case">{inst > 0 && <span className="text-ok">{inst} installed</span>}<span>{g.items.length}</span></span>
                      </button>
                      {open && g.items.map((it) => <Row key={it.key} item={it} selected={sel === it.key} onSelect={() => { setSel(it.key); setPinned(it); }} downloading={isDownloading(it)} />)}
                    </div>);
                })}
                {catalog && !groups.length && <div className="px-3 py-6 text-center text-xs text-muted">No models match{query ? ` “${query}”` : ""}.</div>}
                {!catalog && <div className="px-3 py-6 text-center text-xs text-muted">{catErr ? <span className="text-danger">Could not load the model guide: {catErr}</span> : "Loading model guide…"}</div>}
              </div>
              <div className="space-y-1 border-t border-border px-3 py-1.5 text-[10px] text-muted" data-testid="library-status">
                {searching && remoteBusy && <div className="flex items-center gap-1"><Loader2 size={10} className="animate-spin" />Searching ollama.com…</div>}
                {searching && !remoteBusy && remote?.error && <div className="text-warn">{remote.error} Showing models already loaded.</div>}
                {searching && !remoteBusy && remote && !remote.error && <div>{found.length} result{found.length === 1 ? "" : "s"} on ollama.com for “{typed}”.</div>}
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 flex-1">
                    {libBusy && !library ? "Loading the Ollama library…"
                      : library && library.models.length ? `${library.models.length} models in the Ollama library${library.source === "offline-cache" ? " (offline copy)" : ""}`
                      : library?.error ? <span className="text-warn">Could not load the Ollama library: {library.error}</span> : ""}
                  </span>
                  <button type="button" onClick={() => loadLibrary(true)} disabled={libBusy} aria-label="Refresh the Ollama library" title="Fetch the newest list from ollama.com"
                    className="cursor-pointer rounded p-1 text-muted hover:bg-surface-2 hover:text-foreground disabled:opacity-50"><RefreshCw size={11} className={libBusy ? "animate-spin" : ""} /></button>
                </div>
                {catalog && <div>{catalog.note}</div>}
              </div>
            </nav>
            {/* ------------------------------------------------ right: details */}
            <main className="min-w-0 flex-1 overflow-auto p-5">
              {sel === ARENA ? <ArenaPanel /> : !ov ? <div className="text-xs text-muted">Reading Ollama state…</div> : (
                <div className="space-y-4">
                  <Downloads rows={ov.downloads} />
                  {sel === OVERVIEW || !selected || !catalog ? (
                    <>
                      {(!ov.connected || ov.installed.length === 0) && <SetupGuide connected={ov.connected} error={ov.error} endpoint={ov.endpoint} hasModels={ov.installed.length > 0} />}
                      <AutoModeCard />
                      <Installed ov={ov} catalog={catalog} />
                      {catalog && <p className="text-xs text-muted">Pick a family on the left to see what each model is good for and download it. <Chip cls="bg-accent text-accent-foreground">best</Chip> marks models that suit the Zeus-Agent agent best.</p>}
                    </>
                  ) : <ModelDetail item={selected} ov={ov} catalog={catalog} />}
                </div>)}
            </main>
          </div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
