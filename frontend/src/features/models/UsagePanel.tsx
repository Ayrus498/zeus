import { useApp } from "../../stores/app";
import { fmtNum } from "../../lib/utils";
import { LiveMetrics } from "./LiveMetrics";

function Row({ k, v, hint }: { k: string; v: string; hint?: string }) {
  return <div className="flex justify-between border-b border-border py-1.5 text-xs"><span className="text-muted" title={hint}>{k}</span><span className="font-mono">{v}</span></div>;
}

export function UsagePanel() {
  const { session, usage, modelState, modelMeta } = useApp();
  if (!session) return <div className="h-full overflow-auto"><LiveMetrics /></div>;
  const cap = session.num_ctx ?? 0;
  const used = usage?.context_used ?? 0;
  const pct = cap ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  return (
    <div className="h-full overflow-auto">
    <LiveMetrics />
    <div className="border-t border-border p-4">
      <div className="mb-2 text-xs text-muted">Session</div><div className="mb-3 font-mono text-sm">{session.model ?? "—"}</div><div className="mb-3 font-mono text-sm">{session.model ?? "—"}</div>
      <Row k="Context capacity" v={fmtNum(cap)} hint="Configured num_ctx sent to Ollama" />
      <Row k="Model max context" v={fmtNum(modelMeta?.context_length)} hint="From model metadata" />
      <Row k="Input tokens (session)" v={fmtNum(session.input_tokens)} hint="Reported by Ollama (prompt_eval_count)" />
      <Row k="Output tokens (session)" v={fmtNum(session.output_tokens)} hint="Reported by Ollama (eval_count)" />
      <Row k="Total session tokens" v={fmtNum(session.input_tokens + session.output_tokens)} />
      <Row k="Last request context use" v={usage ? `${fmtNum(used)} (${pct}%)` : "–"} hint="prompt + output tokens of the last model call" />
      <Row k="Generation speed" v={usage?.tokens_per_sec ? `${usage.tokens_per_sec} tok/s` : "–"} hint="eval_count / eval_duration from Ollama" />
      <Row k="Model state" v={modelState === "loading" ? "loading / generating…" : modelState === "ready" ? "generating" : "idle"} />
      <div className="mt-3 h-1.5 overflow-hidden rounded bg-surface-2"><div className="h-full bg-accent" style={{ width: `${pct}%` }} /></div>
      <div className="mt-2 text-[11px] text-muted">All counts come from Ollama response metadata; none are estimated.{modelMeta?.context_check?.kv_bytes ? " The KV-cache memory figure in Settings is an estimate." : ""}</div>
    </div>
    </div>
  );
}
