import type { Catalog, CatalogModel, Fit, LibraryEntry, ModelInfo, Overview } from "../services/types";

/** Left-panel families of the Models window. Order here is the display order. */
export const GROUPS: Array<{ id: string; label: string }> = [
  { id: "qwen", label: "Qwen" }, { id: "deepseek", label: "DeepSeek" }, { id: "llama", label: "Llama" }, { id: "gemma", label: "Gemma" },
  { id: "mistral", label: "Mistral" }, { id: "phi", label: "Phi" }, { id: "openai", label: "OpenAI" }, { id: "granite", label: "IBM Granite" },
  { id: "embeddings", label: "Embeddings" }, { id: "other", label: "Other" },
  { id: "library", label: "Ollama library" },                // every other model on ollama.com/library, plus search results
];

const bare = (name: string) => name.toLowerCase();
const norm = (n: string) => (n.includes(":") ? n : `${n}:latest`);

/** Which family a model (catalog name or any installed model name, including hf.co/... ones) belongs to. */
export function groupOf(name: string): string {
  const n = bare(name);
  if (/embed|(^|[/-])bge-|minilm/.test(n)) return "embeddings";
  if (/deepseek/.test(n)) return "deepseek";                        // before qwen: "DeepSeek-R1-0528-Qwen3-8B" is a DeepSeek model
  if (/qwen|qwq/.test(n)) return "qwen";
  if (/^tinyllama|llava/.test(n)) return "other";
  if (/llama/.test(n)) return "llama";
  if (/gemma/.test(n)) return "gemma";
  if (/mistral|mixtral|devstral|codestral/.test(n)) return "mistral";
  if (/(^|\/)phi\d?/.test(n)) return "phi";
  if (/gpt-oss/.test(n)) return "openai";
  if (/granite/.test(n)) return "granite";
  return "other";
}

/** Longest catalog family that prefixes an installed name ('qwen3-coder-16k:latest' -> qwen3-coder); vision/embedding variants are not folded in. */
export function catalogFamily(name: string, catalog: CatalogModel[]): CatalogModel | null {
  const base = bare(name).split(":")[0].split("/").pop() ?? "";
  let best: CatalogModel | null = null;
  for (const m of catalog) {
    const f = m.name;
    const next = base[f.length];
    if ((base === f || (base.startsWith(f) && (next === "-" || next === "_"))) && (!best || f.length > best.name.length)) {
      if (/(^|[-_])(vl|vision|embed)/.test(base.slice(f.length))) continue;
      best = m;
    }
  }
  return best;
}

export type Item =
  | { key: string; kind: "catalog"; label: string; group: string; model: CatalogModel; installed: ModelInfo[]; loaded: boolean }
  | { key: string; kind: "installed"; label: string; group: string; info: ModelInfo; installed: ModelInfo[]; loaded: boolean }
  | { key: string; kind: "library"; label: string; group: string; entry: LibraryEntry; installed: ModelInfo[]; loaded: boolean };

export interface GroupView { id: string; label: string; items: Item[] }

const FIT_ORDER: Record<Fit, number> = { best: 0, good: 1, limited: 2, no: 3 };

/** The base name of an installed model ("ornith:9b" -> "ornith", "user/model:q4" -> "user/model"). */
const baseName = (name: string) => bare(name).split(":")[0];

/** Every catalog model, every library model the guide does not cover (the public Ollama library and any search results), and every
 * installed model that neither covers; installed models are attached to the family they belong to. */
export function buildItems(catalog: Catalog | null, ov: Overview | null, library: LibraryEntry[] = [], found: LibraryEntry[] = []): Item[] {
  const models = catalog?.models ?? [];
  const installed = ov?.installed ?? [];
  const loaded = new Set((ov?.loaded ?? []).map((l) => norm(l.name)));
  const byFamily = new Map<string, ModelInfo[]>();
  const orphans: ModelInfo[] = [];
  for (const m of installed) {
    const fam = catalogFamily(m.name, models);
    if (fam) byFamily.set(fam.name, [...(byFamily.get(fam.name) ?? []), m]);
    else orphans.push(m);
  }
  const items: Item[] = models.map((m) => {
    const inst = byFamily.get(m.name) ?? [];
    return { key: `cat:${m.name}`, kind: "catalog", label: m.title, group: groupOf(m.name), model: m, installed: inst, loaded: inst.some((i) => loaded.has(norm(i.name))) };
  });
  const covered = new Set(models.map((m) => bare(m.name)));
  const taken = new Set<string>();
  for (const e of [...library, ...found]) {
    const id = bare(e.name);
    if (covered.has(id)) continue;                                       // the curated guide already describes it
    covered.add(id);
    const inst = orphans.filter((o) => baseName(o.name) === id);
    inst.forEach((o) => taken.add(o.name));
    items.push({ key: `lib:${id}`, kind: "library", label: e.name, group: "library", entry: e, installed: inst, loaded: inst.some((i) => loaded.has(norm(i.name))) });
  }
  for (const o of orphans) {
    if (taken.has(o.name)) continue;
    items.push({ key: `inst:${o.name}`, kind: "installed", label: o.name, group: groupOf(o.name), info: o, installed: [o], loaded: loaded.has(norm(o.name)) });
  }
  return items;
}

const haystack = (it: Item): string =>
  it.kind === "catalog"
    ? [it.model.name, it.model.title, it.model.maker, it.model.description, it.model.best_for, ...it.model.purposes, ...it.model.capabilities, ...it.model.tags.map((t) => t.tag), ...it.installed.map((i) => i.name)].join(" ").toLowerCase()
    : it.kind === "library"
      ? [it.entry.name, it.entry.description, ...it.entry.capabilities, ...it.entry.sizes, it.entry.community ? "community" : "official"].join(" ").toLowerCase()
      : [it.info.name, it.info.family ?? "", it.info.parameter_size ?? "", it.info.quantization ?? ""].join(" ").toLowerCase();

/** Apply the search box and the "installed only" toggle, group, and order (best fit first; installed-only models last). */
export function groupItems(items: Item[], query: string, installedOnly: boolean): GroupView[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const keep = items.filter((it) => (!installedOnly || it.installed.length > 0) && terms.every((t) => haystack(it).includes(t)));
  const q = query.trim().toLowerCase();
  /** Library order: a name that is the search, then one that starts with it, then the most pulled. */
  const libRank = (e: LibraryEntry) => (q && e.name.toLowerCase() === q ? 0 : q && e.name.toLowerCase().startsWith(q) ? 1 : 2);
  return GROUPS.map((g) => ({
    id: g.id, label: g.label,
    items: keep.filter((it) => it.group === g.id).sort((a, b) => {
      if (a.kind === "library" && b.kind === "library") return libRank(a.entry) - libRank(b.entry) || Number(a.entry.community) - Number(b.entry.community) || b.entry.pulls_n - a.entry.pulls_n || a.label.localeCompare(b.label);
      if (a.kind !== b.kind) return a.kind === "catalog" ? -1 : 1;
      if (a.kind === "catalog" && b.kind === "catalog") return FIT_ORDER[a.model.fit] - FIT_ORDER[b.model.fit] || a.label.localeCompare(b.label);
      return a.label.localeCompare(b.label);
    }),
  })).filter((g) => g.items.length > 0);
}
