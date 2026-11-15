import { useEffect, useRef, useState } from "react";
import { Play, Square } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Socket } from "../../services/socket";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import type { Execution } from "../../services/types";
import { cn } from "../../lib/utils";

interface Line { text: string; kind: "cmd" | "out" | "err" | "sys" }

export function TerminalPanel() {
  const { project, bump } = useApp();
  const [lines, setLines] = useState<Line[]>([]);
  const [cmd, setCmd] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [history, setHistory] = useState<Execution[]>([]);
  const [hIdx, setHIdx] = useState(-1);
  const sock = useRef<Socket | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const pid = project?.id;

  const loadHistory = () => { if (pid) api.executions(pid).then(setHistory).catch(() => undefined); };
  useEffect(() => {
    if (!pid) return;
    setLines([]); loadHistory();
    sock.current = new Socket(`/ws/terminal/${pid}`, (ev) => {
      if (ev.type === "command_start") { setRunId(ev.run_id); setLines((l) => [...l, { text: `$ ${ev.command}`, kind: "cmd" }]); }
      else if (ev.type === "command_output") setLines((l) => [...l, { text: ev.text, kind: ev.stream === "stderr" ? "err" : "out" }]);
      else if (ev.type === "command_end") { setRunId(null); setLines((l) => [...l, { text: `[${ev.status}] exit code ${ev.exit_code} · ${ev.duration}s`, kind: "sys" }]); loadHistory(); bump(); }
      else if (ev.type === "command_denied") setLines((l) => [...l, { text: `Denied: ${ev.reason}`, kind: "sys" }]);
    });
    return () => sock.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }); }, [lines]);

  const run = () => { if (!cmd.trim() || runId) return; sock.current?.send({ type: "run", command: cmd.trim() }); setCmd(""); setHIdx(-1); };
  if (!project) return <div className="p-4 text-xs text-muted">No project open.</div>;
  const cmds = history.map((h) => h.command);
  return (
    <div className="flex h-full flex-col">
      <div ref={box} className="min-h-0 flex-1 overflow-auto bg-[#0d1117] p-2 font-mono text-[12px]" aria-label="Terminal output">
        {!lines.length && <div className="text-muted">Commands run in {project.path}. History: ↑/↓. Commands are denied in Read-only mode.</div>}
        {lines.map((l, i) => <span key={i} className={cn("whitespace-pre-wrap", l.kind === "cmd" && "block text-accent", l.kind === "err" && "text-danger", l.kind === "sys" && "block text-muted", l.kind === "out" && "text-foreground")}>{l.text}</span>)}
      </div>
      <div className="flex items-center gap-2 border-t border-border p-2">
        <span className="font-mono text-xs text-accent">$</span>
        <input value={cmd} onChange={(e) => setCmd(e.target.value)} aria-label="Terminal command" placeholder="command…"
          onKeyDown={(e) => {
            if (e.key === "Enter") run();
            if (e.key === "ArrowUp" && cmds.length) { const n = Math.min(hIdx + 1, cmds.length - 1); setHIdx(n); setCmd(cmds[n]); e.preventDefault(); }
            if (e.key === "ArrowDown") { const n = Math.max(hIdx - 1, -1); setHIdx(n); setCmd(n < 0 ? "" : cmds[n]); e.preventDefault(); }
          }} className="h-8 flex-1 bg-transparent font-mono text-xs outline-none" />
        {runId ? <Button variant="danger" size="sm" onClick={() => sock.current?.send({ type: "cancel", run_id: runId })}><Square size={11} fill="currentColor" /> Cancel</Button>
          : <Button size="sm" onClick={run} disabled={!cmd.trim()}><Play size={11} /> Run</Button>}
      </div>
      {history.length > 0 && <div className="max-h-24 overflow-auto border-t border-border px-2 py-1 text-[11px] text-muted">
        {history.slice(0, 8).map((h) => <div key={h.id} className="flex gap-2 font-mono"><span className={h.exit_code === 0 ? "text-ok" : "text-danger"}>{h.exit_code ?? "…"}</span><button className="cursor-pointer truncate text-left hover:text-foreground" onClick={() => setCmd(h.command)}>{h.command}</button></div>)}</div>}
    </div>
  );
}
