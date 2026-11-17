import { describe, expect, it } from "vitest";
import { buildItems, catalogFamily, groupItems, groupOf } from "../lib/modelGroups";
import type { Catalog, CatalogModel, ModelInfo, Overview } from "../services/types";

const cm = (name: string, title: string, fit: CatalogModel["fit"] = "good", purposes: string[] = ["chat"]): CatalogModel => ({
  name, title, maker: "M", description: `${title} description`, best_for: "x", purposes, capabilities: [], fit, context: null, note: null,
  url: `https://ollama.com/library/${name}`, tags: [{ tag: `${name}:7b`, size_gb: 4, latest: true }],
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
