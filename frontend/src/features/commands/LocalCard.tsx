import { AlertTriangle, Check, Info } from "lucide-react";
import { Button } from "../../components/ui/button";
import { cn, fmtCtx } from "../../lib/utils";
import type { TimelineItem } from "../../services/types";
import { Markdown } from "../chat/Markdown";
import { runSlash } from "./commands";

/** The answer to a slash command, shown in the conversation: text, a meter, a table of commands and buttons that run further commands. */
export function LocalCard({ item }: { item: Extract<TimelineItem, { kind: "local" }> }) {
  const Icon = item.tone === "error" ? AlertTriangle : item.tone === "ok" ? Check : Info;
  const pct = item.meter ? Math.min(100, Math.round((100 * item.meter.used) / item.meter.window)) : 0;
  return (
    <div className={cn("rounded-lg border bg-surface px-3 py-2.5 text-sm", item.tone === "error" ? "border-danger/50" : item.tone === "warn" ? "border-warn/50" : "border-border")} data-testid="local-card">
      <div className="flex items-center gap-2 text-xs font-medium">
        <Icon size={13} className={item.tone === "error" ? "text-danger" : item.tone === "warn" ? "text-warn" : "text-accent"} />{item.title}
      </div>
      {item.meter && (
        <div className="mt-2" data-testid="context-meter">
          <div className="relative h-2 overflow-hidden rounded bg-surface-2">
            <div className={cn("h-2 rounded", pct >= item.meter.trigger ? "bg-warn" : "bg-accent")} style={{ width: `${pct}%` }} />
            <div className="absolute inset-y-0 w-px bg-foreground/50" style={{ left: `${item.meter.trigger}%` }} title={`Older messages are summarised at ${item.meter.trigger}%`} />
          </div>
          <div className="mt-1 text-[11px] text-muted">~{item.meter.used.toLocaleString()} of {fmtCtx(item.meter.window)} tokens ({pct}%) · summarised automatically at {item.meter.trigger}%</div>
        </div>)}
      {item.body && <div className="mt-1.5 text-[13px]"><Markdown text={item.body} streaming={false} /></div>}
      {item.table && (
        <div className="mt-2 divide-y divide-border/50 rounded-md border border-border text-xs">
          {item.table.map((r) => (
            <button key={r.name} type="button" disabled={!r.cmd} onClick={() => void runSlash(r.cmd)}
              className="flex w-full cursor-pointer items-baseline gap-3 px-2.5 py-1.5 text-left hover:bg-surface-2 disabled:cursor-default disabled:hover:bg-transparent">
              <code className="w-52 shrink-0 font-mono text-accent">{r.name}</code><span className="text-muted">{r.desc}</span></button>))}
        </div>)}
      {item.actions && item.actions.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">{item.actions.map((a) => <Button key={a.cmd} size="sm" variant="outline" onClick={() => void runSlash(a.cmd)}>{a.label}</Button>)}</div>)}
    </div>
  );
}
