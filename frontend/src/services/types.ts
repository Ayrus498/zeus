export type Mode = "read_only" | "approval" | "trusted" | "custom";
export type Policy = "allow" | "ask" | "deny";
export interface Project { id: number; name: string; path: string; last_opened: number; permission_mode: Mode; custom_permissions: string; exists?: boolean; readable?: boolean; writable?: boolean; is_git?: boolean }
export interface Session { id: number; project_id: number; title: string; model: string | null; num_ctx: number | null; created: number; updated: number; input_tokens: number; output_tokens: number; last_model?: string | null }
export interface ModelInfo { name: string; size: number; family?: string; parameter_size?: string; quantization?: string }
export interface ModelMeta { name: string; context_length: number | null; size: number; tools_supported: boolean | null; capabilities: string[];
  context_check?: { warning: string | null; kv_bytes: number | null; needed_bytes?: number; estimate: boolean } }
export interface TreeNode { name: string; path: string; is_dir: boolean; symlink: boolean }
export interface OllamaStatus { connected: boolean; version?: string; endpoint: string; error?: string; loaded?: string[] }
export interface PermissionRequest { id: string; agent: string; project: string; action: string; target: string; reason: string; detail: { diff?: string; content?: string; cwd?: string }; session_id: number }
export interface ChangeEntry { path: string; status: "created" | "modified" | "deleted" }
export interface Execution { id: number; command: string; started: number; exit_code: number | null; status: string; output: string }
export interface Usage { prompt_tokens: number; output_tokens: number; tokens_per_sec: number | null; session_input: number; session_output: number; context_used: number }

export type TimelineItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; agent: string; text: string; streaming?: boolean }
  | { kind: "tool"; id: string; name: string; args: Record<string, unknown>; result?: string; ok?: boolean }
  | { kind: "command"; runId: string; command: string; output: string; status: "running" | "completed" | "timeout" | "cancelled"; exitCode?: number | null }
  | ({ kind: "mission" } & MissionState)
  | { kind: "route"; model: string; role: string; reason: string; previous?: string | null; tools: boolean }
  | { kind: "notice"; level: "info" | "error"; text: string };

export interface GpuSample { vendor: string; index: number | null; name: string; util_pct: number | null; mem_util_pct: number | null; mem_used: number | null; mem_total: number | null; temp_c: number | null; power_w: number | null }
export interface LoadedModel { name: string; size: number; size_vram: number; gpu_pct: number | null; processor: string; context_length?: number | null; expires_at?: string | null; details?: { parameter_size?: string; quantization_level?: string } }
export interface Metrics {
  ts: number;
  cpu: { percent: number | null; cores: number; load1: number };
  ram: { total: number | null; used: number | null };
  ollama_proc: { running: boolean; cpu_percent: number | null; cpu_percent_of_total: number | null; rss: number | null };
  gpus: GpuSample[];
  ollama: { connected: boolean; version?: string; loaded: LoadedModel[] };
}

export interface CatalogTag { tag: string; size_gb: number; latest: boolean }
export type Fit = "best" | "good" | "limited" | "no";
export interface CatalogModel {
  name: string; title: string; maker: string; description: string; best_for: string; purposes: string[]; capabilities: string[];
  fit: Fit; context: string | null; note: string | null; url: string; tags: CatalogTag[];
  about: string; examples: { prompt: string; shows: string }[];
}
export interface LibraryInfo {
  family: string; url: string; summary: string; readme: string; capabilities: string[]; updated: string | null;
  tags: { tag: string; size: string | null; context: string | null; latest: boolean; kind: string | null; updated: string | null }[];
  source: "live" | "cache" | "offline-cache" | "offline"; fetched_at?: number; error?: string | null;
}
/** One model family from the public Ollama library (or a community model found by searching ollama.com). */
export interface LibraryEntry {
  name: string; description: string; capabilities: string[]; sizes: string[]; pulls: string; pulls_n: number;
  tags: number | null; updated: string | null; community: boolean; url: string;
}
export interface LibraryList { models: LibraryEntry[]; fetched_at: number | null; source: "live" | "cache" | "offline-cache" | "offline"; error?: string | null }
export interface LibrarySearch { query: string; models: LibraryEntry[]; source: "live" | "cache" | "offline" | "none"; error: string | null }
export interface Catalog { purposes: Record<string, string>; library_url: string; models: CatalogModel[]; note: string }
export interface DownloadRow {
  id: number; model: string; status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted"; detail: string;
  completed: number; total: number; percent: number | null; error: string | null; started: number; updated: number;
}
export interface Overview {
  connected: boolean; version?: string; endpoint: string; error?: string;
  installed: ModelInfo[]; loaded: LoadedModel[]; downloads: DownloadRow[];
  hardware: { gpu: { total: number; free: number; name: string } | null; ram_total: number | null; ram_available: number | null };
}
export interface AboutInfo {
  name: string; tagline: string; version: string; founder: string; github: string; website: string; license: string;
  platform: { os: string; kernel: string; arch: string; python: string; ollama: string | null };
  cpu: { name: string | null; cores: number | null; threads: number | null };
  gpus: { name: string; vendor: string | null; vram_total: number | null; vram_used: number | null }[];
  memory: { total: number | null; available: number | null; used: number | null; swap_total: number | null; swap_used: number | null };
}
export type SettingsView = "endpoint" | "model" | "installed" | "about";
export interface Toast { id: number; level: "info" | "error" | "success"; text: string }

export interface AutoRole { name: string; title: string; fit: Fit; capabilities: string[]; size: number; loaded: boolean; purposes: string[]; why: string[]; tools: boolean }
export interface AutoPlan {
  chat_models: number; candidates: string[]; pins: { code: string | null; think: string | null };
  roles: { code: AutoRole | null; think: AutoRole | null };
  warnings: string[]; suggestions: Array<{ role: "code" | "think"; tag: string; size_gb: number | null; why: string }>;
}
export interface AutoPreview { intent: "code" | "think" | "keep"; choice: { model: string; role: string; tools: boolean; reason: string } | null }

export interface MissionCriterion { id: number; text: string; met: boolean | null; evidence: string }
export type MissionStatus = "running" | "reviewing" | "accepted" | "stalled" | "limit" | "stopped" | "error";
export interface MissionState {
  mid: number | null; name: string; title: string; objective: string; criteria: MissionCriterion[]; status: MissionStatus;
  round: number; maxRounds: number; plan: Record<string, unknown>; feedback: string; summary: string; doc: string;
}
export interface MissionRow { id: number; name: string; title: string; objective: string; criteria: MissionCriterion[]; status: MissionStatus; round: number; max_rounds: number; plan: Record<string, unknown>; log: Array<{ feedback?: string }> }
export interface Attachment { name: string; content: string; size: number; source?: string; truncated?: boolean; omitted?: number }
export interface ConvertResult { name: string; source: string; markdown: string; title: string | null; chars: number; truncated: boolean; omitted_chars: number; converter: string }
export interface ConvertFormats { installed: boolean; version: string | null; max_bytes: number; formats: Array<{ ext: string; label: string; needs_markitdown: boolean }> }
