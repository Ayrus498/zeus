export type Mode = "read_only" | "approval" | "trusted" | "custom";
export type Policy = "allow" | "ask" | "deny";
export interface Project { id: number; name: string; path: string; last_opened: number; permission_mode: Mode; custom_permissions: string; exists?: boolean; readable?: boolean; writable?: boolean; is_git?: boolean }
export interface Session { id: number; project_id: number; title: string; model: string | null; num_ctx: number | null; created: number; updated: number; input_tokens: number; output_tokens: number; last_model?: string | null; engine?: EngineId | null }
export interface ModelInfo { name: string; size: number; family?: string; parameter_size?: string; quantization?: string; path?: string; context_length?: number | null; tools?: boolean; thinking?: boolean; embedding?: boolean; managed?: boolean }
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
  | { kind: "notice"; level: "info" | "error"; text: string }
  | { kind: "local"; title: string; body?: string; tone?: "ok" | "warn" | "error"; meter?: { used: number; window: number; trigger: number }; actions?: { label: string; cmd: string }[]; table?: { name: string; desc: string; cmd: string }[] };

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

/** The engines that can run models (see backend/app/providers/engines.py). */
export type EngineId = "ollama" | "llamacpp";
export interface LlamaServer { running: boolean; starting: string | null; model: string | null; path: string | null; port: number | null; pid: number | null; ctx: number | null; uptime: number | null; busy: number }
export interface GpuDevice { id: string; name: string; memory_mib: number; free_mib: number | null }
export interface Engine {
  id: EngineId; label: string; available: boolean; version?: string | null; endpoint?: string | null; binary?: string | null; error?: string | null;
  models: number; last_model?: string | null; devices?: GpuDevice[]; server?: LlamaServer;
}
export interface EngineHelp { rows: { topic: string; ollama: string; llamacpp: string }[]; when: Record<EngineId, string[]>; install: string[] }
export interface EnginesInfo {
  default: EngineId; engines: Engine[]; recommendation: { engine: EngineId; reason: string }; help: EngineHelp;
  hardware: { gpu: { total: number; free: number; name: string } | null; ram_available: number | null };
}
export interface LlamaSettings { ctx: number; ngl: string; threads: number; flash_attn: string; cache_type_k: string; cache_type_v: string; batch: number; ubatch: number; extra_args: string }
export interface LlamaConfig { binary: string; models_dirs: string[]; ollama_models: boolean; defaults: LlamaSettings; models: Record<string, Partial<LlamaSettings>>; last_model: string }
export interface LlamaModel {
  name: string; path: string; size: number; modified: string | null; family: string | null; parameter_size: string | null; quantization: string | null;
  context_length: number | null; tools: boolean; thinking: boolean; embedding: boolean; managed: boolean;
  source: ModelSource; removable: { kind: ModelSource; files: string[]; bytes: number } | null; ollama_name?: string; also?: string[]; projector?: boolean;
}
export type ModelSource = "managed" | "hf_cache" | "llama_cache" | "folder" | "ollama";
export interface LlamaFound { path: string; source: string; version: string | null; build: number | null; devices: string[]; gpu: boolean; auto: boolean }
export interface LlamaOverview {
  found: LlamaFound[]; binary_auto: boolean; binary_problem: string | null;
  config: LlamaConfig; server: LlamaServer; error: string | null; models: LlamaModel[]; folders: string[]; managed_dir: string; log: string; downloads: DownloadRow[];
  status: { connected: boolean; version?: string | null; binary?: string; error?: string; devices?: GpuDevice[]; running?: boolean };
}
export interface HfFiles { repo: string; url: string; suggested: string | null; hardware: ArenaHardware; files: { file: string; size: number; quant: string | null; split: boolean; installed: boolean; fit: ArenaFit }[] }
export interface HfSearch { query: string; results: { repo: string; downloads: number; likes: number; updated: string | null }[] }
export interface SavedState {
  path: string; state: { client?: string; engine?: string; model?: string; num_ctx?: number; project?: { path: string; name: string }; session?: { uid: string | null; id: number | null; title: string } };
  resolved: { project: { path: string } | null; session: Session | null; engine?: string; model?: string; num_ctx?: number } | null;
}

/** Arena benchmark data and what this machine can run (backend/app/providers/arena.py). */
export interface ArenaVerdict { level: "great" | "ok" | "slow" | "no"; label: string; text: string }
export interface ArenaQuant { quant: string; size_gb: number; where: string; tps: number | null; loss: number; quality: string }
export interface ArenaFit { where: "gpu" | "split" | "cpu" | "no"; weights_gb: number; kv_gb: number; serve_gb: number; in_ram_gb: number; tps: number | null; verdict: ArenaVerdict; quants?: ArenaQuant[]; best_quant?: string | null }
export interface ArenaOllama { family: string; tag: string; size: string | null; local: boolean }
export interface ArenaModel { name: string; org: string; license: string; rank: number; score: number; votes: number; repo: string | null; params_b: number | null; active_b: number | null; context: number | null; fit: ArenaFit | null; ollama: ArenaOllama | null }
export interface ArenaActivity { id: string; label: string; arena: string; blurb: string; date: string; total: number; top_paid: { name: string; score: number; rank: number }[]; best_here: ArenaModel | null; best_quality: ArenaModel | null; models: ArenaModel[] }
export interface ArenaHardware { gpu: string | null; vram_gb: number; vram_free_gb: number; bandwidth: number | null; bandwidth_known: boolean; gpu_catalogue_match: string | null; ram_gb: number; ram_bandwidth: number }
export interface ArenaOverview { updated: string; license: string; source: string; state: "bundled" | "cached" | "live"; error?: string | null; hardware: ArenaHardware; ctx: number; activities: ArenaActivity[] }
export interface ArenaFamilyResult { activity: string; label: string; name: string; rank: number; of: number; score: number; votes: number; size: string | null; params_b: number | null; fit: ArenaFit | null }
export interface ArenaFamily { license: string; updated: string; hardware: ArenaHardware; results: ArenaFamilyResult[] }

/** Project memory, the context meter and the rolling summary (backend/app/persistence/memory.py, agent/summary.py). */
export interface MemoryInfo { text: string; path: string; exists: boolean; bytes: number; max_bytes: number; prompt_chars: number }
export interface ContextInfo {
  tokens: number; window: number; percent: number; trigger_percent: number; messages_total: number; messages_in_context: number;
  summary: { text: string; covers: number; updated: number | null } | null; memory: MemoryInfo;
}
export interface CompactResult { ok: boolean; reason?: string; covers?: number; previous_covers?: number; summary?: string; tokens_before?: number; tokens_after?: number; context: ContextInfo }

/** Reset to a fresh installation (backend/app/persistence/reset.py). */
export interface ResetItem { id: string; label: string; detail: string; default: boolean; summary: string; counts: Record<string, number>; bytes: number; paths: string[]; paths_total: number }
export interface ResetPlan { confirm: string; items: ResetItem[]; keeps: string[] }
export interface ResetReport { items: { id: string; ok: boolean; files: number; bytes: number; errors: string[] }[]; removed_bytes: number; reload: boolean }
