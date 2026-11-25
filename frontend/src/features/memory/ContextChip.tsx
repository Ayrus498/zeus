import { useApp } from "../../stores/app";
import { cn, fmtCtx } from "../../lib/utils";
import { runSlash } from "../commands/commands";

/** A small context meter beside the attach button: how much of the model's window this conversation uses. Click for details. */
export function ContextChip() {
  const c = useApp((s) => s.ctxInfo);
  if (!c) return null;
  const pct = Math.min(100, c.percent);
  return (
    <button type="button" onClick={() => void runSlash("/context")} data-testid="context-chip"
      title={`~${c.tokens.toLocaleString()} of ${fmtCtx(c.window)} tokens${c.summary ? ` · ${c.summary.covers} older messages summarised` : ""}${c.memory.exists ? " · project memory in use" : ""}. Click for details.`}
      className="flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2 text-[11px] text-muted hover:bg-surface-2 hover:text-foreground">
      <span className="relative h-1.5 w-12 overflow-hidden rounded bg-surface-2"><span className={cn("absolute inset-y-0 left-0 rounded", pct >= c.trigger_percent ? "bg-warn" : "bg-accent")} style={{ width: `${pct}%` }} /></span>
      <span className="font-mono">{pct}%</span>
      {c.summary && <span className="rounded bg-accent/15 px-1 text-accent" title="Older messages are summarised">Σ</span>}
      {c.memory.exists && <span className="rounded bg-accent/15 px-1 text-accent" title="Project memory in use">mem</span>}
    </button>
  );
}
