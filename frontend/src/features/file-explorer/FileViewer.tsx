import { useEffect, useState } from "react";
import Editor, { DiffEditor } from "@monaco-editor/react";
import { X } from "lucide-react";
import "../../lib/monaco";
import { langFor } from "../../lib/monaco";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { CopyButton } from "../../components/ui/copy";

export function FileViewer() {
  const { project, openFile, setOpenFile, treeVersion } = useApp();
  const [data, setData] = useState<{ original: string; modified: string; note?: string } | null>(null);
  const [mode, setMode] = useState<"file" | "diff">("file");
  useEffect(() => {
    if (!project || !openFile) return;
    setMode(openFile.mode); setData(null);
    (async () => {
      try {
        if (openFile.mode === "diff") {
          const d = await api.changeDiff(project.id, openFile.path);
          setData({ original: d.before, modified: d.after, note: d.status });
        } else setData({ original: "", modified: (await api.file(project.id, openFile.path)).content });
      } catch (e) {
        if (openFile.mode === "diff") { try { setMode("file"); setData({ original: "", modified: (await api.file(project.id, openFile.path)).content }); return; } catch { /* fallthrough */ } }
        setData({ original: "", modified: "", note: String(e instanceof Error ? e.message : e) });
      }
    })();
  }, [project, openFile, treeVersion]);
  if (!openFile) return null;
  const hasDiff = !!data && mode === "diff";
  const opts = { readOnly: true, minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false, automaticLayout: true, renderSideBySide: false };
  return (
    <div className="flex h-full min-h-0 flex-col border-t border-border">
      <div className="flex items-center gap-2 bg-surface px-3 py-1.5 text-xs">
        <span className="truncate font-mono">{openFile.path}</span>
        {data?.note && <span className="rounded bg-surface-2 px-1.5 text-muted">{data.note}</span>}
        <div className="ml-auto flex items-center gap-1">
          <CopyButton getText={() => data?.modified ?? ""} className="mr-1" />
          <button className={`cursor-pointer rounded px-2 py-0.5 ${mode === "file" ? "bg-surface-2" : "text-muted"}`} onClick={() => setOpenFile({ path: openFile.path, mode: "file" })}>File</button>
          <button className={`cursor-pointer rounded px-2 py-0.5 ${mode === "diff" ? "bg-surface-2" : "text-muted"}`} onClick={() => setOpenFile({ path: openFile.path, mode: "diff" })}>Diff</button>
          <button aria-label="Close file" className="cursor-pointer rounded p-1 hover:bg-surface-2" onClick={() => setOpenFile(null)}><X size={13} /></button></div>
      </div>
      <div className="min-h-0 flex-1">
        {!data ? <div className="p-3 text-xs text-muted">Loading…</div> : hasDiff
          ? <DiffEditor theme="vs-dark" original={data.original} modified={data.modified} language={langFor(openFile.path)} options={opts} />
          : <Editor theme="vs-dark" value={data.modified} language={langFor(openFile.path)} options={opts} />}
      </div>
    </div>
  );
}
