import { useEffect, useState } from "react";
import { Cpu, TriangleAlert } from "lucide-react";
import { Select } from "../../components/ui/select";
import { useApp } from "../../stores/app";
import { fmtBytes, fmtCtx } from "../../lib/utils";

const PRESETS = [8192, 16384, 32768, 65536];

export function ModelSelector() {
  const { models, session, modelMeta, setModel, setCtx, ollama } = useApp();
  const missing = !!session?.model && models.length > 0 && !models.some((m) => m.name === session.model);
  const max = modelMeta?.context_length ?? null;
  const [custom, setCustom] = useState(false);
  useEffect(() => setCustom(false), [session?.id]);
  const ctx = session?.num_ctx ?? 16384;
  const options = PRESETS.filter((p) => !max || p <= max);
  const isMaxSel = !!max && ctx === max;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Cpu size={14} className="text-accent" />
      <Select aria-label="Model" value={session?.model ?? ""} disabled={!ollama?.connected} onChange={(e) => void setModel(e.target.value)}>
        {!session?.model && <option value="">Select model…</option>}
        {missing && <option value={session!.model!}>{session!.model} (not installed)</option>}
        {models.map((m) => <option key={m.name} value={m.name}>{m.name} · {fmtBytes(m.size)}</option>)}
      </Select>
      <Select aria-label="Context window" value={isMaxSel ? "max" : String(ctx)} disabled={!session} onChange={(e) => void setCtx(e.target.value === "max" ? (max ?? ctx) : Number(e.target.value))}>
        {options.map((p) => <option key={p} value={p}>{fmtCtx(p)} ctx</option>)}
        {!options.includes(ctx) && !isMaxSel && <option value={ctx}>{ctx.toLocaleString()} ctx</option>}
        {max && <option value="max">Max supported ({fmtCtx(max)})</option>}
      </Select>
      {custom && null}
      {modelMeta?.tools_supported === false && <span className="flex items-center gap-1 text-xs text-warn" title="This model does not advertise tool support; ZEUS will try JSON tool calls as text."><TriangleAlert size={13} />no native tools</span>}
      {modelMeta?.context_check?.warning && <span className="flex items-center gap-1 text-xs text-warn" title={modelMeta.context_check.warning}><TriangleAlert size={13} />memory (estimate)</span>}
    </div>
  );
}
