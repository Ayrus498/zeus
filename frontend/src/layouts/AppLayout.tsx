import { Files, GitCompare, Gauge, SquareTerminal } from "lucide-react";
import { ProjectsSidebar } from "../features/projects/ProjectsSidebar";
import { Timeline } from "../features/chat/Timeline";
import { PromptInput } from "../features/chat/PromptInput";
import { PermissionModeSelector } from "../features/permissions/PermissionModeSelector";
import { ModelSelector } from "../features/models/ModelSelector";
import { SettingsDialog } from "../features/models/SettingsDialog";
import { UsagePanel } from "../features/models/UsagePanel";
import { FileTree } from "../features/file-explorer/FileTree";
import { ChangesPanel } from "../features/file-explorer/ChangesPanel";
import { FileViewer } from "../features/file-explorer/FileViewer";
import { TerminalPanel } from "../features/terminal/TerminalPanel";
import { StatusBar } from "./StatusBar";
import { useApp, type RightTab } from "../stores/app";
import { cn } from "../lib/utils";

const TABS: Array<[RightTab, string, typeof Files]> = [["files", "Files", Files], ["changes", "Changes", GitCompare], ["terminal", "Terminal", SquareTerminal], ["usage", "Usage", Gauge]];

export function AppLayout() {
  const { timeline, rightTab, setRightTab, openFile, changes, backendUp, ollama, project } = useApp();
  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <ProjectsSidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2"><ModelSelector /><PermissionModeSelector /></header>
          {!backendUp && <div className="bg-danger/15 px-4 py-2 text-xs text-danger">Cannot reach the ZEUS backend. Retrying…</div>}
          {backendUp && ollama && !ollama.connected && <div className="bg-warn/10 px-4 py-2 text-xs text-warn">Ollama is not reachable at {ollama.endpoint}. Start it with <span className="font-mono">ollama serve</span> or change the endpoint in Settings.</div>}
          {backendUp && !project && <div className="px-4 py-3 text-sm text-muted">Open an existing folder or create a new project from the sidebar to get started.</div>}
          <div className="min-h-0 flex-1 overflow-auto"><Timeline items={timeline} /></div>
          <PromptInput />
        </main>
        <section className="flex w-[440px] shrink-0 flex-col border-l border-border bg-background">
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
      <StatusBar /><SettingsDialog />
    </div>
  );
}
