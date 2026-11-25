import { CircleHelp, Layers, Settings2, TriangleAlert } from "lucide-react";
import { Select } from "../../components/ui/select";
import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";
import type { EngineId } from "../../services/types";

const Dot = ({ ok }: { ok: boolean | undefined }) => <span className={cn("inline-block h-1.5 w-1.5 rounded-full", ok ? "bg-ok" : "bg-danger")} aria-hidden />;

/** The engine dropdown that sits before the model dropdown: Ollama or llama.cpp, plus "Help me choose" and the llama.cpp settings. */
export function EngineSelector() {
  const { engines, engine, running, setEngine, setEngineHelpOpen, setLlamaOpen } = useApp();
  const current = engine();
  const info = engines?.engines.find((e) => e.id === current);
  const unavailable = !!engines && !!info && !info.available;
  const change = (v: string) => {
    if (v === "__help") setEngineHelpOpen(true);
    else if (v === "__config") setLlamaOpen(true);
    else if (v === "ollama" || v === "llamacpp") void setEngine(v as EngineId);
  };
  const byId = (id: EngineId) => engines?.engines.find((e) => e.id === id);
  return (
    <div className="flex items-center gap-1.5" data-testid="engine-selector">
      <Layers size={14} className="text-accent" />
      <Select aria-label="Engine" value={current} disabled={running} onChange={(e) => change(e.target.value)}>
        <option value="ollama"><span className="flex items-center gap-1.5"><Dot ok={byId("ollama")?.available} />Ollama</span></option>
        <option value="llamacpp"><span className="flex items-center gap-1.5"><Dot ok={byId("llamacpp")?.available} />llama.cpp</span></option>
        <option value="__help"><span className="flex items-center gap-1.5"><CircleHelp size={12} />Help me choose…</span></option>
        <option value="__config"><span className="flex items-center gap-1.5"><Settings2 size={12} />llama.cpp settings…</span></option>
      </Select>
      {unavailable && (
        <button type="button" onClick={() => (current === "llamacpp" ? setLlamaOpen(true) : setEngineHelpOpen(true))} data-testid="engine-unavailable"
          title={info?.error ?? "This engine is not available"} className="flex cursor-pointer items-center gap-1 text-xs text-warn hover:underline">
          <TriangleAlert size={12} />{current === "llamacpp" ? "llama-server not found: set up" : "not running"}
        </button>)}
    </div>
  );
}
