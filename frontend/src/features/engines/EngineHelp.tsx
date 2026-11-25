import { Check, Layers } from "lucide-react";
import { Dialog } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { ExternalLink } from "../../components/ui/external-link";
import { useApp } from "../../stores/app";
import { cn, fmtBytes } from "../../lib/utils";
import type { EngineId } from "../../services/types";

const NAME: Record<EngineId, string> = { ollama: "Ollama", llamacpp: "llama.cpp" };

/** "Help me choose": a comparison of the two engines, a recommendation for this machine and what llama.cpp needs. */
export function EngineHelp() {
  const { engineHelpOpen, setEngineHelpOpen, setLlamaOpen, engines, engine, setEngine } = useApp();
  const current = engine();
  const help = engines?.help;
  const rec = engines?.recommendation;
  const llama = engines?.engines.find((e) => e.id === "llamacpp");
  const gpu = engines?.hardware.gpu;
  const use = (id: EngineId) => { setEngineHelpOpen(false); void setEngine(id); };
  return (
    <Dialog open={engineHelpOpen} onOpenChange={setEngineHelpOpen} title="Ollama or llama.cpp?" description="What runs your models. You can switch at any time; each conversation remembers its engine."
      className="w-[min(860px,96vw)]">
      {!help ? <div className="text-xs text-muted">Reading the engines…</div> : (
        <div className="space-y-4 text-sm" data-testid="engine-help">
          {rec && (
            <div className="rounded-md border border-accent/40 bg-accent/10 px-3 py-2 text-xs" data-testid="engine-recommendation">
              <span className="font-medium text-accent">Suggested for this machine: {NAME[rec.engine]}.</span> <span className="text-muted">{rec.reason}</span>
              {gpu && <div className="mt-0.5 text-[11px] text-muted">GPU: {gpu.name} ({fmtBytes(gpu.total)})</div>}
            </div>)}
          <div className="overflow-hidden rounded-md border border-border">
            <table className="w-full text-xs">
              <thead className="bg-surface-2 text-left"><tr><th className="w-28 px-3 py-2" />
                {(["ollama", "llamacpp"] as EngineId[]).map((id) => (
                  <th key={id} className="px-3 py-2 font-semibold">
                    <span className="flex items-center gap-1.5">{NAME[id]}
                      {current === id && <span className="rounded bg-ok/15 px-1.5 text-[10px] font-normal text-ok">in use</span>}
                      {rec?.engine === id && <span className="rounded bg-accent px-1.5 text-[10px] font-normal text-accent-foreground">suggested</span>}</span></th>))}</tr></thead>
              <tbody>
                {help.rows.map((r) => (
                  <tr key={r.topic} className="border-t border-border/60 align-top">
                    <td className="px-3 py-1.5 font-medium text-muted">{r.topic}</td><td className="px-3 py-1.5">{r.ollama}</td><td className="px-3 py-1.5">{r.llamacpp}</td></tr>))}
              </tbody>
            </table>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 text-xs">
            {(["ollama", "llamacpp"] as EngineId[]).map((id) => (
              <div key={id} className="rounded-md border border-border px-3 py-2">
                <div className="mb-1 font-medium">Choose {NAME[id]} if…</div>
                <ul className="list-disc space-y-0.5 pl-4 text-muted">{help.when[id].map((w) => <li key={w}>{w}</li>)}</ul></div>))}
          </div>
          {llama && !llama.available && (
            <section data-testid="llama-install-steps">
              <div className="mb-1 text-xs font-medium text-warn">llama.cpp is not set up on this machine yet</div>
              <ol className="list-decimal space-y-1 pl-5 text-xs text-muted">{help.install.map((s) => <li key={s}>{s}</li>)}</ol>
              <div className="mt-1 text-[11px]"><ExternalLink href="https://github.com/ggml-org/llama.cpp/releases">github.com/ggml-org/llama.cpp/releases</ExternalLink></div>
            </section>)}
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-3">
            <Button variant="ghost" onClick={() => setEngineHelpOpen(false)}>Close</Button>
            <Button variant="outline" onClick={() => { setLlamaOpen(true); }}>llama.cpp settings…</Button>
            {(["ollama", "llamacpp"] as EngineId[]).map((id) => (
              <Button key={id} variant={current === id ? "secondary" : "default"} disabled={current === id} onClick={() => use(id)} data-testid={`use-${id}`}
                className={cn(current === id && "opacity-60")}>
                {current === id ? <><Check size={13} /> Using {NAME[id]}</> : <><Layers size={13} /> Use {NAME[id]}</>}
              </Button>))}
          </div>
        </div>)}
    </Dialog>
  );
}
