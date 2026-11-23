import { describe, expect, it } from "vitest";
import { buildItems, catalogFamily, groupItems, groupOf } from "../lib/modelGroups";
import type { Catalog, CatalogModel, LibraryEntry, ModelInfo, Overview } from "../services/types";
import { parseSizeGb } from "../lib/modelFit";

const cm = (name: string, title: string, fit: CatalogModel["fit"] = "good", purposes: string[] = ["chat"]): CatalogModel => ({
  name, title, maker: "M", description: `${title} description`, best_for: "x", purposes, capabilities: [], fit, context: null, note: null,
  url: `https://ollama.com/library/${name}`, tags: [{ tag: `${name}:7b`, size_gb: 4, latest: true }], about: `${title} about`, examples: [],
});
const CATALOG: Catalog = { purposes: {}, library_url: "", note: "", models: [cm("qwen3", "Qwen3", "best"), cm("qwen3-coder", "Qwen3 Coder", "best", ["coding"]), cm("qwen2.5-coder", "Qwen2.5 Coder", "good"),
  cm("deepseek-r1", "DeepSeek-R1"), cm("llama3.2", "Llama 3.2"), cm("gemma3", "Gemma 3"), cm("nomic-embed-text", "Nomic Embed", "no")] };
const inst = (name: string, size = 1e9): ModelInfo => ({ name, size });
const ov = (names: string[], loaded: string[] = []): Overview => ({
  connected: true, endpoint: "", installed: names.map((n) => inst(n)), downloads: [], hardware: { gpu: null, ram_total: null, ram_available: null },
  loaded: loaded.map((n) => ({ name: n, size: 1, size_vram: 1, gpu_pct: 100, processor: "100% GPU" })),
});

describe("groupOf", () => {
  it.each([["qwen3-coder:30b", "qwen"], ["qwq:32b", "qwen"], ["qwen3.5:9b", "qwen"], ["deepseek-r1:14b", "deepseek"],
    ["hf.co/unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF:Q4_K_XL", "deepseek"], ["llama3.2-vision:11b", "llama"], ["codellama:7b", "llama"],
    ["tinyllama:1.1b", "other"], ["llava:7b", "other"], ["gemma3:4b", "gemma"], ["mistral-nemo:12b", "mistral"], ["devstral:24b", "mistral"],
    ["phi4:14b", "phi"], ["gpt-oss:20b", "openai"], ["granite-code:8b", "granite"], ["nomic-embed-text:latest", "embeddings"], ["bge-m3:latest", "embeddings"],
    ["starcoder2:3b", "other"], ["something-new:1b", "other"]])("%s -> %s", (name, group) => expect(groupOf(name)).toBe(group));
});

describe("catalogFamily", () => {
  it("picks the longest family and keeps vision/embedding variants out", () => {
    expect(catalogFamily("qwen3-coder-16k:latest", CATALOG.models)?.name).toBe("qwen3-coder");
    expect(catalogFamily("qwen3-coder:30b", CATALOG.models)?.name).toBe("qwen3-coder");
    expect(catalogFamily("qwen3:8b", CATALOG.models)?.name).toBe("qwen3");
    expect(catalogFamily("qwen3-vl:8b", CATALOG.models)).toBeNull();
    expect(catalogFamily("qwen3.5:9b", CATALOG.models)).toBeNull();
    expect(catalogFamily("deepseek-r1-8b-32k:latest", CATALOG.models)?.name).toBe("deepseek-r1");
  });
});

describe("buildItems / groupItems", () => {
  const items = buildItems(CATALOG, ov(["qwen3-coder-16k:latest", "qwen3.5:9b", "qwen3-vl:8b", "deepseek-r1:14b", "weird-model:1b"], ["qwen3-coder-16k:latest"]));
  it("attaches installed variants to their catalog entry and keeps the rest as installed-only items", () => {
    const coder = items.find((i) => i.key === "cat:qwen3-coder")!;
    expect(coder.installed.map((i) => i.name)).toEqual(["qwen3-coder-16k:latest"]);
    expect(coder.loaded).toBe(true);
    expect(items.filter((i) => i.kind === "installed").map((i) => i.label).sort()).toEqual(["qwen3-vl:8b", "qwen3.5:9b", "weird-model:1b"]);
    expect(items.find((i) => i.key === "cat:llama3.2")!.installed).toEqual([]);
  });
  it("groups by family in display order, best fit first, installed-only last", () => {
    const g = groupItems(items, "", false);
    expect(g.map((x) => x.id)).toEqual(["qwen", "deepseek", "llama", "gemma", "embeddings", "other"]);
    expect(g[0].items.map((i) => i.label)).toEqual(["Qwen3", "Qwen3 Coder", "Qwen2.5 Coder", "qwen3-vl:8b", "qwen3.5:9b"]);
    expect(g.find((x) => x.id === "other")!.items.map((i) => i.label)).toEqual(["weird-model:1b"]);
  });
  it("searches names, titles, descriptions and installed variants; multiple words must all match; empty families disappear", () => {
    expect(groupItems(items, "coder", false).flatMap((g) => g.items.map((i) => i.label))).toEqual(["Qwen3 Coder", "Qwen2.5 Coder"]);
    expect(groupItems(items, "16k", false).flatMap((g) => g.items.map((i) => i.label))).toEqual(["Qwen3 Coder"]);   // matches an installed variant name
    expect(groupItems(items, "deepseek description", false).map((g) => g.id)).toEqual(["deepseek"]);
    expect(groupItems(items, "zzz", false)).toEqual([]);
    expect(groupItems(items, "QWEN", false)[0].id).toBe("qwen");                                                      // case-insensitive
  });
  it("installed-only filter keeps only models with something installed", () => {
    expect(groupItems(items, "", true).flatMap((g) => g.items.map((i) => i.label)).sort()).toEqual(["DeepSeek-R1", "Qwen3 Coder", "qwen3-vl:8b", "qwen3.5:9b", "weird-model:1b"]);
  });
  it("works before the catalog or overview have loaded", () => {
    expect(buildItems(null, null)).toEqual([]);
    expect(groupItems(buildItems(CATALOG, null), "", false).flatMap((g) => g.items).every((i) => i.installed.length === 0)).toBe(true);
  });
});

const le = (name: string, over: Partial<LibraryEntry> = {}): LibraryEntry => ({
  name, description: `${name} description`, capabilities: [], sizes: [], pulls: "1K", pulls_n: 1000, tags: 1, updated: "1d",
  community: false, url: `https://ollama.com/library/${name}`, ...over,
});

describe("library models (everything on ollama.com that the guide does not describe)", () => {
  const LIB = [le("qwen3", { pulls_n: 9e6 }), le("ornith", { description: "A self-improving family of open-source models for agentic coding", capabilities: ["tools", "thinking"], sizes: ["9b", "35b"], pulls_n: 797_400 }),
    le("zzz-new", { pulls_n: 5 }), le("popular", { pulls_n: 5_000_000 })];

  it("adds library models the curated guide does not cover, and skips the ones it does", () => {
    const items = buildItems(CATALOG, ov([]), LIB);
    const lib = items.filter((i) => i.kind === "library").map((i) => i.label);
    expect(lib).toEqual(["ornith", "zzz-new", "popular"]);                  // qwen3 is in the guide
    expect(items.find((i) => i.key === "lib:ornith")?.group).toBe("library");
  });

  it("finds ornith by name, by description and by capability", () => {
    const items = buildItems(CATALOG, ov([]), LIB);
    for (const q of ["ornith", "agentic coding", "self-improving", "thinking"]) {
      expect(groupItems(items, q, false).flatMap((g) => g.items).some((i) => i.key === "lib:ornith"), q).toBe(true);
    }
    expect(groupItems(items, "nothing-like-this", false)).toEqual([]);
  });

  it("orders the library by popularity, and by relevance while searching", () => {
    const items = buildItems(CATALOG, ov([]), LIB);
    expect(groupItems(items, "", false).find((g) => g.id === "library")!.items.map((i) => i.label)).toEqual(["popular", "ornith", "zzz-new"]);
    const more = buildItems(CATALOG, ov([]), [le("ornith-1.5", { pulls_n: 900_000 }), ...LIB], [le("user/ornith-9b", { community: true, pulls_n: 12 })]);
    expect(groupItems(more, "ornith", false).find((g) => g.id === "library")!.items.map((i) => i.label)).toEqual(["ornith", "ornith-1.5", "user/ornith-9b"]);
  });

  it("attaches an installed model to its library family instead of listing it twice", () => {
    const items = buildItems(CATALOG, ov(["ornith:9b", "something-else:1b"], ["ornith:9b"]), LIB);
    const o = items.find((i) => i.key === "lib:ornith")!;
    expect(o.installed.map((m) => m.name)).toEqual(["ornith:9b"]);
    expect(o.loaded).toBe(true);
    expect(items.some((i) => i.kind === "installed" && i.label === "ornith:9b")).toBe(false);
    expect(items.some((i) => i.kind === "installed" && i.label === "something-else:1b")).toBe(true);
    expect(groupItems(items, "", true).flatMap((g) => g.items).map((i) => i.label).sort()).toEqual(["ornith", "something-else:1b"]);
  });

  it("merges search results, including community models, without duplicates", () => {
    const items = buildItems(CATALOG, ov([]), LIB, [le("ornith"), le("jetelain/Ornith-1.5-122b", { community: true }), le("qwen3")]);
    const labels = items.filter((i) => i.kind === "library").map((i) => i.label);
    expect(labels.filter((l) => l === "ornith")).toHaveLength(1);
    expect(labels).toContain("jetelain/Ornith-1.5-122b");
    expect(labels).not.toContain("qwen3");
  });

  it("attaches an installed community model to its entry", () => {
    const items = buildItems(CATALOG, ov(["maxwell1500/ornith-9b:q4_K_M"]), [], [le("maxwell1500/ornith-9b", { community: true })]);
    expect(items.find((i) => i.key === "lib:maxwell1500/ornith-9b")?.installed).toHaveLength(1);
    expect(items.some((i) => i.kind === "installed")).toBe(false);
  });
});

describe("parseSizeGb", () => {
  it.each([["5.6GB", 5.6], ["21GB", 21], ["512MB", 0.512], ["1.2TB", 1200], ["", 0], [null, 0], ["big", 0]])("%s -> %s", (s, gb) => expect(parseSizeGb(s as string | null)).toBeCloseTo(gb as number, 6));
});
