import type { ChangeEntry, Execution, ModelInfo, ModelMeta, OllamaStatus, Project, Session, TreeNode, Mode, Policy, Metrics, Catalog, DownloadRow, Overview, AutoPlan, AutoPreview, MissionRow, ConvertFormats, ConvertResult, LibraryInfo, LibraryList, LibrarySearch, AboutInfo, EngineId, EnginesInfo, HfSearch, ResetPlan, ResetReport, MemoryInfo, ContextInfo, CompactResult, ArenaOverview, ArenaFamily, ArenaHardware, ArenaFit, LlamaOverview, HfFiles, SavedState } from "./types";

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

export interface Settings { home: string; ollama_url: string; engine?: EngineId; default_model: string | null; default_num_ctx: number }
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
  models: (engine?: EngineId) => req<ModelInfo[]>("/api/models" + (engine ? q({ engine }) : "")),
  modelInfo: (name: string, num_ctx?: number, engine?: EngineId) => req<ModelMeta>("/api/models/info" + q({ name, num_ctx, engine })),
  engines: () => req<EnginesInfo>("/api/engines"),
  setEngine: (engine: EngineId, session_id?: number | null) => req<EnginesInfo>("/api/engine", "PUT", { engine, session_id: session_id ?? null }),
  llama: () => req<LlamaOverview>("/api/llamacpp"),
  llamaConfig: (patch: Record<string, unknown>) => req<LlamaOverview>("/api/llamacpp/config", "PUT", patch),
  llamaRescan: () => req<LlamaOverview>("/api/llamacpp/rescan", "POST"),
  llamaStart: (model: string) => req<{ starting: string }>("/api/llamacpp/server/start", "POST", { model }),
  llamaStop: () => req<LlamaOverview>("/api/llamacpp/server/stop", "POST"),
  llamaLog: (lines = 80) => req<{ log: string }>("/api/llamacpp/log" + q({ lines })),
  hfFiles: (repo: string) => req<HfFiles>("/api/llamacpp/hf" + q({ repo })),
  hfSearch: (query: string) => req<HfSearch>("/api/llamacpp/search" + q({ q: query })),
  llamaDownload: (repo: string, file: string) => req<DownloadRow>("/api/llamacpp/download", "POST", { repo, file }),
  llamaDelete: (model: string) => req<LlamaOverview & { deleted: { removed: string[]; bytes: number } }>("/api/llamacpp/models", "DELETE", { model }),
  arena: (refresh = false, ctx = 8192) => req<ArenaOverview>("/api/arena" + q({ refresh: refresh ? "true" : undefined, ctx })),
  arenaFamily: (family: string, ctx = 8192) => req<ArenaFamily>(`/api/arena/family/${encodeURIComponent(family).replace(/%2F/g, "/")}` + q({ ctx })),
  arenaDetail: (repo: string, ctx = 8192) => req<{ hardware: ArenaHardware; fit: ArenaFit }>("/api/arena/detail" + q({ repo, ctx })),
  memory: (pid: number) => req<MemoryInfo>(`/api/projects/${pid}/memory`),
  putMemory: (pid: number, text: string) => req<MemoryInfo>(`/api/projects/${pid}/memory`, "PUT", { text }),
  addMemoryNote: (pid: number, note: string) => req<MemoryInfo & { line: string; added: boolean }>(`/api/projects/${pid}/memory/note`, "POST", { note }),
  context: (sid: number) => req<ContextInfo>(`/api/sessions/${sid}/context`),
  compact: (sid: number) => req<CompactResult>(`/api/sessions/${sid}/compact`, "POST"),
  resetSummary: (sid: number) => req<ContextInfo>(`/api/sessions/${sid}/summary`, "DELETE"),
  resetPlan: () => req<ResetPlan>("/api/reset"),
  reset: (items: string[], confirm: string) => req<ResetReport>("/api/reset", "POST", { items, confirm }),
  openSession: (id: number) => req<{ ok: boolean }>(`/api/sessions/${id}/open`, "POST"),
  savedState: () => req<SavedState>("/api/state"),
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
  createSession: (b: { project_id: number; model?: string | null; num_ctx?: number | null; engine?: EngineId | null }) => req<Session>("/api/sessions", "POST", b),
  patchSession: (id: number, b: Partial<Pick<Session, "title" | "model" | "num_ctx">>) => req<Session>(`/api/sessions/${id}`, "PATCH", b),
  deleteSession: (id: number) => req<{ ok: boolean }>(`/api/sessions/${id}`, "DELETE"),
  missions: (id: number) => req<MissionRow[]>(`/api/sessions/${id}/missions`),
  messages: (id: number) => req<Array<{ role: string; agent: string; content: string; tool_calls: Array<{ function: { name: string; arguments: Record<string, unknown> } }> | null; tool_name: string | null }>>(`/api/sessions/${id}/messages`),
  executions: (project_id: number) => req<Execution[]>("/api/executions" + q({ project_id })),
};
