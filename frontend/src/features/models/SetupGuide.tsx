import { RefreshCw, Settings as Cog } from "lucide-react";
import { Button } from "../../components/ui/button";
import { CodeBox } from "../../components/ui/copy";
import { ExternalLink } from "../../components/ui/external-link";
import { useApp } from "../../stores/app";

const Cmd = ({ children }: { children: string }) => (
  <CodeBox text={children}><pre className="overflow-auto rounded-md border border-border bg-[#0d1117] px-3 py-2 pr-10 font-mono text-[12px]">{children}</pre></CodeBox>
);

/** Shown when Ollama is not reachable (or has no models yet). */
export function SetupGuide({ connected, error, endpoint, hasModels }: { connected: boolean; error?: string; endpoint: string; hasModels: boolean }) {
  const { refreshOverview, refreshOllama, openSettings } = useApp();
  return (
    <div className="rounded-lg border border-warn/40 bg-warn/5 p-3 text-xs" data-testid="setup-guide">
      <div className="mb-1 text-sm font-medium text-warn">{connected ? "No models installed yet" : "Ollama was not detected"}</div>
      <p className="mb-2 text-muted">
        {connected ? "Ollama is running, but nothing is installed. Pick a model from the guide below and press Download, or use step 3."
          : <>ZEUS could not reach Ollama at <span className="font-mono">{endpoint}</span>.{error ? <> <span className="font-mono text-danger/90">({error})</span></> : null} Follow these steps, then press <b>Check again</b>.</>}
      </p>
      {!connected && (
        <ol className="space-y-2.5">
          <li><div className="mb-1 font-medium">1. Install Ollama</div>
            <Cmd>curl -fsSL https://ollama.com/install.sh | sh</Cmd>
            <div className="mt-1 flex flex-wrap gap-x-3 text-muted">
              <ExternalLink href="https://ollama.com/download">Download page (Linux / macOS / Windows)</ExternalLink>
              <ExternalLink href="https://github.com/ollama/ollama/blob/main/docs/linux.md">Linux install guide</ExternalLink></div></li>
          <li><div className="mb-1 font-medium">2. Start the Ollama server</div>
            <Cmd>{"ollama serve"}</Cmd>
            <div className="mt-1 text-muted">The Linux installer also sets up a service: <span className="font-mono">sudo systemctl enable --now ollama</span></div></li>
          <li><div className="mb-1 font-medium">3. Pull your first model</div>
            <Cmd>{"ollama pull qwen2.5-coder:7b"}</Cmd>
            <div className="mt-1 text-muted">Or skip this and use the Download buttons next to each model in this window.</div></li>
          <li><div className="mb-1 font-medium">Using Ollama on another machine or port?</div>
            <div className="text-muted">Set the endpoint in Settings (default <span className="font-mono">http://localhost:11434</span>).</div></li>
        </ol>)}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => { void refreshOverview(); void refreshOllama(); }}><RefreshCw size={12} /> Check again</Button>
        <Button size="sm" variant="secondary" onClick={() => openSettings("endpoint")}><Cog size={12} /> Ollama endpoint</Button>
        <span className="ml-auto flex gap-3 text-muted">
          <ExternalLink href="https://docs.ollama.com">Docs</ExternalLink>
          <ExternalLink href="https://ollama.com/library">Model library</ExternalLink></span>
      </div>
      {!hasModels && connected && <div className="mt-2 text-muted">Tip: for ZEUS pick a model marked <b>Best for ZEUS</b>.</div>}
    </div>
  );
}
