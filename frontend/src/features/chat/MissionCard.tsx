import { memo, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, CircleDashed, FileText, Loader2, Pause, Target, X } from "lucide-react";
import { cn } from "../../lib/utils";
import type { MissionState, MissionStatus } from "../../services/types";

type Item = { kind: "mission" } & MissionState;

const STATUS: Record<MissionStatus, { text: string; cls: string }> = {
  running: { text: "working", cls: "bg-accent/20 text-accent" },
  reviewing: { text: "reviewing", cls: "bg-accent/20 text-accent" },
  accepted: { text: "accepted", cls: "bg-ok/20 text-ok" },
  stalled: { text: "stalled", cls: "bg-warn/20 text-warn" },
  limit: { text: "round limit", cls: "bg-warn/20 text-warn" },
  stopped: { text: "stopped", cls: "bg-surface-2 text-muted" },
  error: { text: "error", cls: "bg-danger/20 text-danger" },
};

function Plan({ plan }: { plan: Record<string, unknown> }) {
  const chips: string[] = [];
  if (plan.model) chips.push(String(plan.model));
  if (plan.temperature != null) chips.push(`temp ${plan.temperature}`);
  if (plan.tools) chips.push(`tools: ${plan.tools}`);
  if (plan.think != null && plan.think !== "") chips.push(`thinking: ${plan.think === false ? "off" : String(plan.think)}`);
  if (plan.system) chips.push(String(plan.system));
  if (!chips.length) return null;
  return (
    <div className="flex flex-wrap gap-1" title="How ZEUS prepared its requests for this model, from Ollama's metadata and the model family's recommendations" data-testid="mission-plan">
      {chips.map((c) => <span key={c} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted">{c}</span>)}
    </div>
  );
}

export const MissionCard = memo(function MissionCard({ item }: { item: Item }) {
  const [doc, setDoc] = useState(false);
  const live = item.status === "running" || item.status === "reviewing";
  const st = STATUS[item.status];
  const met = item.criteria.filter((c) => c.met === true).length;
  return (
    <div className={cn("my-2 overflow-hidden rounded-lg border bg-surface", item.status === "accepted" ? "border-ok/50" : live ? "border-accent/50" : "border-border")} data-testid="mission-card" data-status={item.status}>
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Target size={15} className="shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2"><span className="truncate text-sm font-medium">{item.title || item.name}</span>
            <span className={cn("shrink-0 rounded px-1.5 py-0.5 text-[10px]", st.cls)} data-testid="mission-status">{live && <Loader2 size={9} className="mr-1 inline animate-spin" />}{st.text}</span></div>
          <div className="flex items-center gap-1.5 text-[11px] text-muted"><FileText size={11} />{item.name}
            {item.maxRounds > 0 && <span>· round {Math.max(item.round, live ? 1 : item.round)}/{item.maxRounds}</span>}
            {item.criteria.length > 0 && <span>· {met}/{item.criteria.length} criteria met</span>}</div>
        </div>
      </div>
      <div className="space-y-2 p-3 text-xs">
        {item.objective && <div><span className="text-muted">Objective </span>{item.objective}</div>}
        {item.criteria.length > 0 && (
          <ul className="space-y-1" aria-label="Acceptance criteria">
            {item.criteria.map((c) => (
              <li key={c.id} className="flex items-start gap-1.5" data-testid={`criterion-${c.id}`} data-met={String(c.met)}>
                {c.met === true ? <Check size={13} className="mt-0.5 shrink-0 text-ok" /> : c.met === false ? <X size={13} className="mt-0.5 shrink-0 text-danger" /> : <CircleDashed size={13} className="mt-0.5 shrink-0 text-muted" />}
                <div className="min-w-0"><div className={cn(c.met === true && "text-foreground", c.met === false && "text-foreground")}>{c.text}</div>
                  {c.evidence && <div className="text-[11px] text-muted">{c.evidence}</div>}</div>
              </li>))}
          </ul>)}
        <Plan plan={item.plan} />
        {item.status === "reviewing" && <div className="flex items-center gap-1.5 text-accent"><Loader2 size={12} className="animate-spin" />Reviewing the work against the criteria…</div>}
        {item.feedback && !["accepted"].includes(item.status) && (
          <div className="rounded-md border border-warn/30 bg-warn/5 p-2 text-[11px]" data-testid="mission-feedback"><div className="mb-0.5 flex items-center gap-1 font-medium text-warn"><AlertTriangle size={11} />Reviewer feedback</div>{item.feedback}</div>)}
        {item.summary && <div className={cn("flex items-start gap-1.5 rounded-md p-2", item.status === "accepted" ? "bg-ok/10 text-ok" : "bg-warn/10 text-warn")} data-testid="mission-summary">
          {item.status === "accepted" ? <Check size={13} className="mt-0.5 shrink-0" /> : item.status === "stopped" ? <Pause size={13} className="mt-0.5 shrink-0" /> : <AlertTriangle size={13} className="mt-0.5 shrink-0" />}{item.summary}</div>}
        {item.doc && (
          <div><button className="flex cursor-pointer items-center gap-1 text-[11px] text-muted hover:text-foreground" onClick={() => setDoc(!doc)} aria-expanded={doc}>
            {doc ? <ChevronDown size={12} /> : <ChevronRight size={12} />}Attached document</button>
            {doc && <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-background p-2 font-mono text-[11px] text-muted">{item.doc}</pre>}</div>)}
      </div>
    </div>
  );
});
