import type { ChangeEntry, Execution, ModelInfo, ModelMeta, OllamaStatus, Project, Session, TreeNode, Mode, Policy, Metrics } from "./types";

export const API = (import.meta.env.VITE_ZEUS_API as string | undefined) ?? "http://127.0.0.1:8765";
export const WS = API.replace(/^http/, "ws");

async function req<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(API + path, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep */ }
    throw new Error(msg);
  }
  return r.json();
}
const q = (o: Record<string, string | number | undefined>) =>
  "?" + Object.entries(o).filter(([, v]) => v !== undefined).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export interface Settings { home: string; ollama_url: string; default_model: string | null; default_num_ctx: number }
export interface PermInfo { mode: Mode; custom: Record<string, Policy>; policy: Record<string, Policy> }

export const api = {
  health: () => req<{ ok: boolean }>("/api/health"),
  settings: () => req<Settings>("/api/settings"),
  saveSettings: (s: Partial<Settings>) => req<Settings>("/api/settings", "PUT", s),
  ollama: () => req<OllamaStatus>("/api/ollama/status"),
  metrics: () => req<Metrics>("/api/metrics"),
  models: () => req<ModelInfo[]>("/api/models"),
  modelInfo: (name: string, num_ctx?: number) => req<ModelMeta>("/api/models/info" + q({ name, num_ctx })),
  projects: () => req<Project[]>("/api/projects"),
  addProject: (path: string) => req<Project>("/api/projects", "POST", { path }),
  createProject: (parent: string, name: string) => req<Project>("/api/projects/create", "POST", { parent, name }),
  removeProject: (id: number) => req<{ ok: boolean }>(`/api/projects/${id}`, "DELETE"),
  openProject: (id: number) => req<Project>(`/api/projects/${id}/open`, "POST"),
  pickFolder: () => req<{ path: string | null }>("/api/pick-folder", "POST"),
  tree: (id: number, rel = "") => req<TreeNode[]>(`/api/projects/${id}/tree` + q({ rel })),
  file: (id: number, path: string) => req<{ content: string }>(`/api/projects/${id}/file` + q({ path })),
  changes: (id: number) => req<ChangeEntry[]>(`/api/projects/${id}/changes`),
  changeDiff: (id: number, path: string) => req<{ before: string; after: string; status: string }>(`/api/projects/${id}/changes/diff` + q({ path })),
  getPerms: (id: number) => req<PermInfo>(`/api/projects/${id}/permissions`),
  setPerms: (id: number, mode: Mode, custom?: Record<string, Policy>) => req<PermInfo>(`/api/projects/${id}/permissions`, "PUT", { mode, custom }),
  sessions: (project_id?: number) => req<Session[]>("/api/sessions" + q({ project_id })),
  createSession: (b: { project_id: number; model?: string | null; num_ctx?: number | null }) => req<Session>("/api/sessions", "POST", b),
  patchSession: (id: number, b: Partial<Pick<Session, "title" | "model" | "num_ctx">>) => req<Session>(`/api/sessions/${id}`, "PATCH", b),
  deleteSession: (id: number) => req<{ ok: boolean }>(`/api/sessions/${id}`, "DELETE"),
  messages: (id: number) => req<Array<{ role: string; agent: string; content: string; tool_calls: Array<{ function: { name: string; arguments: Record<string, unknown> } }> | null; tool_name: string | null }>>(`/api/sessions/${id}/messages`),
  executions: (project_id: number) => req<Execution[]>("/api/executions" + q({ project_id })),
};
