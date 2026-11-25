import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { fmtCtx } from "../../lib/utils";
import type { ContextInfo, EngineId, Mode } from "../../services/types";
import type { RightTab } from "../../stores/app";

/** Slash commands of the prompt box. Each one either opens the matching part of the interface or shows its result in the conversation. */
export interface Cmd {
  name: string; aliases?: string[]; group: "Memory" | "Conversation" | "Models" | "Panels"; desc: string;
  args?: string;                      // shown as a hint
  required?: boolean;                 // the argument is mandatory: choosing the command from the menu waits for it instead of running
  run?: (arg: string) => void | Promise<void>;
  insert?: string;                    // text that goes to the model as typed (Auto mode's /think and /code)
}

const app = () => useApp.getState();
const show = (title: string, body?: string, extra: Partial<Extract<import("../../services/types").TimelineItem, { kind: "local" }>> = {}) => app().addLocal({ kind: "local", title, body, ...extra });
const fail = (text: string) => show(text, undefined, { tone: "error" });
const err = (e: unknown) => (e instanceof Error ? e.message : String(e));
const MODES: Mode[] = ["read_only", "approval", "trusted"];
const MODE_LABEL: Record<string, string> = { read_only: "Read only", approval: "Approval", trusted: "Trusted" };

export function contextCard(c: ContextInfo) {
  const warn = c.percent >= c.trigger_percent;
  const lines = [
    `**${c.messages_in_context}** of ${c.messages_total} messages are sent to the model${c.summary ? `; the first ${c.summary.covers} are replaced by a summary` : ""}.`,
    c.summary ? "A summary of the older messages is in use (see `/summary`)." : `Older messages are summarised automatically at ${c.trigger_percent}% of the window; \`/compact\` does it now.`,
    c.memory.exists ? `Project memory: ${c.memory.bytes.toLocaleString()} bytes are added to every request (\`/memory\` to edit).` : "No project memory yet (`/memory` or `/remember <note>` creates it).",
  ];
  show("Context", lines.join("\n\n"), { tone: warn ? "warn" : "ok", meter: { used: c.tokens, window: c.window, trigger: c.trigger_percent },
    actions: [{ label: "Compact now", cmd: "/compact" }, { label: "Project memory", cmd: "/memory" }, ...(c.summary ? [{ label: "Show summary", cmd: "/summary" }] : [])] });
}

export const COMMANDS: Cmd[] = [
  { name: "context", aliases: ["ctx"], group: "Memory", desc: "How full the context window is, what is summarised, what memory is in use", async run() {
    if (!app().session) return fail("Open a conversation first.");
    const c = await app().refreshContext(); if (c) contextCard(c); else fail("Could not read the context.");
  } },
  { name: "compact", group: "Memory", desc: "Summarise the older messages now to free context window", async run() {
    const s = app().session; if (!s) return fail("Open a conversation first.");
    app().toast("Summarising the older messages…");
    try {
      const r = await api.compact(s.id);
      useApp.setState({ ctxInfo: r.context });
      if (r.ok) show("Conversation summarised", `The first **${r.covers}** messages were folded into a summary (about ${r.tokens_before?.toLocaleString()} → ${r.tokens_after?.toLocaleString()} tokens). The full transcript is kept.\n\n${r.summary}`,
        { tone: "ok", meter: { used: r.context.tokens, window: r.context.window, trigger: r.context.trigger_percent }, actions: [{ label: "Undo (forget the summary)", cmd: "/summary reset" }] });
      else show("Nothing to compact", r.reason, { tone: "warn" });
    } catch (e) { fail(`Could not compact: ${err(e)}`); }
  } },
  { name: "summary", group: "Memory", args: "[reset]", desc: "Show the rolling summary of this conversation, or `reset` to send the whole conversation again", async run(arg) {
    const s = app().session; if (!s) return fail("Open a conversation first.");
    try {
      if (arg.trim() === "reset") { const c = await api.resetSummary(s.id); useApp.setState({ ctxInfo: c }); return show("Summary forgotten", "The whole conversation is sent to the model again, until it fills the window and is summarised again.", { tone: "ok" }); }
      const c = await app().refreshContext();
      if (!c?.summary) return show("No summary yet", `The conversation is summarised automatically at ${c?.trigger_percent ?? 75}% of the context window, or on request with /compact.`);
      show(`Summary of the first ${c.summary.covers} messages`, c.summary.text, { actions: [{ label: "Forget the summary", cmd: "/summary reset" }] });
    } catch (e) { fail(err(e)); }
  } },
  { name: "memory", group: "Memory", args: "[add <note> | show]", desc: "Project memory: notes every new conversation starts with. Opens the editor", async run(arg) {
    const p = app().project; if (!p) return fail("Open a project first.");
    const [sub, ...rest] = arg.trim().split(/\s+/);
    if (!sub) return app().setMemoryOpen(true);
    if (sub === "add") return remember(rest.join(" "));
    if (sub === "show") { const m = await api.memory(p.id); return show("Project memory", m.text.trim() ? m.text : "Nothing saved yet.", { actions: [{ label: "Edit", cmd: "/memory" }] }); }
    fail("Use /memory, /memory show or /memory add <note>.");
  } },
  { name: "remember", group: "Memory", args: "<note>", required: true, desc: "Save one note to project memory (every new conversation starts with it)", run: (arg) => remember(arg) },
  { name: "new", group: "Conversation", desc: "Start a new conversation in this project", async run() { await app().newSession(); } },
  { name: "clear", group: "Conversation", desc: "Clear the screen (the conversation is kept)", run() { app().clearTimeline(); } },
  { name: "stop", group: "Conversation", desc: "Stop what Zeus-Agent is doing", run() { app().stop(); } },
  { name: "help", aliases: ["?"], group: "Conversation", desc: "List the commands", run() {
    show("Commands", "Type **/** in the prompt box to see these. Click a command to run it.", { table: COMMANDS.filter((c) => !c.insert).map((c) => ({ name: `/${c.name}${c.args ? " " + c.args : ""}`, desc: c.desc, cmd: c.required ? "" : `/${c.name}` })) });
  } },
  { name: "think", group: "Conversation", desc: "Auto mode: use the reasoning model for this prompt", insert: "/think " },
  { name: "code", group: "Conversation", desc: "Auto mode: use the coding model for this prompt", insert: "/code " },
  { name: "engine", group: "Models", args: "[ollama|llamacpp|help]", desc: "Choose what runs the models: Ollama or llama.cpp", async run(arg) {
    const a = arg.trim().toLowerCase().replace(/[.\s-]/g, "");
    if (a === "help") return app().setEngineHelpOpen(true);
    if (a === "ollama" || a === "llamacpp" || a === "llama") { await app().setEngine((a === "llama" ? "llamacpp" : a) as EngineId); return show(`Engine: ${app().engine() === "llamacpp" ? "llama.cpp" : "Ollama"}`, undefined, { tone: "ok" }); }
    const cur = app().engine();
    show("Engine", `This conversation runs on **${cur === "llamacpp" ? "llama.cpp" : "Ollama"}**.`, { actions: [{ label: "Use Ollama", cmd: "/engine ollama" }, { label: "Use llama.cpp", cmd: "/engine llamacpp" }, { label: "Help me choose", cmd: "/engine help" }] });
  } },
  { name: "llamacpp", aliases: ["llama"], group: "Models", desc: "llama.cpp window: models, server path, downloads", run() { app().setLlamaOpen(true); } },
  { name: "models", group: "Models", desc: "Open the Models window", run() { app().openModels(); } },
  { name: "best", aliases: ["benchmarks"], group: "Models", desc: "Which model is best for each job, with benchmarks and fit for your hardware", run() { app().openModels("arena"); } },
  { name: "permissions", aliases: ["perm"], group: "Panels", args: "[read_only|approval|trusted]", desc: "What Zeus-Agent may do in this project", async run(arg) {
    const m = arg.trim().toLowerCase().replace("-", "_") as Mode;
    if (arg.trim() && !MODES.includes(m)) return fail("Use /permissions read_only, approval or trusted.");
    if (arg.trim()) { await app().setMode(m); return show(`Permissions: ${MODE_LABEL[m]}`, undefined, { tone: "ok" }); }
    show("Permissions", `This project is in **${MODE_LABEL[app().perms?.mode ?? "approval"]}** mode.`, { actions: MODES.map((x) => ({ label: MODE_LABEL[x], cmd: `/permissions ${x}` })) });
  } },
  ...(["files", "changes", "terminal", "usage"] as RightTab[]).map<Cmd>((t) => ({ name: t, group: "Panels", desc: `Show the ${t} panel`, run() { app().setRightTab(t); } })),
  { name: "reset", group: "Panels", desc: "Return Zeus-Agent to a fresh installation (asks what to remove and for confirmation)", run() { app().setResetOpen(true); } },
  { name: "settings", aliases: ["about"], group: "Panels", desc: "About Zeus-Agent: version, device and memory", run() { app().openSettings("about"); } },
];

async function remember(note: string) {
  const p = app().project; if (!p) return fail("Open a project first.");
  if (!note.trim()) return fail("Write the note after the command, for example /remember Tests run with pytest -q");
  try {
    const r = await api.addMemoryNote(p.id, note);
    void app().refreshContext();
    show(r.added ? "Saved to project memory" : "Already in project memory", r.line, { tone: r.added ? "ok" : undefined, actions: [{ label: "Edit memory", cmd: "/memory" }] });
  } catch (e) { fail(err(e)); }
}

/** Commands that match what is being typed (the text after the slash), best matches first. */
export function matchCommands(text: string): Cmd[] {
  const q = text.slice(1).toLowerCase();
  const names = (c: Cmd) => [c.name, ...(c.aliases ?? [])];
  const starts = COMMANDS.filter((c) => names(c).some((n) => n.startsWith(q)));
  const has = COMMANDS.filter((c) => !starts.includes(c) && (names(c).some((n) => n.includes(q)) || c.desc.toLowerCase().includes(q)));
  return q ? [...starts, ...has] : COMMANDS;
}

export function findCommand(name: string): Cmd | undefined {
  const n = name.toLowerCase();
  return COMMANDS.find((c) => c.name === n || c.aliases?.includes(n));
}

/** Run a typed slash command. Returns false when the text should go to the model instead (not a command, or /think, /code). */
export async function runSlash(text: string): Promise<boolean> {
  const m = /^\/(\S*)\s*([\s\S]*)$/.exec(text.trim());
  if (!m) return false;
  const cmd = findCommand(m[1]);
  if (!cmd) { if (!m[1]) return false; show(`Unknown command /${m[1]}`, "Type **/help** to see the commands.", { tone: "error" }); return true; }
  if (cmd.insert) return false;
  try { await cmd.run?.(m[2].trim()); } catch (e) { fail(`/${cmd.name} failed: ${err(e)}`); }
  return true;
}

export { fmtCtx };
