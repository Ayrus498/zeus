import { useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, RotateCcw } from "lucide-react";
import { Dialog } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { fmtBytes } from "../../lib/utils";
import type { ResetItem, ResetPlan, ResetReport } from "../../services/types";

/** Reset: return Zeus-Agent to a fresh installation. Shows exactly what each choice removes, what is never touched, and asks for the word RESET. */
export function ResetDialog() {
  const { resetOpen, setResetOpen } = useApp();
  const [plan, setPlan] = useState<ResetPlan | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<ResetReport | null>(null);

  useEffect(() => {
    if (!resetOpen) return;
    setPlan(null); setError(null); setWord(""); setDone(null); setOpen(null);
    api.resetPlan().then((p) => { setPlan(p); setPicked(new Set(p.items.filter((i) => i.default).map((i) => i.id))); })
      .catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [resetOpen]);

  const toggle = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const total = plan?.items.filter((i) => picked.has(i.id)).reduce((a, i) => a + i.bytes, 0) ?? 0;
  const ready = !!plan && picked.size > 0 && word === plan.confirm && !busy;

  const run = async () => {
    if (!plan || !ready) return;
    setBusy(true); setError(null);
    try {
      const r = await api.reset([...picked], word);
      setDone(r);
      try { Object.keys(localStorage).filter((k) => k.startsWith("zeus")).forEach((k) => localStorage.removeItem(k)); } catch { /* storage unavailable */ }
      setTimeout(() => window.location.reload(), 2200);                 // the app starts again like a first run
    } catch (e) { setError(String(e instanceof Error ? e.message : e)); setBusy(false); }
  };

  const Row = ({ it }: { it: ResetItem }) => (
    <div className="rounded-md border border-border" data-testid={`reset-item-${it.id}`}>
      <label className="flex cursor-pointer items-start gap-2.5 px-3 py-2">
        <input type="checkbox" className="mt-1" checked={picked.has(it.id)} onChange={() => toggle(it.id)} aria-label={it.label} />
        <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{it.label}</span>
          <span className="block text-[11px] text-muted">{it.detail}</span>
          <span className="mt-0.5 block text-[11px]">{it.summary}{it.bytes ? ` · ${fmtBytes(it.bytes)}` : ""}</span></span>
        {it.paths_total > 0 && <button type="button" className="shrink-0 cursor-pointer text-[11px] text-accent hover:underline" onClick={(e) => { e.preventDefault(); setOpen(open === it.id ? null : it.id); }}>{open === it.id ? "hide" : "show"} files</button>}
      </label>
      {open === it.id && <ul className="max-h-32 overflow-auto border-t border-border/60 px-3 py-1.5 font-mono text-[10px] text-muted">
        {it.paths.map((p) => <li key={p} className="truncate" title={p}>{p}</li>)}{it.paths_total > it.paths.length && <li>… and {it.paths_total - it.paths.length} more</li>}</ul>}
    </div>
  );

  return (
    <Dialog open={resetOpen} onOpenChange={(o) => { if (!busy) setResetOpen(o); }} dismissable={!busy && !done} title="Reset Zeus-Agent" description="Remove what Zeus-Agent saved and start as a fresh installation" className="w-[min(720px,96vw)]">
      <div className="space-y-3 text-sm" data-testid="reset-dialog">
        {done ? (
          <div className="space-y-2" data-testid="reset-done">
            <div className="flex items-center gap-2 font-medium text-ok"><Check size={16} />Zeus-Agent was reset. Freed {fmtBytes(done.removed_bytes)}.</div>
            {done.items.some((i) => !i.ok) && <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">Some files could not be removed:
              <ul className="mt-1 list-disc pl-4 font-mono text-[10px]">{done.items.flatMap((i) => i.errors).map((e) => <li key={e}>{e}</li>)}</ul></div>}
            <div className="flex items-center gap-2 text-xs text-muted"><Loader2 size={12} className="animate-spin" />Restarting the interface…</div>
          </div>
        ) : !plan ? (error ? <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">{error}</div> : <div className="flex items-center gap-2 text-xs text-muted"><Loader2 size={12} className="animate-spin" />Looking at what Zeus-Agent has saved…</div>) : (
          <>
            <div className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger"><AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>This cannot be undone. Choose what to remove; the sizes below are what will be freed.</span></div>
            <div className="space-y-2">{plan.items.map((it) => <Row key={it.id} it={it} />)}</div>
            <div className="rounded-md border border-border bg-surface-2/40 px-3 py-2 text-xs" data-testid="reset-keeps">
              <div className="mb-0.5 font-medium text-muted">Never touched</div>
              <ul className="list-disc pl-4 text-muted">{plan.keeps.map((k) => <li key={k}>{k}</li>)}</ul></div>
            <div>
              <label htmlFor="reset-word" className="mb-1 block text-xs text-muted">Type <span className="font-mono font-semibold text-foreground">{plan.confirm}</span> to confirm</label>
              <input id="reset-word" value={word} onChange={(e) => setWord(e.target.value)} autoComplete="off" spellCheck={false} data-testid="reset-word"
                className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 font-mono text-sm outline-none focus:border-danger" />
            </div>
            {error && <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">{error}</div>}
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted">{picked.size} of {plan.items.length} selected · {fmtBytes(total)}</span>
              <Button className="ml-auto" variant="ghost" onClick={() => setResetOpen(false)} disabled={busy}>Cancel</Button>
              <Button variant="danger" disabled={!ready} onClick={() => void run()} data-testid="reset-run">{busy ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />} Reset selected</Button>
            </div>
          </>)}
      </div>
    </Dialog>
  );
}
