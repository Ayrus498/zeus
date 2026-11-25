import { useEffect, useState } from "react";
import { Cpu, TriangleAlert } from "lucide-react";
import { Select } from "../../components/ui/select";
import { useApp } from "../../stores/app";
import { fmtBytes, fmtCtx } from "../../lib/utils";

const PRESETS = [8192, 16384, 32768, 65536];

export function ModelSelector() {
  const { models, session, modelMeta, setModel, setCtx, ollama, activeModel, engines, engine, setLlamaOpen } = useApp();
  const eng = engine();
  const pending = useApp((s) => s.pendingModel);
  const chosen = session?.model ?? (pending && pending.engine === engine() ? pending.name : "");
  const available = engines ? (engines.engines.find((e) => e.id === eng)?.available ?? false) : eng === "ollama" ? !!ollama?.connected : false;
  const isAuto = session?.model === "auto" && eng === "ollama";
  const missing = !!session?.model && session.model !== "auto" && models.length > 0 && !models.some((m) => m.name === session.model);
  const max = modelMeta?.context_length ?? null;
  const [custom, setCustom] = useState(false);
  useEffect(() => setCustom(false), [session?.id]);
  const ctx = session?.num_ctx ?? 16384;
  const options = PRESETS.filter((p) => !max || p <= max);
  const isMaxSel = !!max && ctx === max;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Cpu size={14} className="text-accent" />
      <Select aria-label="Model" value={chosen} disabled={!available} onChange={(e) => void setModel(e.target.value)}>
        {!chosen && <option value="">{eng === "llamacpp" && models.length === 0 ? "No GGUF models yet" : "Select model…"}</option>}
        {eng === "ollama" && <option value="auto">✨ Auto — picks a model per prompt</option>}
        {missing && <option value={session!.model!}>{session!.model} ({eng === "llamacpp" ? "file not found" : "not installed"})</option>}
        {models.filter((m) => !m.embedding).map((m) => <option key={m.name} value={m.name}>{m.name} · {fmtBytes(m.size)}{eng === "llamacpp" && m.quantization ? ` · ${m.quantization}` : ""}</option>)}
      </Select>
      {eng === "llamacpp" && available && models.length === 0 && (
        <button type="button" onClick={() => setLlamaOpen(true)} data-testid="add-gguf" className="cursor-pointer text-xs text-accent hover:underline">Add or download a model</button>)}
      {isAuto && (
        <span className="flex max-w-[260px] items-center gap-1 truncate rounded-md bg-accent/15 px-2 py-1 text-[11px] text-accent" data-testid="auto-active"
          title="Auto mode: Zeus-Agent picks a coding or a reasoning model for each prompt from your installed models. Type /think or /code at the start of a prompt to force one. See Settings → Models for details.">
          {activeModel ? <>now: <span className="truncate font-mono">{activeModel}</span></> : "picks per prompt"}</span>)}
      <Select aria-label="Context window" value={isMaxSel ? "max" : String(ctx)} disabled={!session} onChange={(e) => void setCtx(e.target.value === "max" ? (max ?? ctx) : Number(e.target.value))}>
        {options.map((p) => <option key={p} value={p}>{fmtCtx(p)} ctx</option>)}
        {!options.includes(ctx) && !isMaxSel && <option value={ctx}>{ctx.toLocaleString()} ctx</option>}
        {max && <option value="max">Max supported ({fmtCtx(max)})</option>}
      </Select>
      {custom && null}
      {modelMeta?.tools_supported === false && <span className="flex items-center gap-1 text-xs text-warn" title="This model does not advertise tool support; Zeus-Agent will try JSON tool calls as text."><TriangleAlert size={13} />no native tools</span>}
      {modelMeta?.context_check?.warning && <span className="flex items-center gap-1 text-xs text-warn" title={modelMeta.context_check.warning}><TriangleAlert size={13} />memory (estimate)</span>}
    </div>
  );
}
