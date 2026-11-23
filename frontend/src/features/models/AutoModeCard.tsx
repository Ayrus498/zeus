import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Download, Sparkles } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Select } from "../../components/ui/select";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { cn, fmtBytes } from "../../lib/utils";
import type { AutoPlan, AutoPreview, AutoRole } from "../../services/types";

const ROLE_LABEL = { code: "Coding & agent work", think: "Thinking & analysis" } as const;
const ROLE_HINT = {
  code: "Prompts that create, change, fix or run things. Needs tool calling.",
  think: "Questions about why/how, comparisons, design and trade-offs.",
} as const;

function RoleRow({ role, info, pin, candidates, onPin }: {
  role: "code" | "think"; info: AutoRole | null; pin: string | null; candidates: string[]; onPin: (v: string) => void;
}) {
  return (
    <div className="rounded-md border border-border bg-surface-2/40 p-2.5" data-testid={`auto-role-${role}`}>
      <div className="flex items-baseline justify-between gap-2"><span className="text-xs font-medium">{ROLE_LABEL[role]}</span><span className="truncate text-[10px] text-muted">{ROLE_HINT[role]}</span></div>
      <div className="mt-1.5 flex items-center gap-2">
        <div className="min-w-0 flex-1">
          {info ? <>
            <div className="flex flex-wrap items-center gap-1.5"><span className="truncate font-mono text-sm">{info.name}</span>
              {info.loaded && <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] text-accent-foreground">loaded</span>}
              {!info.tools && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] text-warn" title="Answers questions but cannot read or change files">no tools</span>}</div>
            <div className="truncate text-[10px] text-muted">{fmtBytes(info.size)} · {info.why.join(" · ")}</div></>
            : <span className="text-xs text-muted">No suitable model installed</span>}
        </div>
        <Select aria-label={`Pin ${role} model`} value={pin ?? ""} onChange={(e) => onPin(e.target.value)} className="max-w-[170px]">
          <option value="">Automatic</option>
          {candidates.map((c) => <option key={c} value={c}>{c}</option>)}
        </Select>
      </div>
    </div>
  );
}

export function AutoModeCard() {
  const { overview, session, setModel, project, pullModel, toast } = useApp();
  const [plan, setPlan] = useState<AutoPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<AutoPreview | null>(null);
  const key = overview?.installed.map((m) => m.name).join("|") ?? "";
  const load = useCallback(() => api.autoPlan().then((p) => { setPlan(p); setErr(null); }).catch((e) => setErr(String(e instanceof Error ? e.message : e))), []);
  useEffect(() => { if (overview?.connected) void load(); }, [key, overview?.connected, load]);

  if (!overview?.connected || !overview.installed.length) return null;
  const isAuto = session?.model === "auto";
  const pin = async (role: "code" | "think", v: string) => {
    try { await api.autoPins(v ? { [role]: v } : { clear: [role] }); await load(); setPreview(null); }
    catch (e) { toast(`Could not save the choice: ${e instanceof Error ? e.message : e}`, "error"); }
  };
  const tryIt = async () => {
    if (!text.trim()) return;
    try { setPreview(await api.autoPreview(text, session?.last_model)); } catch (e) { toast(`Preview failed: ${e instanceof Error ? e.message : e}`, "error"); }
  };
  return (
    <section className="rounded-lg border border-accent/40 bg-accent/5 p-3" data-testid="auto-card">
      <div className="flex items-center gap-2"><Sparkles size={14} className="text-accent" /><span className="text-sm font-medium">Auto mode</span>
        {isAuto ? <span className="rounded bg-ok/15 px-1.5 py-0.5 text-[10px] text-ok">on for this session</span>
          : <Button size="sm" className="ml-auto" disabled={!project} title={project ? "Let Zeus-Agent choose the model for each prompt" : "Open a project first"} onClick={() => void setModel("auto")}>Use Auto</Button>}</div>
      <p className="mt-1 text-[11px] text-muted">Zeus-Agent picks a coding or a reasoning model for each prompt from what is installed, using what it knows about each model, what fits your GPU/RAM and what is already loaded.
        The choice is made once per prompt and shown in the chat. Start a prompt with <span className="font-mono">/think</span> or <span className="font-mono">/code</span> to force one.</p>
      {err && <div className="mt-2 text-xs text-danger">Could not build the plan: {err}</div>}
      {plan && (
        <div className="mt-2 space-y-2">
          <RoleRow role="code" info={plan.roles.code} pin={plan.pins.code} candidates={plan.candidates} onPin={(v) => void pin("code", v)} />
          <RoleRow role="think" info={plan.roles.think} pin={plan.pins.think} candidates={plan.candidates} onPin={(v) => void pin("think", v)} />
          {plan.warnings.map((w) => <div key={w} className="flex items-start gap-1.5 text-[11px] text-warn"><AlertTriangle size={12} className="mt-0.5 shrink-0" />{w}</div>)}
          {plan.suggestions.map((s) => (
            <div key={s.tag} className="flex items-center gap-2 rounded-md border border-border bg-surface p-2 text-xs">
              <div className="min-w-0 flex-1"><div className="font-mono">{s.tag}{s.size_gb ? <span className="ml-1 text-muted">~{s.size_gb} GB</span> : null}</div><div className="text-[10px] text-muted">{s.why}</div></div>
              <Button size="sm" onClick={() => void pullModel(s.tag)}><Download size={11} /> Download</Button></div>))}
          <div className="flex gap-1.5">
            <input value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && void tryIt()} placeholder="Try a prompt to see which model Auto would pick…" aria-label="Try a prompt"
              className="h-8 min-w-0 flex-1 rounded-md border border-border bg-surface-2 px-2 text-xs outline-none focus:border-accent" />
            <Button size="sm" variant="secondary" onClick={() => void tryIt()}>Preview</Button></div>
          {preview && (
            <div className={cn("rounded-md border border-border bg-surface p-2 text-[11px]")} data-testid="auto-preview">
              {preview.choice ? <><span className="font-mono text-foreground">{preview.choice.model}</span> <span className="text-muted">· {preview.choice.role === "think" ? "reasoning" : preview.choice.role === "code" ? "coding" : "keeps current"} · {preview.choice.tools ? "tools on" : "no tools"}</span>
                <div className="mt-0.5 text-muted">{preview.choice.reason}</div></> : <span className="text-danger">No chat-capable model available.</span>}
            </div>)}
        </div>)}
    </section>
  );
}
