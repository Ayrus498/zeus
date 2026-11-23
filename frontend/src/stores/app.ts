import { create } from "zustand";
import { api, type PermInfo, type Settings } from "../services/api";
import { Socket } from "../services/socket";
import type {
  ChangeEntry, Mode, ModelInfo, ModelMeta, OllamaStatus, PermissionRequest, Policy, Project, Session, TimelineItem, Usage, Overview, Toast, Attachment, MissionState, SettingsView,
} from "../services/types";

export type RightTab = "files" | "changes" | "terminal" | "usage";
export interface OpenFile { path: string; mode: "file" | "diff" }

interface State {
  backendUp: boolean; error: string | null;
  ollama: OllamaStatus | null; settings: Settings | null;
  models: ModelInfo[]; modelMeta: ModelMeta | null;
  projects: Project[]; project: Project | null; sessions: Session[]; session: Session | null; allSessions: Record<number, Session[]>;
  perms: PermInfo | null;
  timeline: TimelineItem[]; running: boolean; modelState: "idle" | "loading" | "ready"; activeModel: string | null;
  pending: PermissionRequest[]; usage: Usage | null;
  changes: ChangeEntry[]; treeVersion: number; openFile: OpenFile | null; rightTab: RightTab; settingsOpen: boolean; settingsView: SettingsView; modelsOpen: boolean;
  overview: Overview | null; toasts: Toast[]; attachment: Attachment | null;
  setAttachment(a: Attachment | null): void; sendMission(o: { note: string; rounds: number; verifier: "same" | "think" }): void;
  refreshOverview(): Promise<void>; pullModel(name: string): Promise<void>; cancelDownload(id: number): Promise<void>;
  dismissDownload(id: number): Promise<void>; unloadModel(name: string): Promise<void>; deleteModel(name: string): Promise<boolean>;
  toast(text: string, level?: Toast["level"]): void; dismissToast(id: number): void;
  init(): Promise<void>; refreshOllama(): Promise<void>; refreshModels(): Promise<void>;
  selectProject(id: number): Promise<void>; addProjectFromPicker(): Promise<void>; addProjectPath(p: string): Promise<void>;
  createProject(parent: string, name: string): Promise<void>; removeProject(id: number): Promise<void>;
  newSession(): Promise<void>; selectSession(id: number): Promise<void>; renameSession(id: number, t: string): Promise<void>; deleteSession(id: number): Promise<void>;
  setModel(name: string): Promise<void>; setCtx(n: number): Promise<void>;
  sendPrompt(t: string): void; stop(): void; respond(id: string, d: "once" | "session" | "reject"): void;
  setMode(m: Mode, custom?: Record<string, Policy>): Promise<void>;
  refreshChanges(): Promise<void>; bump(): void; setOpenFile(f: OpenFile | null): void; setRightTab(t: RightTab): void; setSettingsOpen(o: boolean): void; openSettings(v: SettingsView): void; setModelsOpen(o: boolean): void;
  saveSettings(s: Partial<Settings>): Promise<void>; notify(text: string, level?: "info" | "error"): void;
}

let sock: Socket | null = null;
let seq = 0;
let toastSeq = 0;
let started = false;

export function timelineFromMessages(msgs: Awaited<ReturnType<typeof api.messages>>): TimelineItem[] {
  const out: TimelineItem[] = [];
  const open: Array<Extract<TimelineItem, { kind: "tool" }>> = [];
  for (const m of msgs) {
    if (m.role === "user") {
      const mk = /^<!--zeus:mission id=(\d+) name=(.*?)-->\n/.exec(m.content);
      if (mk) {
        const doc = /```+markdown\n([\s\S]*?)\n```+\s*$/.exec(m.content)?.[1] ?? "";
        out.push({ kind: "mission", mid: Number(mk[1]), name: mk[2], title: mk[2], objective: "", criteria: [], status: "stopped", round: 0, maxRounds: 0, plan: {}, feedback: "", summary: "", doc });
      } else if (/^<!--zeus:feedback round=(\d+)-->/.test(m.content)) {
        const r = /^<!--zeus:feedback round=(\d+)-->/.exec(m.content)![1];
        out.push({ kind: "notice", level: "info", text: `Zeus-Agent review (round ${r}): not accepted yet. The unmet criteria were sent back to the agent.` });
      } else out.push({ kind: "user", text: m.content });
    }
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

  const patchMission = (mid: number, fn: (m: MissionState) => MissionState) =>
    set((st) => ({ timeline: st.timeline.map((it) => (it.kind === "mission" && it.mid === mid ? { ...fn(it), kind: "mission" as const } : it)) }));

  // Streamed tokens are buffered and applied in one update (see the "token" case).
  const tokenBuf = { agent: "coder", text: "" };
  let tokenTimer: ReturnType<typeof setTimeout> | null = null;
  function flushTokens() {
    if (tokenTimer) { clearTimeout(tokenTimer); tokenTimer = null; }
    if (!tokenBuf.text) return;
    const { agent, text } = tokenBuf; tokenBuf.text = "";
    set((s) => {
      const t = s.timeline.slice(); const last = t[t.length - 1];
      if (last?.kind === "assistant" && last.streaming) t[t.length - 1] = { ...last, text: last.text + text };
      else t.push({ kind: "assistant", agent, text, streaming: true });
      return { timeline: t };
    });
  }

  function handle(ev: Record<string, any>) { // eslint-disable-line @typescript-eslint/no-explicit-any
    if (ev.type !== "token") flushTokens();            // keep event order: pending text lands before whatever comes next
    switch (ev.type) {
      case "state": set({ running: ev.running, pending: ev.pending.map((p: PermissionRequest) => ({ ...p, session_id: ev.session_id })) }); break;
      case "run_start": set({ running: true }); break;
      case "mission_start":
        set((st) => ({ running: true, timeline: [...st.timeline, { kind: "mission", mid: ev.id, name: ev.name, title: ev.title, objective: ev.objective, criteria: ev.criteria,
          status: "running", round: 0, maxRounds: ev.max_rounds, plan: ev.plan ?? {}, feedback: "", summary: "", doc: st.attachment?.content ?? "" }], attachment: null })); break;
      case "mission_round": patchMission(ev.id, (m) => ({ ...m, round: ev.round, status: "running" })); break;
      case "mission_verifying": patchMission(ev.id, (m) => ({ ...m, status: "reviewing" })); break;
      case "mission_check": patchMission(ev.id, (m) => ({ ...m, criteria: ev.results, feedback: ev.feedback ?? "", status: "running" })); break;
      case "mission_end": patchMission(ev.id, (m) => ({ ...m, status: ev.status, summary: ev.summary })); break;
      case "model_selected":
        set((st) => ({ activeModel: ev.model, session: st.session ? { ...st.session, last_model: ev.model } : st.session,
          timeline: [...st.timeline, { kind: "route", model: ev.model, role: ev.role, reason: ev.reason, previous: ev.previous, tools: ev.tools }] })); break;
      case "model_loading": set({ modelState: "loading" }); break;
      case "model_ready": set({ modelState: "ready" }); break;
      case "token":
        // Tokens arrive far faster than the screen can usefully repaint; coalesce them so a big code block is
        // re-parsed ~12x/second instead of once per token.
        tokenBuf.agent = ev.agent; tokenBuf.text += ev.text;
        if (!tokenTimer) tokenTimer = setTimeout(flushTokens, 80);
        break;
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
      case "notice": get().notify(ev.text, ev.level === "error" ? "error" : "info"); break;
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
    sock?.close(); sock = null; tokenBuf.text = ""; if (tokenTimer) { clearTimeout(tokenTimer); tokenTimer = null; }
    set({ session: sess, timeline: [], pending: [], running: false, usage: null, modelState: "idle", activeModel: sess?.last_model ?? null });
    if (!sess) return;
    const msgs = await api.messages(sess.id);
    const tl = timelineFromMessages(msgs);
    set({ timeline: tl });
    if (tl.some((i) => i.kind === "mission")) {
      try {      // the Nth mission document in the conversation is the Nth mission row
        const rows = await api.missions(sess.id);
        let n = 0;
        set((st) => ({ timeline: st.timeline.map((it) => {
          if (it.kind !== "mission") return it;
          const r = rows[n++];
          return r ? { ...it, mid: r.id, title: r.title, objective: r.objective, criteria: r.criteria, status: r.status === "running" ? "stopped" : r.status, round: r.round, maxRounds: r.max_rounds,
            plan: r.plan, feedback: [...r.log].reverse().find((l) => l.feedback)?.feedback ?? "" } : it;
        }) }));
      } catch { /* mission details are optional on reload */ }
    }
    sock = new Socket(`/ws/sessions/${sess.id}`, handle);
    if (sess.model && sess.model !== "auto") void api.modelInfo(sess.model, sess.num_ctx ?? undefined).then((m) => set({ modelMeta: m })).catch(() => set({ modelMeta: null }));
  }

  return {
    backendUp: false, error: null, ollama: null, settings: null, models: [], modelMeta: null,
    projects: [], project: null, sessions: [], session: null, allSessions: {}, perms: null,
    timeline: [], running: false, modelState: "idle", activeModel: null, pending: [], usage: null,
    changes: [], treeVersion: 0, openFile: null, rightTab: "files", settingsOpen: false, settingsView: "endpoint", modelsOpen: false,

    overview: null, toasts: [], attachment: null,
    setAttachment: (attachment) => set({ attachment }),
    sendMission({ note, rounds, verifier }) {
      const a = get().attachment; const sess = get().session;
      if (!a || !sess) return;
      set({ running: true });
      sock?.send({ type: "mission", name: a.name, content: a.content, note, max_rounds: rounds, verifier });
    },
    toast(text, level = "info") {
      const id = ++toastSeq;
      set((s) => ({ toasts: [...s.toasts, { id, level, text }].slice(-4) }));
      if (level !== "error") setTimeout(() => get().dismissToast(id), 6000);   // errors stay until dismissed
    },
    dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
    async refreshOverview() {
      let o: Overview;
      try { o = await api.overview(); } catch { return; }
      const prev = new Map((get().overview?.downloads ?? []).map((d) => [d.id, d.status]));
      set({ overview: o });
      for (const d of o.downloads) {
        const was = prev.get(d.id);
        if (!was || was === d.status) continue;
        if (d.status === "completed") { get().toast(`${d.model} downloaded.`, "success"); void get().refreshModels(); }
        else if (d.status === "failed") get().toast(`Download of ${d.model} failed: ${d.error ?? "unknown error"}`, "error");
      }
    },
    async pullModel(name) {
      try { await api.pullModel(name); } catch (e) { get().toast(`Could not start download of ${name}: ${e instanceof Error ? e.message : e}`, "error"); }
      await get().refreshOverview();
    },
    async cancelDownload(id) {
      try { await api.cancelDownload(id); } catch (e) { get().toast(`Could not cancel: ${e instanceof Error ? e.message : e}`, "error"); }
      await get().refreshOverview();
    },
    async dismissDownload(id) { try { await api.dismissDownload(id); } catch { /* already gone */ } await get().refreshOverview(); },
    async unloadModel(name) {
      try { await api.unloadModel(name); get().toast(`${name} unloaded from memory.`, "success"); }
      catch (e) { get().toast(e instanceof Error ? e.message : String(e), "error"); }
      await get().refreshOverview();
    },
    async deleteModel(name) {
      try { await api.deleteModel(name); get().toast(`${name} uninstalled.`, "success"); }
      catch (e) { get().toast(e instanceof Error ? e.message : String(e), "error"); await get().refreshOverview(); return false; }
      await Promise.all([get().refreshOverview(), get().refreshModels()]);
      return true;
    },
    notify: (text, level = "info") => set((s) => ({ timeline: [...s.timeline, { kind: "notice", level, text }] })),
    bump: () => set((s) => ({ treeVersion: s.treeVersion + 1 })),
    setOpenFile: (openFile) => set({ openFile }),
    setRightTab: (rightTab) => set({ rightTab }),
    setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
    openSettings: (settingsView) => set({ settingsView, settingsOpen: true }),
    setModelsOpen: (modelsOpen) => set(modelsOpen ? { modelsOpen, settingsOpen: false } : { modelsOpen }),

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
      try { await api.removeProject(id); } catch (e) { get().notify(`Could not remove project: ${e instanceof Error ? e.message : e}`, "error"); return; }
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
      try { await api.deleteSession(id); } catch (e) { get().notify(`Could not delete session: ${e instanceof Error ? e.message : e}`, "error"); return; }
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
      if (name === "auto") { set({ modelMeta: null }); return; }        // Auto resolves a real model per prompt
      try { set({ modelMeta: await api.modelInfo(name, s.num_ctx ?? undefined) }); } catch (e) { set({ modelMeta: null }); get().notify(String(e instanceof Error ? e.message : e), "error"); }
    },
    async setCtx(n) {
      const sess = get().session; if (!sess) return;
      const s = await api.patchSession(sess.id, { num_ctx: n });
      await api.saveSettings({ default_num_ctx: n });
      set((st) => ({ session: s, sessions: st.sessions.map((x) => (x.id === s.id ? s : x)) }));
      if (s.model && s.model !== "auto") try { set({ modelMeta: await api.modelInfo(s.model, n) }); } catch { /* keep old */ }
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
