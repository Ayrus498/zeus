export type Mode = "read_only" | "approval" | "trusted" | "custom";
export type Policy = "allow" | "ask" | "deny";
export interface Project { id: number; name: string; path: string; last_opened: number; permission_mode: Mode; custom_permissions: string; exists?: boolean; readable?: boolean; writable?: boolean; is_git?: boolean }
export interface Session { id: number; project_id: number; title: string; model: string | null; num_ctx: number | null; created: number; updated: number; input_tokens: number; output_tokens: number }
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
