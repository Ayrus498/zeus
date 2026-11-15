import { ConfirmDialog, type ConfirmRequest } from "../../components/ui/confirm";
import { useState } from "react";
import { Check, FolderOpen, FolderPlus, Pencil, Plus, Settings, Trash2, Zap, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Dialog } from "../../components/ui/dialog";
import { useApp } from "../../stores/app";
import { api } from "../../services/api";
import { cn } from "../../lib/utils";

function NewProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { settings, createProject } = useApp();
  const [parent, setParent] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const loc = parent || settings?.home || "";
  const pick = async () => { try { const r = await api.pickFolder(); if (r.path) setParent(r.path); } catch (e) { setErr(String(e instanceof Error ? e.message : e)); } };
  const create = async () => { try { await createProject(loc, name.trim()); setName(""); setErr(null); onClose(); } catch (e) { setErr(String(e instanceof Error ? e.message : e)); } };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="New project" description="Creates a new empty folder and opens it as a project.">
      <div className="space-y-3 text-sm">
        <div><label className="mb-1 block text-xs text-muted" htmlFor="pn">Project name</label>
          <input id="pn" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="zeus-notes" className="h-9 w-full rounded-md border border-border bg-surface-2 px-2 outline-none focus:border-accent" /></div>
        <div><div className="mb-1 text-xs text-muted">Location</div>
          <div className="flex gap-2"><div className="flex h-9 flex-1 items-center truncate rounded-md border border-border bg-surface-2 px-2 font-mono text-xs">{loc}</div><Button variant="outline" onClick={pick}>Choose…</Button></div>
          <div className="mt-1 font-mono text-xs text-muted">{loc}/{name || "…"}</div></div>
        {err && <div className="text-xs text-danger">{err}</div>}
        <div className="flex justify-end"><Button onClick={create} disabled={!name.trim()}>Create project</Button></div>
      </div>
    </Dialog>
  );
}

export function ProjectsSidebar() {
  const { projects, project, sessions, session, selectProject, addProjectFromPicker, removeProject, newSession, selectSession, renameSession, deleteSession, setSettingsOpen, ollama } = useApp();
  const [creating, setCreating] = useState(false);
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex items-center gap-2 px-4 pb-2 pt-3"><Zap className="text-accent" size={20} fill="currentColor" />
        <div><div className="text-sm font-semibold tracking-wide">ZEUS</div>
          <div className="flex items-center gap-1 text-[11px] text-muted"><span className={cn("h-1.5 w-1.5 rounded-full", ollama?.connected ? "bg-ok" : "bg-danger")} />{ollama?.connected ? "Ollama connected" : "Ollama offline"}</div></div></div>
      <div className="px-3 pb-2"><Button className="w-full" onClick={() => void newSession()} disabled={!project}><Plus size={15} /> New session</Button></div>

      <div className="flex items-center justify-between px-4 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted">Projects
        <span className="flex gap-0.5"><button title="Open existing folder" aria-label="Open folder" className="cursor-pointer rounded p-1 hover:bg-surface-2" onClick={() => void addProjectFromPicker()}><FolderOpen size={14} /></button>
          <button title="New project folder" aria-label="New project" className="cursor-pointer rounded p-1 hover:bg-surface-2" onClick={() => setCreating(true)}><FolderPlus size={14} /></button></span></div>
      <div className="max-h-48 overflow-auto px-2 py-1">
        {projects.map((p) => (
          <div key={p.id} className={cn("group flex items-center gap-2 rounded-md px-2 py-1.5", project?.id === p.id ? "bg-surface-2" : "hover:bg-surface-2/60", p.exists === false && "opacity-50")}>
            <button className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left" onClick={() => void selectProject(p.id)} title={p.path}>
              <span>📁</span><span className="min-w-0"><span className="block truncate text-sm">{p.name}</span>{project?.id === p.id && <span className="block truncate font-mono text-[10px] text-muted">{p.path}</span>}</span></button>
            <button title="Remove from ZEUS (files are kept)" aria-label={`Remove ${p.name}`} className="hidden cursor-pointer rounded p-1 text-muted hover:text-danger group-hover:block"
              onClick={() => setConfirmReq({ title: `Remove "${p.name}" from ZEUS?`, description: "Your files on disk will NOT be deleted. Its conversations will be forgotten.", confirmLabel: "Remove", onConfirm: () => removeProject(p.id) })}><X size={13} /></button>
          </div>))}
        {!projects.length && <div className="px-2 py-3 text-xs text-muted">No projects yet. Open an existing folder or create a new one.</div>}
      </div>

      <div className="px-4 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted">Recent sessions</div>
      <div className="min-h-0 flex-1 overflow-auto px-2 py-1">
        {sessions.map((s) => (
          <div key={s.id} className={cn("group flex items-center gap-1 rounded-md px-2 py-1.5", session?.id === s.id ? "bg-surface-2" : "hover:bg-surface-2/60")}>
            {editing === s.id ? (
              <><input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { void renameSession(s.id, title); setEditing(null); } if (e.key === "Escape") setEditing(null); }} className="min-w-0 flex-1 rounded border border-border bg-background px-1 text-sm outline-none" />
                <button aria-label="Save name" className="cursor-pointer" onClick={() => { void renameSession(s.id, title); setEditing(null); }}><Check size={13} /></button></>
            ) : (
              <><button className="min-w-0 flex-1 cursor-pointer truncate text-left text-sm" onClick={() => void selectSession(s.id)} title={s.model ?? ""}>{s.title}</button>
                <button aria-label="Rename" className="hidden cursor-pointer text-muted hover:text-foreground group-hover:block" onClick={() => { setEditing(s.id); setTitle(s.title); }}><Pencil size={12} /></button>
                <button aria-label="Delete session" className="hidden cursor-pointer text-muted hover:text-danger group-hover:block" onClick={() => setConfirmReq({ title: "Delete this conversation?", description: `"${s.title}" and its messages will be permanently deleted. Files in your project are not affected.`, confirmLabel: "Delete", onConfirm: () => deleteSession(s.id) })}><Trash2 size={12} /></button></>)}
          </div>))}
        {project && !sessions.length && <div className="px-2 py-2 text-xs text-muted">No conversations yet.</div>}
      </div>
      <div className="border-t border-border p-2"><Button variant="ghost" className="w-full justify-start" onClick={() => setSettingsOpen(true)}><Settings size={15} /> Settings</Button></div>
      <NewProjectDialog open={creating} onClose={() => setCreating(false)} />
      <ConfirmDialog request={confirmReq} onClose={() => setConfirmReq(null)} />
    </aside>
  );
}
