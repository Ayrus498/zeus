import { useApp } from "../stores/app";
import { cn, fmtNum } from "../lib/utils";

export function StatusBar() {
  const { project, session, ollama, usage, modelState, activeModel } = useApp();
  const cap = session?.num_ctx;
  return (
    <footer className="flex h-7 shrink-0 items-center gap-4 overflow-hidden whitespace-nowrap border-t border-border bg-surface px-3 text-[11px] text-muted">
      <span>Project: <b className="font-medium text-foreground">{project?.name ?? "–"}</b></span>
      <span>Model: <b className="font-medium text-foreground">{session?.model === "auto" ? `auto${activeModel ? ` → ${activeModel}` : ""}` : session?.model ?? "–"}</b>{modelState === "loading" && " (loading…)"}</span>
      <span className="flex items-center gap-1"><span className={cn("h-1.5 w-1.5 rounded-full", ollama?.connected ? "bg-ok" : "bg-danger")} />Ollama {ollama?.connected ? `v${ollama.version}` : "disconnected"}</span>
      <span>Context: {fmtNum(usage?.context_used ?? 0)} / {fmtNum(cap)} tokens</span>
      <span>In/Out: {fmtNum(session?.input_tokens)} / {fmtNum(session?.output_tokens)}</span>
      {usage?.tokens_per_sec ? <span>{usage.tokens_per_sec} tok/s</span> : null}
      <span className="ml-auto truncate font-mono">{project?.path}</span>
    </footer>
  );
}
