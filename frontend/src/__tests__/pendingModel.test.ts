import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../services/api";
import { useApp } from "../stores/app";
import type { Project } from "../services/types";

const project = { id: 1, name: "p", path: "/p", permission_mode: "approval" } as unknown as Project;
const texts = () => useApp.getState().timeline.map((i) => (i.kind === "notice" ? `${i.level}:${i.text}` : ""));

beforeEach(() => useApp.setState({ timeline: [], session: null, project: null, sessions: [], pendingModel: null, settings: null, engines: null }));
afterEach(() => vi.restoreAllMocks());

describe("choosing a model before any project is open (a fresh install, or right after a reset)", () => {
  it("remembers the choice and says so instead of doing nothing", async () => {
    const patch = vi.spyOn(api, "patchSession");
    const create = vi.spyOn(api, "createSession");
    await useApp.getState().setModel("gemma-3-1b-it-Q4_K_M");
    expect(useApp.getState().pendingModel).toEqual({ engine: "ollama", name: "gemma-3-1b-it-Q4_K_M" });
    expect(texts().join("\n")).toMatch(/gemma-3-1b-it-Q4_K_M will be used for your first conversation\. Open or create a project/);
    expect(patch).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
  });

  it("remembers it for the engine that is selected", async () => {
    useApp.setState({ settings: { home: "", ollama_url: "", default_model: null, default_num_ctx: 0, engine: "llamacpp" } });
    await useApp.getState().setModel("gemma");
    expect(useApp.getState().pendingModel).toEqual({ engine: "llamacpp", name: "gemma" });
  });

  it("words Auto mode sensibly", async () => {
    await useApp.getState().setModel("auto");
    expect(texts().join("\n")).toContain("Auto mode will be used for your first conversation");
  });

  it("a new conversation without a project explains why it cannot start", async () => {
    const create = vi.spyOn(api, "createSession");
    await useApp.getState().newSession();
    expect(texts().join("\n")).toMatch(/^error:Open or create a project first/m);
    expect(create).not.toHaveBeenCalled();
  });

  it("the first conversation then starts with the remembered model", async () => {
    useApp.setState({ project, pendingModel: { engine: "ollama", name: "qwen3:8b" } });
    const create = vi.spyOn(api, "createSession").mockRejectedValue(new Error("stop here"));       // enough to see what it was asked to create
    await expect(useApp.getState().newSession()).rejects.toThrow("stop here");
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ project_id: 1, engine: "ollama", model: "qwen3:8b" }));
  });

  it("a choice made for another engine is not applied to this one", async () => {
    useApp.setState({ project, pendingModel: { engine: "llamacpp", name: "gemma" } });
    const create = vi.spyOn(api, "createSession").mockRejectedValue(new Error("stop here"));
    await expect(useApp.getState().newSession()).rejects.toThrow("stop here");
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ engine: "ollama", model: undefined }));
  });

  it("with a project open, choosing a model still creates the conversation (unchanged)", async () => {
    useApp.setState({ project });
    const create = vi.spyOn(api, "createSession").mockRejectedValue(new Error("stop here"));
    await expect(useApp.getState().setModel("qwen3:8b")).rejects.toThrow("stop here");
    expect(create).toHaveBeenCalled();
    expect(useApp.getState().pendingModel).toBeNull();
  });
});
