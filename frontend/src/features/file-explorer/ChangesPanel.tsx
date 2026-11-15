import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";

export function ChangesPanel() {
  const { changes, setOpenFile } = useApp();
  if (!changes.length) return <div className="p-4 text-xs text-muted">No file changes by the agent yet.</div>;
  return (
    <div className="p-2">
      {changes.map((c) => (
        <button key={c.path} onClick={() => setOpenFile({ path: c.path, mode: "diff" })} className="flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1 text-left text-[13px] hover:bg-surface-2">
          <span className={cn("w-4 font-mono text-[11px]", c.status === "created" ? "text-ok" : c.status === "deleted" ? "text-danger" : "text-warn")}>{c.status[0].toUpperCase()}</span>
          <span className={cn("truncate", c.status === "deleted" && "line-through")}>{c.path}</span>
          <span className="ml-auto text-[10px] text-muted">{c.status}</span>
        </button>))}
    </div>
  );
}
