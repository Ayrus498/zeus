import { Files, GitCompare, Gauge, SquareTerminal } from "lucide-react";
import { useEffect } from "react";
import { ProjectsSidebar } from "../features/projects/ProjectsSidebar";
import { Timeline } from "../features/chat/Timeline";
import { PromptInput } from "../features/chat/PromptInput";
import { PermissionModeSelector } from "../features/permissions/PermissionModeSelector";
import { ModelSelector } from "../features/models/ModelSelector";
import { EngineSelector } from "../features/engines/EngineSelector";
import { ResetDialog } from "../features/maintenance/ResetDialog";
import { MemoryDialog } from "../features/memory/MemoryDialog";
import { EngineHelp } from "../features/engines/EngineHelp";
import { LlamaWindow } from "../features/engines/LlamaWindow";
import { SettingsDialog } from "../features/models/SettingsDialog";
import { UsagePanel } from "../features/models/UsagePanel";
import { ModelsWindow } from "../features/models/ModelsWindow";
import { ToastHost } from "../features/models/ToastHost";
import { FileTree } from "../features/file-explorer/FileTree";
import { ChangesPanel } from "../features/file-explorer/ChangesPanel";
import { FileViewer } from "../features/file-explorer/FileViewer";
import { TerminalPanel } from "../features/terminal/TerminalPanel";
import { StatusBar } from "./StatusBar";
import { useApp, type RightTab } from "../stores/app";
import { cn } from "../lib/utils";
import { Resizer, usePaneWidth, useWindowWidth } from "../components/ui/resizer";

const LEFT_MIN = 256;    // original sidebar width (w-64)
const RIGHT_MIN = 440;   // original right pane width
const CENTER_MIN = 420;  // always keep the conversation usable

const TABS: Array<[RightTab, string, typeof Files]> = [["files", "Files", Files], ["changes", "Changes", GitCompare], ["terminal", "Terminal", SquareTerminal], ["usage", "Usage", Gauge]];

export function AppLayout() {
  const { timeline, rightTab, setRightTab, openFile, changes, backendUp, ollama, project } = useApp();
  const { overview, refreshOverview, modelsOpen, setModelsOpen, engines, engine, setLlamaOpen, setEngineHelpOpen } = useApp();
  const eng = engine();
  const llamaInfo = engines?.engines.find((e) => e.id === "llamacpp");
  const activeDownloads = overview?.downloads.filter((d) => d.status === "queued" || d.status === "running").length ?? 0;
  // Poll Ollama state while the Models window is open or a download is running, so progress and errors are always current.
  useEffect(() => {
    if (!backendUp) return;
    void refreshOverview();
    if (!modelsOpen && !activeDownloads) return;
    const t = setInterval(() => void refreshOverview(), 1500);
    return () => clearInterval(t);
  }, [backendUp, modelsOpen, activeDownloads, refreshOverview]);
  const [leftW, setLeftW] = usePaneWidth("left", LEFT_MIN);
  const [rightW, setRightW] = usePaneWidth("right", RIGHT_MIN);
  const winW = useWindowWidth();
  // Each pane may grow until the conversation would drop below CENTER_MIN; never below its original width.
  const leftMax = Math.max(LEFT_MIN, winW - rightW - CENTER_MIN);
  const rightMax = Math.max(RIGHT_MIN, winW - leftW - CENTER_MIN);
  const left = Math.min(Math.max(leftW, LEFT_MIN), leftMax);
  const right = Math.min(Math.max(rightW, RIGHT_MIN), rightMax);
  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <div className="shrink-0" style={{ width: left }}><ProjectsSidebar /></div>
        <Resizer side="left" label="Resize left pane" width={left} min={LEFT_MIN} max={leftMax} onChange={setLeftW} />
        <main className="flex min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2"><div className="flex flex-wrap items-center gap-3"><EngineSelector /><ModelSelector /></div><PermissionModeSelector /></header>
          {!backendUp && <div className="bg-danger/15 px-4 py-2 text-xs text-danger">Cannot reach the Zeus-Agent backend. Retrying…</div>}
          {backendUp && eng === "ollama" && ollama && !ollama.connected && <div className="bg-warn/10 px-4 py-2 text-xs text-warn">Ollama is not reachable at {ollama.endpoint}. Start it with <span className="font-mono">ollama serve</span> or change the endpoint in Settings. <button className="cursor-pointer underline" onClick={() => setModelsOpen(true)}>Open the setup guide</button></div>}
          {backendUp && eng === "llamacpp" && llamaInfo && !llamaInfo.available && (
            <div className="bg-warn/10 px-4 py-2 text-xs text-warn" data-testid="llama-missing-banner">llama-server (llama.cpp) was not found. <button className="cursor-pointer underline" onClick={() => setLlamaOpen(true)}>Set it up</button>
              {" · "}<button className="cursor-pointer underline" onClick={() => setEngineHelpOpen(true)}>Help me choose an engine</button></div>)}
          {backendUp && !project && <div className="px-4 py-3 text-sm text-muted">Open an existing folder or create a new project from the sidebar to get started.</div>}
          <div className="min-h-0 flex-1 overflow-auto"><Timeline items={timeline} /></div>
          <PromptInput />
        </main>
        <Resizer side="right" label="Resize right pane" width={right} min={RIGHT_MIN} max={rightMax} onChange={setRightW} />
        <section className="flex shrink-0 flex-col border-l border-border bg-background" style={{ width: right }}>
          <nav className="flex border-b border-border">
            {TABS.map(([id, label, Icon]) => (
              <button key={id} onClick={() => setRightTab(id)} className={cn("flex flex-1 cursor-pointer items-center justify-center gap-1.5 py-2 text-xs", rightTab === id ? "border-b-2 border-accent text-foreground" : "text-muted hover:text-foreground")}>
                <Icon size={13} />{label}{id === "changes" && changes.length > 0 && <span className="rounded bg-accent px-1 text-[10px] text-accent-foreground">{changes.length}</span>}</button>))}
          </nav>
          <div className={cn("min-h-0 overflow-auto", openFile ? "h-[38%] shrink-0" : "flex-1")}>
            {rightTab === "files" && <FileTree />}{rightTab === "changes" && <ChangesPanel />}{rightTab === "terminal" && <TerminalPanel />}{rightTab === "usage" && <UsagePanel />}
          </div>
          {openFile && <div className="min-h-0 flex-1"><FileViewer /></div>}
        </section>
      </div>
      <StatusBar /><SettingsDialog /><ModelsWindow /><LlamaWindow /><EngineHelp /><MemoryDialog /><ResetDialog /><ToastHost />
    </div>
  );
}
