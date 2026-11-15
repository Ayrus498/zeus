import { useRef, useState } from "react";
import { FilePlus, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { suggestFilename } from "../../lib/filename";

/** "Save as file" for a code block the model printed instead of saving. The click is the user's own approval. */
export function SaveCodeButton({ lang }: { lang: string | null }) {
  const { project, session, toast, bump, refreshChanges } = useApp();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [exists, setExists] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  if (!project) return null;
  const code = () => anchor.current?.closest("[data-codebox]")?.querySelector("pre")?.textContent ?? "";

  const start = () => { setName(suggestFilename(lang, code())); setExists(false); setErr(null); setOpen(true); };
  const save = async (overwrite = false) => {
    if (!name.trim()) return;
    setBusy(true); setErr(null);
    try {
      const r = await api.saveFile(project.id, { path: name.trim(), content: code(), session_id: session?.id, overwrite });
      toast(`Saved ${r.path} (${r.bytes.toLocaleString()} bytes)${r.overwritten ? " - replaced the previous version" : ""}.`, "success");
      setOpen(false); bump(); void refreshChanges();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (/already exists/.test(msg)) setExists(true); else setErr(msg);
    } finally { setBusy(false); }
  };

  return (
    <div ref={anchor} className="absolute right-10 top-2 z-10">
      <button type="button" title="Save this code as a file in the project" aria-label="Save as file" onClick={start}
        className="grid h-6 w-6 cursor-pointer place-items-center rounded-md border border-white/20 bg-black/40 text-white/80 opacity-0 backdrop-blur transition-opacity hover:bg-black/60 hover:text-white focus:opacity-100 group-hover/code:opacity-100"><FilePlus size={13} /></button>
      {open && (
        <div className="absolute right-0 top-8 z-20 w-72 rounded-lg border border-border bg-surface p-2.5 text-xs shadow-xl" data-testid="save-popover" role="dialog" aria-label="Save as file">
          <div className="mb-1.5 flex items-center justify-between font-medium">Save in {project.name}/<button aria-label="Close" className="cursor-pointer text-muted hover:text-foreground" onClick={() => setOpen(false)}><X size={12} /></button></div>
          <input autoFocus value={name} onChange={(e) => { setName(e.target.value); setExists(false); }} onKeyDown={(e) => { if (e.key === "Enter") void save(); if (e.key === "Escape") setOpen(false); }}
            aria-label="File name" className="h-8 w-full rounded-md border border-border bg-surface-2 px-2 font-mono outline-none focus:border-accent" />
          {exists && <div className="mt-1.5 text-warn">{name} already exists.</div>}
          {err && <div className="mt-1.5 break-words text-danger">{err}</div>}
          <div className="mt-2 flex justify-end gap-1.5">
            <Button size="sm" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
            {exists ? <Button size="sm" variant="danger" disabled={busy} onClick={() => void save(true)}>Overwrite</Button>
              : <Button size="sm" disabled={busy || !name.trim()} data-testid="save-confirm" onClick={() => void save()}>Save</Button>}
          </div>
        </div>)}
    </div>
  );
}
