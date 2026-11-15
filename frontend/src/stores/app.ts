import { create } from "zustand";
import { api, type PermInfo, type Settings } from "../services/api";
import { Socket } from "../services/socket";
import type {
  ChangeEntry, Mode, ModelInfo, ModelMeta, OllamaStatus, PermissionRequest, Policy, Project, Session, TimelineItem, Usage,
} from "../services/types";

export type RightTab = "files" | "changes" | "terminal" | "usage";
export interface OpenFile { path: string; mode: "file" | "diff" }

interface State {
  backendUp: boolean; error: string | null;
  ollama: OllamaStatus | null; settings: Settings | null;
  models: ModelInfo[]; modelMeta: ModelMeta | null;
  projects: Project[]; project: Project | null; sessions: Session[]; session: Session | null; allSessions: Record<number, Session[]>;
  perms: PermInfo | null;
  timeline: TimelineItem[]; running: boolean; modelState: "idle" | "loading" | "ready";
  pending: PermissionRequest[]; usage: Usage | null;
  changes: ChangeEntry[]; treeVersion: number; openFile: OpenFile | null; rightTab: RightTab; settingsOpen: boolean;
  init(): Promise<void>; refreshOllama(): Promise<void>; refreshModels(): Promise<void>;
  selectProject(id: number): Promise<void>; addProjectFromPicker(): Promise<void>; addProjectPath(p: string): Promise<void>;
  createProject(parent: string, name: string): Promise<void>; removeProject(id: number): Promise<void>;
  newSession(): Promise<void>; selectSession(id: number): Promise<void>; renameSession(id: number, t: string): Promise<void>; deleteSession(id: number): Promise<void>;
  setModel(name: string): Promise<void>; setCtx(n: number): Promise<void>;
  sendPrompt(t: string): void; stop(): void; respond(id: string, d: "once" | "session" | "reject"): void;
  setMode(m: Mode, custom?: Record<string, Policy>): Promise<void>;
  refreshChanges(): Promise<void>; bump(): void; setOpenFile(f: OpenFile | null): void; setRightTab(t: RightTab): void; setSettingsOpen(o: boolean): void;
  saveSettings(s: Partial<Settings>): Promise<void>; notify(text: string, level?: "info" | "error"): void;
}

let sock: Socket | null = null;
let seq = 0;
let started = false;

export function timelineFromMessages(msgs: Awaited<ReturnType<typeof api.messages>>): TimelineItem[] {
  const out: TimelineItem[] = [];
  const open: Array<Extract<TimelineItem, { kind: "tool" }>> = [];
  for (const m of msgs) {
    if (m.role === "user") out.push({ kind: "user", text: m.content });
    else if (m.role === "assistant") {
      if (m.content.trim()) out.push({ kind: "assistant", agent: m.agent, text: m.content });
      for (const c of m.tool_calls ?? []) {
        const t: Extract<TimelineItem, { kind: "tool" }> = { kind: "tool", id: `h${seq++}`, name: c.function.name, args: c.function.arguments ?? {} };
        out.push(t); open.push(t);
      }
    } else if (m.role === "tool") {
      const t = open.shift();
      if (t) { t.result = m.content; t.ok = !/^(Permission denied|Blocked|Invalid tool call)/.test(m.content) && !/^exit code: [1-9-]/.test(m.content); }
    }
  }
  return out;
}

export const useApp = create<State>((set, get) => {
  const patchLast = (fn: (it: TimelineItem) => TimelineItem | null, pred: (it: TimelineItem) => boolean) =>
    set((s) => {
      for (let i = s.timeline.length - 1; i >= 0; i--) if (pred(s.timeline[i])) {
        const nx = fn(s.timeline[i]); const t = s.timeline.slice(); if (nx) t[i] = nx; return { timeline: t };
      }
      return {};
    });

  function handle(ev: Record<string, any>) { // eslint-disable-line @typescript-eslint/no-explicit-any
    switch (ev.type) {
      case "state": set({ running: ev.running, pending: ev.pending.map((p: PermissionRequest) => ({ ...p, session_id: ev.session_id })) }); break;
      case "run_start": set({ running: true }); break;
      case "model_loading": set({ modelState: "loading" }); break;
      case "model_ready": set({ modelState: "ready" }); break;
      case "token":
        set((s) => {
          const t = s.timeline.slice(); const last = t[t.length - 1];
          if (last?.kind === "assistant" && last.streaming) t[t.length - 1] = { ...last, text: last.text + ev.text };
          else t.push({ kind: "assistant", agent: ev.agent, text: ev.text, streaming: true });
          return { timeline: t };
        }); break;
      case "assistant_end":
        // `text` is the final content with any text-form tool-call JSON removed; empty => drop the bubble
        set((s) => ({ timeline: s.timeline.flatMap<TimelineItem>((i) => (i.kind === "assistant" && i.streaming ? (ev.text?.trim() ? [{ ...i, text: ev.text, streaming: false }] : []) : [i])) })); break;
      case "tool_call":
        set((s) => ({ timeline: [...s.timeline, { kind: "tool", id: `t${seq++}`, name: ev.name, args: ev.args }] })); break;
      case "tool_result":
        patchLast((i) => (i.kind === "tool" ? { ...i, result: ev.output, ok: ev.ok } : i), (i) => i.kind === "tool" && i.result === undefined && i.name === ev.name); break;
      case "permission_request": set((s) => ({ pending: [...s.pending, ev as PermissionRequest] })); break;
      case "permission_decision": break;
      case "command_start":
        set((s) => ({ timeline: [...s.timeline, { kind: "command", runId: ev.run_id, command: ev.command, output: "", status: "running" }], rightTab: s.rightTab })); break;
      case "command_output":
        set((s) => ({ timeline: s.timeline.map((i) => (i.kind === "command" && i.runId === ev.run_id ? { ...i, output: (i.output + ev.text).slice(-20000) } : i)) })); break;
      case "command_end":
        set((s) => ({ timeline: s.timeline.map((i) => (i.kind === "command" && i.runId === ev.run_id ? { ...i, status: ev.status, exitCode: ev.exit_code } : i)) }));
        get().bump(); break;
      case "file_change": get().bump(); void get().refreshChanges(); break;
      case "usage":
        set((s) => ({ session: s.session ? { ...s.session, input_tokens: ev.session_input, output_tokens: ev.session_output } : s.session, usage: { prompt_tokens: ev.prompt_tokens, output_tokens: ev.output_tokens, tokens_per_sec: ev.tokens_per_sec,
          session_input: ev.session_input, session_output: ev.session_output, context_used: ev.context_used } })); break;
      case "error": get().notify(ev.message, "error"); break;
      case "run_end":
        set((s) => ({ running: false, modelState: "idle", pending: [], timeline: s.timeline.map((i) => (i.kind === "assistant" && i.streaming ? { ...i, streaming: false } : i)) }));
        if (ev.status === "stopped") get().notify("Generation stopped.");
        if (ev.status === "step_limit") get().notify("Stopped after reaching the step limit.", "error");
        void get().refreshChanges(); get().bump();
        void api.sessions(get().project?.id).then((sessions) => set({ sessions, session: sessions.find((x) => x.id === get().session?.id) ?? get().session }));
        break;
    }
  }

  async function loadSession(sess: Session | null) {
    sock?.close(); sock = null;
    set({ session: sess, timeline: [], pending: [], running: false, usage: null, modelState: "idle" });
    if (!sess) return;
    const msgs = await api.messages(sess.id);
    set({ timeline: timelineFromMessages(msgs) });
    sock = new Socket(`/ws/sessions/${sess.id}`, handle);
    if (sess.model) void api.modelInfo(sess.model, sess.num_ctx ?? undefined).then((m) => set({ modelMeta: m })).catch(() => set({ modelMeta: null }));
  }

  return {
    backendUp: false, error: null, ollama: null, settings: null, models: [], modelMeta: null,
    projects: [], project: null, sessions: [], session: null, allSessions: {}, perms: null,
    timeline: [], running: false, modelState: "idle", pending: [], usage: null,
    changes: [], treeVersion: 0, openFile: null, rightTab: "files", settingsOpen: false,

    notify: (text, level = "info") => set((s) => ({ timeline: [...s.timeline, { kind: "notice", level, text }] })),
    bump: () => set((s) => ({ treeVersion: s.treeVersion + 1 })),
    setOpenFile: (openFile) => set({ openFile }),
    setRightTab: (rightTab) => set({ rightTab }),
    setSettingsOpen: (settingsOpen) => set({ settingsOpen }),

    async init() {
      if (started) return;
      started = true;
      try {
        await api.health(); set({ backendUp: true });
      } catch { started = false; set({ backendUp: false }); setTimeout(() => void get().init(), 1500); return; }
      const settings = await api.settings(); set({ settings });
      await Promise.all([get().refreshOllama(), get().refreshModels()]);
      const projects = await api.projects(); set({ projects });
      const first = projects.find((p) => p.exists !== false);
      if (first) await get().selectProject(first.id);
      setInterval(() => void get().refreshOllama(), 10000);
    },
    async refreshOllama() { try { set({ ollama: await api.ollama() }); } catch { set({ ollama: { connected: false, endpoint: get().settings?.ollama_url ?? "", error: "backend unreachable" } }); } },
    async refreshModels() { try { set({ models: await api.models() }); } catch { set({ models: [] }); } },

    async selectProject(id) {
      const project = await api.openProject(id);
      const [perms, sessions] = await Promise.all([api.getPerms(id), api.sessions(id)]);
      set({ project, perms, sessions, projects: await api.projects(), openFile: null, changes: [] });
      await loadSession(sessions[0] ?? null);   // resume the most recent conversation
      void get().refreshChanges(); get().bump();
    },
    async addProjectPath(path) { try { const p = await api.addProject(path); await get().selectProject(p.id); } catch (e) { get().notify(String(e instanceof Error ? e.message : e), "error"); } },
    async addProjectFromPicker() {
      try { const { path } = await api.pickFolder(); if (path) await get().addProjectPath(path); }
      catch (e) { get().notify(String(e instanceof Error ? e.message : e), "error"); }
    },
    async createProject(parent, name) { const p = await api.createProject(parent, name); await get().selectProject(p.id); },
    async removeProject(id) {
      await api.removeProject(id);
      const projects = await api.projects(); set({ projects });
      if (get().project?.id === id) {
        set({ project: null, sessions: [], changes: [] }); await loadSession(null);
        if (projects[0]) await get().selectProject(projects[0].id);
      }
    },

    async newSession() {
      const p = get().project; if (!p) return;
      const s = get().settings;
      const prev = get().session;
      const sess = await api.createSession({ project_id: p.id, model: prev?.model ?? s?.default_model, num_ctx: prev?.num_ctx ?? s?.default_num_ctx });
      set({ sessions: [sess, ...get().sessions] }); await loadSession(sess);
    },
    async selectSession(id) { const s = get().sessions.find((x) => x.id === id); if (s) await loadSession(s); },
    async renameSession(id, title) { const s = await api.patchSession(id, { title }); set((st) => ({ sessions: st.sessions.map((x) => (x.id === id ? s : x)), session: st.session?.id === id ? s : st.session })); },
    async deleteSession(id) {
      await api.deleteSession(id);
      const sessions = get().sessions.filter((x) => x.id !== id); set({ sessions });
      if (get().session?.id === id) await loadSession(sessions[0] ?? null);
    },

    async setModel(name) {
      let sess = get().session;
      if (!sess) { await get().newSession(); sess = get().session; }
      if (!sess) return;
      const s = await api.patchSession(sess.id, { model: name });
      await api.saveSettings({ default_model: name });
      set((st) => ({ session: s, sessions: st.sessions.map((x) => (x.id === s.id ? s : x)) }));
      try { set({ modelMeta: await api.modelInfo(name, s.num_ctx ?? undefined) }); } catch (e) { set({ modelMeta: null }); get().notify(String(e instanceof Error ? e.message : e), "error"); }
    },
    async setCtx(n) {
      const sess = get().session; if (!sess) return;
      const s = await api.patchSession(sess.id, { num_ctx: n });
      await api.saveSettings({ default_num_ctx: n });
      set((st) => ({ session: s, sessions: st.sessions.map((x) => (x.id === s.id ? s : x)) }));
      if (s.model) try { set({ modelMeta: await api.modelInfo(s.model, n) }); } catch { /* keep old */ }
    },

    sendPrompt(text) {
      if (!get().session) return;
      set((s) => ({ timeline: [...s.timeline, { kind: "user", text }], running: true }));
      sock?.send({ type: "prompt", content: text });
    },
    stop() { sock?.send({ type: "stop" }); },
    respond(id, decision) {
      sock?.send({ type: "permission_response", id, decision });
      set((s) => ({ pending: s.pending.filter((p) => p.id !== id) }));
    },

    async setMode(mode, custom) { const p = get().project; if (!p) return; set({ perms: await api.setPerms(p.id, mode, custom) }); },
    async refreshChanges() { const p = get().project; if (p) try { set({ changes: await api.changes(p.id) }); } catch { /* ignore */ } },
    async saveSettings(s) { set({ settings: await api.saveSettings(s) }); await Promise.all([get().refreshOllama(), get().refreshModels()]); },
  };
});
