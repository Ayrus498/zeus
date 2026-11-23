import { useEffect, useState } from "react";
import { Boxes } from "lucide-react";
import { Dialog } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { ExternalLink } from "../../components/ui/external-link";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { fmtBytes, fmtNum } from "../../lib/utils";
import type { AboutInfo, SettingsView } from "../../services/types";

const TITLES: Record<SettingsView, { title: string; description: string }> = {
  endpoint: { title: "Ollama endpoint", description: "Where Zeus-Agent reaches Ollama" },
  model: { title: "Current model", description: "Details of the model used by this session" },
  installed: { title: "Installed models", description: "Models available on this machine" },
  about: { title: "About Zeus-Agent", description: "Version, platform and device information" },
};

function Row({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex gap-3 border-b border-border/50 py-1.5 text-xs last:border-b-0">
      <div className="w-28 shrink-0 text-muted">{k}</div>
      <div className={mono ? "min-w-0 flex-1 break-words font-mono" : "min-w-0 flex-1 break-words"}>{v ?? <span className="text-muted">unavailable</span>}</div>
    </div>
  );
}
const Card = ({ title, children, testid }: { title: string; children: React.ReactNode; testid?: string }) => (
  <section data-testid={testid}><div className="mb-1 text-xs font-medium text-muted">{title}</div>
    <div className="rounded-md border border-border bg-surface-2/40 px-3">{children}</div></section>
);

function Endpoint() {
  const { settingsOpen, settings, saveSettings, ollama } = useApp();
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (settingsOpen) { setUrl(settings?.ollama_url ?? ""); setMsg(null); } }, [settingsOpen, settings]);
  const save = async () => { try { await saveSettings({ ollama_url: url }); setMsg("Saved."); } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); } };
  return (
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
        <div className="mb-1 font-medium">Where Zeus-Agent keeps your data</div>
        <div className="text-muted">Conversation history (the context sent to Ollama) is saved in each project's <span className="font-mono text-foreground">.zeus/</span> folder, and settings in <span className="font-mono text-foreground">{settings?.home ?? "~"}/.zeus/</span>.
          They are reloaded automatically when Zeus-Agent or the project is opened, even if its internal database is lost. The project's <span className="font-mono">.zeus/</span> is git-ignored by default.</div>
      </div>
    </div>
  );
}

function CurrentModel() {
  const { session, modelMeta, models, overview } = useApp();
  const name = session?.model;
  if (!name) return <div className="text-sm text-muted" data-testid="current-model">No session is open. Open a project and a session to see its model.</div>;
  if (name === "auto") return <div className="text-sm" data-testid="current-model">This session uses <b>Auto</b> mode: Zeus-Agent picks a coding or thinking model for each prompt. Open <b>Models</b> to see what Auto would use right now.</div>;
  const info = models.find((m) => m.name === name);
  const loaded = overview?.loaded.find((l) => l.name === name);
  return (
    <div className="space-y-3" data-testid="current-model">
      <Card title={name}>
        <Row k="Status" v={loaded ? <span className="text-ok">Loaded in memory · {loaded.processor}</span> : "Not loaded (loads on first use)"} />
        <Row k="Size on disk" v={info ? fmtBytes(info.size) : null} />
        <Row k="Parameters" v={info?.parameter_size} />
        <Row k="Quantization" v={info?.quantization} />
        <Row k="Native context" v={modelMeta?.context_length ? `${fmtNum(modelMeta.context_length)} tokens` : null} />
        <Row k="Session context" v={session?.num_ctx ? `${fmtNum(session.num_ctx)} tokens` : null} />
        <Row k="Tool calling" v={modelMeta ? (modelMeta.tools_supported == null ? "unknown" : modelMeta.tools_supported ? "yes (native)" : "no (text-format fallback)") : null} />
        <Row k="Capabilities" v={modelMeta?.capabilities?.length ? modelMeta.capabilities.join(", ") : null} />
      </Card>
      {modelMeta?.context_check && (
        <div className="text-xs text-muted">Estimated KV cache at {fmtNum(session?.num_ctx ?? 0)} tokens: {fmtBytes(modelMeta.context_check.kv_bytes)} (estimate)
          {modelMeta.context_check.warning && <div className="mt-1 text-warn">{modelMeta.context_check.warning}</div>}</div>)}
    </div>
  );
}

function InstalledList() {
  const { models, setModelsOpen } = useApp();
  const total = models.reduce((a, m) => a + (m.size || 0), 0);
  return (
    <div className="space-y-3 text-sm" data-testid="installed-list">
      <div className="flex items-center justify-between text-xs text-muted"><span>{models.length} installed · {fmtBytes(total)} on disk</span>
        <Button size="sm" variant="secondary" onClick={() => setModelsOpen(true)}><Boxes size={12} /> Manage in Models</Button></div>
      <div className="max-h-72 overflow-auto rounded-md border border-border">
        {models.map((m) => <div key={m.name} className="flex justify-between gap-3 border-b border-border px-3 py-1.5 text-xs last:border-0"><span className="truncate font-mono">{m.name}</span><span className="shrink-0 text-muted">{m.parameter_size} · {m.quantization} · {fmtBytes(m.size)}</span></div>)}
        {!models.length && <div className="px-3 py-2 text-xs text-muted">No models found. Open <b>Models</b> to download one.</div>}
      </div>
    </div>
  );
}

function About() {
  const [a, setA] = useState<AboutInfo | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.about().then(setA).catch((e) => setErr(e instanceof Error ? e.message : String(e))); }, []);
  if (err) return <div className="text-sm text-danger">Could not load: {err}</div>;
  if (!a) return <div className="text-sm text-muted">Reading device information…</div>;
  const g = a.gpus;
  return (
    <div className="space-y-3" data-testid="about">
      <div><div className="text-base font-semibold">{a.name} <span className="text-xs font-normal text-muted">v{a.version}</span></div><div className="text-xs text-muted">{a.tagline}</div></div>
      <Card title="Application">
        <Row k="Version" v={a.version} />
        <Row k="Founder" v={a.founder} />
        <Row k="GitHub" v={<ExternalLink href={a.github}>{a.github.replace("https://", "")}</ExternalLink>} />
        <Row k="Website" v={<ExternalLink href={a.website}>{a.website.replace("https://", "")}</ExternalLink>} />
        <Row k="License" v={a.license} />
      </Card>
      <Card title="Platform" testid="about-platform">
        <Row k="Operating system" v={a.platform.os} />
        <Row k="Kernel" v={`${a.platform.kernel} (${a.platform.arch})`} />
        <Row k="Ollama" v={a.platform.ollama ? `v${a.platform.ollama}` : "not connected"} />
        <Row k="Backend runtime" v={`Python ${a.platform.python}`} />
      </Card>
      <Card title="Device" testid="about-device">
        <Row k="CPU" v={a.cpu.name} />
        <Row k="Cores / threads" v={a.cpu.cores || a.cpu.threads ? `${a.cpu.cores ?? "?"} cores · ${a.cpu.threads ?? "?"} threads` : null} />
        {g.length ? g.map((x, i) => (
          <div key={i}>
            <Row k={g.length > 1 ? `GPU ${i + 1}` : "GPU"} v={x.name} />
            <Row k="VRAM" v={x.vram_total ? `${fmtBytes(x.vram_total)} total${x.vram_used != null ? ` · ${fmtBytes(x.vram_used)} in use` : ""}` : null} />
          </div>)) : <Row k="GPU" v={null} />}
      </Card>
      <Card title="Memory" testid="about-memory">
        <Row k="RAM" v={a.memory.total ? fmtBytes(a.memory.total) : null} />
        <Row k="In use" v={a.memory.used != null ? fmtBytes(a.memory.used) : null} />
        <Row k="Available" v={a.memory.available != null ? fmtBytes(a.memory.available) : null} />
        <Row k="Swap" v={a.memory.swap_total != null ? `${fmtBytes(a.memory.swap_total)} total · ${fmtBytes(a.memory.swap_used ?? 0)} used` : null} />
      </Card>
    </div>
  );
}

export function SettingsDialog() {
  const { settingsOpen, setSettingsOpen, settingsView } = useApp();
  const t = TITLES[settingsView];
  return (
    <Dialog open={settingsOpen} onOpenChange={setSettingsOpen} title={t.title} description={t.description}>
      {settingsView === "endpoint" && <Endpoint />}
      {settingsView === "model" && <CurrentModel />}
      {settingsView === "installed" && <InstalledList />}
      {settingsView === "about" && settingsOpen && <About />}
    </Dialog>
  );
}
