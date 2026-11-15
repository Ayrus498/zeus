import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { useApp } from "../../stores/app";
import { fmtBytes, fmtNum } from "../../lib/utils";

export function SettingsDialog() {
  const { settingsOpen, setSettingsOpen, settings, saveSettings, ollama, models, modelMeta, session } = useApp();
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (settingsOpen) { setUrl(settings?.ollama_url ?? ""); setMsg(null); } }, [settingsOpen, settings]);
  const save = async () => { try { await saveSettings({ ollama_url: url }); setMsg("Saved."); } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } };
  return (
    <Dialog open={settingsOpen} onOpenChange={setSettingsOpen} title="Settings" description="Ollama connection and model details">
      <div className="space-y-4 text-sm">
        <div>
          <label className="mb-1 block text-xs text-muted" htmlFor="ep">Ollama endpoint</label>
          <div className="flex gap-2">
            <input id="ep" value={url} onChange={(e) => setUrl(e.target.value)} className="h-9 flex-1 rounded-md border border-border bg-surface-2 px-2 font-mono text-xs outline-none focus:border-accent" />
            <Button onClick={save}>Save</Button>
          </div>
          <div className="mt-1 text-xs">{ollama?.connected ? <span className="text-ok">Connected · Ollama {ollama.version}</span> : <span className="text-danger">Not connected{ollama?.error ? ` – ${ollama.error}` : ""}</span>}
            {msg && <span className="ml-2 text-muted">{msg}</span>}</div>
        </div>
        <div className="rounded-md border border-border bg-surface-2/50 p-3 text-xs" data-testid="storage-info">
          <div className="mb-1 font-medium">Where ZEUS keeps your data</div>
          <div className="text-muted">Conversation history (the context sent to Ollama) is saved in each project's <span className="font-mono text-foreground">.zeus/</span> folder, and settings in <span className="font-mono text-foreground">{settings?.home ?? "~"}/.zeus/</span>.
            They are reloaded automatically when ZEUS or the project is opened, even if its internal database is lost. The project's <span className="font-mono">.zeus/</span> is git-ignored by default.</div>
        </div>
        <div>
          <div className="mb-1 text-xs text-muted">Installed models ({models.length})</div>
          <div className="max-h-48 overflow-auto rounded-md border border-border">
            {models.map((m) => <div key={m.name} className="flex justify-between border-b border-border px-3 py-1.5 text-xs last:border-0"><span className="font-mono">{m.name}</span><span className="text-muted">{m.parameter_size} · {m.quantization} · {fmtBytes(m.size)}</span></div>)}
            {!models.length && <div className="px-3 py-2 text-xs text-muted">No models found. Run <span className="font-mono">ollama pull qwen2.5-coder:7b</span>.</div>}
          </div>
        </div>
        {modelMeta && session?.model && (
          <div className="text-xs"><div className="mb-1 text-muted">Active model: {modelMeta.name}</div>
            <div>Native context: {fmtNum(modelMeta.context_length)} · Tools: {modelMeta.tools_supported == null ? "unknown" : modelMeta.tools_supported ? "yes" : "no"}</div>
            {modelMeta.context_check && <div className="mt-1 text-muted">Estimated KV cache at {fmtNum(session.num_ctx)} tokens: {fmtBytes(modelMeta.context_check.kv_bytes)} (estimate)</div>}
            {modelMeta.context_check?.warning && <div className="mt-1 text-warn">{modelMeta.context_check.warning}</div>}</div>)}
      </div>
    </Dialog>
  );
}
