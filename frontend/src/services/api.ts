import type { ChangeEntry, Execution, ModelInfo, ModelMeta, OllamaStatus, Project, Session, TreeNode, Mode, Policy, Metrics, Catalog, DownloadRow, Overview, AutoPlan, AutoPreview, MissionRow, ConvertFormats, ConvertResult, LibraryInfo, LibraryList, LibrarySearch, AboutInfo } from "./types";

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

/** Upload a document (raw body) and get Markdown back; conversion happens in the backend with MarkItDown. */
async function convertFile(file: File): Promise<ConvertResult> {
  const r = await fetch(`${API}/api/convert?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep */ }
    throw new Error(msg);
  }
  return r.json();
}

export interface Settings { home: string; ollama_url: string; default_model: string | null; default_num_ctx: number }
export interface PermInfo { mode: Mode; custom: Record<string, Policy>; policy: Record<string, Policy> }

export const api = {
  health: () => req<{ ok: boolean }>("/api/health"),
  settings: () => req<Settings>("/api/settings"),
  saveSettings: (s: Partial<Settings>) => req<Settings>("/api/settings", "PUT", s),
  ollama: () => req<OllamaStatus>("/api/ollama/status"),
  autoPlan: () => req<AutoPlan>("/api/auto/plan"),
  autoPreview: (prompt: string, last_model?: string | null) => req<AutoPreview>("/api/auto/preview", "POST", { prompt, last_model }),
  autoPins: (b: { code?: string; think?: string; clear?: string[] }) => req<{ code: string | null; think: string | null }>("/api/auto/pins", "PUT", b),
  convertFormats: () => req<ConvertFormats>("/api/convert/formats"),
  convertFile,
  catalog: () => req<Catalog>("/api/ollama/catalog"),
  overview: () => req<Overview>("/api/ollama/overview"),
  pullModel: (model: string) => req<DownloadRow>("/api/ollama/pull", "POST", { model }),
  cancelDownload: (id: number) => req<{ ok: boolean }>(`/api/ollama/downloads/${id}/cancel`, "POST"),
  dismissDownload: (id: number) => req<{ ok: boolean }>(`/api/ollama/downloads/${id}`, "DELETE"),
  about: () => req<AboutInfo>("/api/about"),
  library: (family: string, refresh = false) => req<LibraryInfo>(`/api/ollama/library/${family.split("/").map(encodeURIComponent).join("/")}` + q({ refresh: refresh ? "true" : undefined })),
  libraryList: (refresh = false) => req<LibraryList>("/api/ollama/library" + q({ refresh: refresh ? "true" : undefined })),
  searchLibrary: (query: string) => req<LibrarySearch>("/api/ollama/search" + q({ q: query })),
  deleteModel: (model: string) => req<{ ok: boolean }>("/api/ollama/delete", "POST", { model }),
  unloadModel: (model: string) => req<{ ok: boolean }>("/api/ollama/unload", "POST", { model }),
  openUrl: (url: string) => req<{ ok: boolean }>("/api/open-url", "POST", { url }),
  metrics: () => req<Metrics>("/api/metrics"),
  models: () => req<ModelInfo[]>("/api/models"),
  modelInfo: (name: string, num_ctx?: number) => req<ModelMeta>("/api/models/info" + q({ name, num_ctx })),
  projects: () => req<Project[]>("/api/projects"),
  addProject: (path: string) => req<Project>("/api/projects", "POST", { path }),
  createProject: (parent: string, name: string) => req<Project>("/api/projects/create", "POST", { parent, name }),
  removeProject: (id: number) => req<{ ok: boolean }>(`/api/projects/${id}`, "DELETE"),
  openProject: (id: number) => req<Project>(`/api/projects/${id}/open`, "POST"),
  pickFolder: () => req<{ path: string | null }>("/api/pick-folder", "POST"),
  saveFile: (pid: number, b: { path: string; content: string; session_id?: number | null; overwrite?: boolean }) => req<{ ok: boolean; path: string; bytes: number; overwritten: boolean }>(`/api/projects/${pid}/files`, "POST", b),
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
  missions: (id: number) => req<MissionRow[]>(`/api/sessions/${id}/missions`),
  messages: (id: number) => req<Array<{ role: string; agent: string; content: string; tool_calls: Array<{ function: { name: string; arguments: Record<string, unknown> } }> | null; tool_name: string | null }>>(`/api/sessions/${id}/messages`),
  executions: (project_id: number) => req<Execution[]>("/api/executions" + q({ project_id })),
};
