import { beforeEach, describe, expect, it } from "vitest";
import { COMMANDS, findCommand, matchCommands, runSlash } from "../features/commands/commands";
import { useApp } from "../stores/app";

beforeEach(() => useApp.setState({ timeline: [], session: null, project: null }));
const last = () => useApp.getState().timeline.at(-1) as Extract<ReturnType<typeof useApp.getState>["timeline"][number], { kind: "local" }>;

describe("slash commands", () => {
  it("lists every command for a bare slash and filters by prefix, then by description", () => {
    expect(matchCommands("/")).toHaveLength(COMMANDS.length);
    expect(matchCommands("/com")[0].name).toBe("compact");
    expect(matchCommands("/mem").map((c) => c.name)).toContain("memory");
    expect(matchCommands("/summar").some((c) => c.name === "summary")).toBe(true);
    expect(matchCommands("/zzzzqq")).toHaveLength(0);
    expect(matchCommands("/benchmark")[0].name).toBe("best");                    // an alias matches too
  });
  it("finds commands by name or alias, case-insensitively", () => {
    expect(findCommand("CTX")?.name).toBe("context");
    expect(findCommand("perm")?.name).toBe("permissions");
    expect(findCommand("nope")).toBeUndefined();
  });
  it("memory commands are in the menu with their arguments", () => {
    const m = Object.fromEntries(COMMANDS.map((c) => [c.name, c]));
    expect(m.remember.args).toBe("<note>");
    expect(m.memory.group).toBe("Memory");
    expect(m.compact.group).toBe("Memory");
  });
  it("shows the commands as a card with runnable rows", async () => {
    expect(await runSlash("/help")).toBe(true);
    const c = last();
    expect(c.kind).toBe("local");
    expect(c.table!.find((r) => r.name === "/context")?.cmd).toBe("/context");
    expect(c.table!.find((r) => r.name.startsWith("/remember"))?.cmd).toBe("");     // needs an argument: not runnable from the list
    expect(c.table!.some((r) => r.name === "/think")).toBe(false);                   // model passthrough commands are not listed as local
  });
  it("reports an unknown command instead of sending it to the model", async () => {
    expect(await runSlash("/frobnicate")).toBe(true);
    expect(last().title).toContain("Unknown command /frobnicate");
    expect(last().tone).toBe("error");
  });
  it("lets /think and /code through to the model, and ignores plain text", async () => {
    expect(await runSlash("/think design this")).toBe(false);
    expect(await runSlash("/code fix")).toBe(false);
    expect(await runSlash("hello")).toBe(false);
    expect(useApp.getState().timeline).toHaveLength(0);
  });
  it("memory and context commands explain what is missing instead of failing", async () => {
    await runSlash("/context");
    expect(last().title).toMatch(/Open a conversation/);
    await runSlash("/remember something");
    expect(last().title).toMatch(/Open a project/);
    await runSlash("/clear");
    expect(useApp.getState().timeline).toHaveLength(0);
  });
  it("/reset opens the reset dialog and removes nothing by itself", async () => {
    expect(findCommand("reset")?.group).toBe("Panels");
    expect(useApp.getState().resetOpen).toBe(false);
    expect(await runSlash("/reset")).toBe(true);
    expect(useApp.getState().resetOpen).toBe(true);
    expect(useApp.getState().timeline).toHaveLength(0);                           // no result card: the dialog does the asking
    useApp.getState().setResetOpen(false);
  });
  it("/permissions rejects an unknown mode", async () => {
    await runSlash("/permissions godmode");
    expect(last().tone).toBe("error");
  });
});
