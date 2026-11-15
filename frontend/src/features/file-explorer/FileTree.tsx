import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, File as FileIcon, Folder, RefreshCw } from "lucide-react";
import { api } from "../../services/api";
import type { TreeNode } from "../../services/types";
import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";

const BADGE = { created: ["A", "text-ok"], modified: ["M", "text-warn"], deleted: ["D", "text-danger"] } as const;

function Dir({ pid, node, depth, status, version }: { pid: number; node: TreeNode; depth: number; status: Map<string, keyof typeof BADGE>; version: number }) {
  const [open, setOpen] = useState<boolean>(false);
  const [kids, setKids] = useState<TreeNode[] | null>(null);
  const { setOpenFile } = useApp();
  const load = useCallback(() => api.tree(pid, node.path).then(setKids).catch(() => setKids([])), [pid, node.path]);
  useEffect(() => { if (open) void load(); }, [open, version, load]);
  const st = status.get(node.path);
  return (
    <div>
      <button className="flex w-full cursor-pointer items-center gap-1 rounded px-1 py-0.5 text-left text-[13px] hover:bg-surface-2" style={{ paddingLeft: depth * 12 + 4 }}
        onClick={() => (node.is_dir ? setOpen(!open) : setOpenFile({ path: node.path, mode: st === "modified" || st === "created" ? "diff" : "file" }))}>
        {node.is_dir ? (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : <span className="w-[13px]" />}
        {node.is_dir ? <Folder size={13} className="text-accent" /> : <FileIcon size={13} className="text-muted" />}
        <span className={cn("truncate", st === "created" && "text-ok", st === "modified" && "text-warn")}>{node.name}</span>
        {st && <span className={cn("ml-auto pr-1 font-mono text-[10px]", BADGE[st][1])}>{BADGE[st][0]}</span>}
      </button>
      {open && kids?.map((k) => <Dir key={k.path} pid={pid} node={k} depth={depth + 1} status={status} version={version} />)}
    </div>
  );
}

export function FileTree() {
  const { project, treeVersion, changes, bump, refreshChanges } = useApp();
  const [roots, setRoots] = useState<TreeNode[]>([]);
  useEffect(() => { if (project) api.tree(project.id).then(setRoots).catch(() => setRoots([])); }, [project, treeVersion]);
  if (!project) return <div className="p-4 text-xs text-muted">No project open.</div>;
  const status = new Map(changes.map((c) => [c.path, c.status] as const));
  const deleted = changes.filter((c) => c.status === "deleted");
  return (
    <div className="p-2">
      <div className="mb-1 flex items-center justify-between px-1 text-[11px] uppercase tracking-wide text-muted">{project.name}
        <button aria-label="Refresh" className="cursor-pointer rounded p-1 hover:bg-surface-2" onClick={() => { bump(); void refreshChanges(); }}><RefreshCw size={12} /></button></div>
      {roots.map((n) => <Dir key={n.path} pid={project.id} node={n} depth={0} status={status} version={treeVersion} />)}
      {!roots.length && <div className="px-2 py-2 text-xs text-muted">Empty folder.</div>}
      {deleted.map((d) => <button key={d.path} className="block w-full cursor-pointer px-2 py-0.5 text-left text-[13px] text-danger line-through" onClick={() => useApp.getState().setOpenFile({ path: d.path, mode: "diff" })}>{d.path}</button>)}
    </div>
  );
}
