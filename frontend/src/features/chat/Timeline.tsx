import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, Target, FilePlus, FileEdit, FileSearch, FolderOpen, GitBranch, ArrowDown, Loader2, Search, Terminal, Trash2, X, Zap } from "lucide-react";
import type { TimelineItem } from "../../services/types";
import { cn } from "../../lib/utils";
import { Markdown } from "./Markdown";
import { useApp } from "../../stores/app";
import { ApprovalCard } from "../permissions/ApprovalCard";
import { MissionCard } from "./MissionCard";
import { CodeBox } from "../../components/ui/copy";
import { useStickScroll } from "../../hooks/useStickScroll";

const ICONS: Record<string, typeof Terminal> = {
  list_files: FolderOpen, read_file: FileSearch, search_files: Search, create_file: FilePlus, edit_file: FileEdit,
  delete_file: Trash2, run_command: Terminal, git_status: GitBranch, git_diff: GitBranch,
};

const ToolCard = memo(function ToolCard({ item }: { item: Extract<TimelineItem, { kind: "tool" }> }) {
  const [open, setOpen] = useState(false);
  const Icon = ICONS[item.name] ?? Zap;
  const target = String(item.args.path ?? item.args.command ?? item.args.pattern ?? "");
  const pending = item.result === undefined;
  return (
    <div className="my-1.5 rounded-lg border border-border bg-surface">
      <button className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-xs" onClick={() => setOpen(!open)}>
        <Icon size={14} className="text-accent" />
        <span className="font-medium">{item.name}</span>
        <span className="truncate font-mono text-muted">{target}</span>
        <span className="ml-auto flex items-center gap-1">
          {pending ? <Loader2 size={13} className="animate-spin text-muted" /> : item.ok ? <Check size={13} className="text-ok" /> : <X size={13} className="text-danger" />}
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border p-3 text-xs">
          {item.args.reason ? <div className="text-muted">Reason: {String(item.args.reason)}</div> : null}
          {item.name === "create_file" && <CodeBox text={String(item.args.content ?? "")}><pre className="max-h-64 overflow-auto rounded bg-[#0d1117] p-2 font-mono">{String(item.args.content ?? "")}</pre></CodeBox>}
          {item.name === "edit_file" && <CodeBox text={String(item.args.new_str ?? "")}><pre className="max-h-64 overflow-auto rounded bg-[#0d1117] p-2 font-mono"><span className="text-danger">{"- " + String(item.args.old_str ?? "").split("\n").join("\n- ")}</span>{"\n"}<span className="text-ok">{"+ " + String(item.args.new_str ?? "").split("\n").join("\n+ ")}</span></pre></CodeBox>}
          {item.result !== undefined && <CodeBox text={item.result}><pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-background p-2 font-mono text-muted">{item.result}</pre></CodeBox>}
        </div>
      )}
    </div>
  );
});

const CommandCard = memo(function CommandCard({ item }: { item: Extract<TimelineItem, { kind: "command" }> }) {
  const ref = useRef<HTMLPreElement>(null);
  useStickScroll(() => ref.current, item.output, !!item.output);          // follows the output, but lets you scroll up while it runs
  const color = item.status === "running" ? "text-warn" : item.exitCode === 0 ? "text-ok" : "text-danger";
  return (
    <div className="my-1.5 rounded-lg border border-border bg-[#0d1117]">
      <div className="flex items-center gap-2 border-b border-border px-3 py-1.5 font-mono text-xs">
        <Terminal size={13} className="text-accent" /><span className="truncate">$ {item.command}</span>
        <span className={cn("ml-auto shrink-0", color)}>{item.status === "running" ? "running…" : `${item.status === "completed" ? "exit" : item.status} ${item.exitCode ?? ""}`}</span>
      </div>
      {item.output && <CodeBox text={item.output}><pre ref={ref} className="max-h-60 overflow-auto whitespace-pre-wrap px-3 py-2 font-mono text-[12px] text-muted">{item.output}</pre></CodeBox>}
    </div>
  );
});

/** Animated "…" that loops . → .. → ... while the agent is working. */
function Dots() {
  const [n, setN] = useState(1);
  useEffect(() => { const t = setInterval(() => setN((x) => (x % 3) + 1), 450); return () => clearInterval(t); }, []);
  return <span className="inline-block w-5 text-left" aria-hidden>{".".repeat(n)}</span>;
}

/** Pinned to the top of the chat while a mission runs, so progress stays visible after the card scrolls away. */
function MissionBar() {
  const m = useApp((s) => [...s.timeline].reverse().find((i) => i.kind === "mission" && (i.status === "running" || i.status === "reviewing")));
  if (!m || m.kind !== "mission") return null;
  const met = m.criteria.filter((c) => c.met === true).length;
  return (
    <div className="sticky top-0 z-10 -mx-4 flex items-center gap-2 border-b border-accent/30 bg-background/95 px-4 py-1.5 text-[11px] backdrop-blur" data-testid="mission-bar" role="status">
      <Target size={12} className="shrink-0 text-accent" />
      <span className="truncate font-medium">{m.title || m.name}</span>
      <span className="text-muted">round {Math.max(m.round, 1)}/{m.maxRounds} · {met}/{m.criteria.length} criteria met · {m.status === "reviewing" ? "reviewing" : "working"}</span>
      <Loader2 size={11} className="ml-auto shrink-0 animate-spin text-accent" />
    </div>
  );
}

function WorkingIndicator() {
  const { running, pending } = useApp();
  const reviewing = useApp((s) => s.timeline.some((i) => i.kind === "mission" && i.status === "reviewing"));
  if (!running) return null;
  const waiting = pending.length > 0;
  return (
    <div className="flex items-center gap-2 py-1 text-sm text-muted" role="status" aria-live="polite" data-testid="working-indicator">
      <Zap size={16} className={cn("text-accent", waiting ? "" : "animate-pulse")} />
      <span>{waiting ? "Waiting for your approval" : reviewing ? "Reviewing the mission" : "Thunderstorm is approaching"}<Dots /></span>
    </div>
  );
}

export function Timeline({ items }: { items: TimelineItem[] }) {
  const root = useRef<HTMLDivElement>(null);
  const pendingCount = useApp((s) => s.pending.length);
  // Follow the newest content only while the reader is at the bottom; scrolling up while Zeus-Agent writes stays where you put it.
  const { following, jump } = useStickScroll(() => root.current?.parentElement, [items, pendingCount], items.length > 0);
  useLayoutEffect(() => { if (items[items.length - 1]?.kind === "user") jump(); }, [items, jump]);     // you just sent something: go down
  if (!items.length) return (
    <div className="grid h-full place-items-center text-center text-muted">
      <div><Zap className="mx-auto mb-2 text-accent" size={28} /><div className="text-foreground">What should we build?</div>
        <div className="mt-1 text-xs">Zeus-Agent reads, edits and runs code in your project — with your approval.</div></div>
    </div>
  );
  return (
    <div ref={root} className="mx-auto max-w-3xl space-y-3 px-4 py-4">
      <MissionBar />
      {items.map((it, i) => {
        if (it.kind === "user") return <div key={i} className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-surface-2 px-4 py-2.5">{it.text}</div>;
        if (it.kind === "assistant") return (
          <div key={i}><div className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-accent">Zeus-Agent</div>
            <Markdown text={it.text} streaming={!!it.streaming} /></div>);
        if (it.kind === "route") return (
          <div key={i} className="flex items-start gap-1.5 text-[11px] text-muted" title={it.reason} data-testid="route-note">
            <Zap size={12} className="mt-0.5 shrink-0 text-accent" />
            <span>Auto picked <span className="font-mono text-foreground">{it.model}</span> for {it.role === "think" ? "reasoning" : it.role === "code" ? "coding" : "this follow-up"}
              {it.previous && it.previous !== it.model ? <> (switching from <span className="font-mono">{it.previous}</span>; loading may take a few seconds)</> : null}
              {!it.tools ? " · no tools this turn (reasoning-only model)" : ""}</span></div>);
        if (it.kind === "mission") return <MissionCard key={`m${it.mid ?? i}`} item={it} />;
        if (it.kind === "tool") return <ToolCard key={i} item={it} />;
        if (it.kind === "command") return <CommandCard key={i} item={it} />;
        return <div key={i} className={cn("flex items-start gap-2 rounded-md px-3 py-2 text-xs", it.level === "error" ? "bg-danger/15 text-danger" : "bg-surface text-muted")}>
          {it.level === "error" && <AlertTriangle size={14} className="mt-0.5 shrink-0" />}{it.text}</div>;
      })}
      <ApprovalCard />
      <WorkingIndicator />
      {!following && (
        <div className="pointer-events-none sticky bottom-2 flex justify-center">
          <button type="button" onClick={jump} data-testid="jump-latest"
            className="pointer-events-auto flex cursor-pointer items-center gap-1 rounded-full border border-border bg-surface px-3 py-1 text-xs text-foreground shadow-lg hover:bg-surface-2">
            <ArrowDown size={12} /> Jump to latest
          </button>
        </div>
      )}
    </div>
  );
}
