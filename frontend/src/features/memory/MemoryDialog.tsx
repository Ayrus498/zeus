import { useEffect, useState } from "react";
import { BookMarked, Save } from "lucide-react";
import { Dialog } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import type { MemoryInfo } from "../../services/types";

/** Project memory editor: the notes every new conversation in this project starts with. */
export function MemoryDialog() {
  const { memoryOpen, setMemoryOpen, project, refreshContext, toast } = useApp();
  const [info, setInfo] = useState<MemoryInfo | null>(null);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!memoryOpen || !project) return;
    setError(null); setNote("");
    api.memory(project.id).then((m) => { setInfo(m); setText(m.text); }).catch((e) => setError(String(e instanceof Error ? e.message : e)));
  }, [memoryOpen, project]);
  const dirty = info !== null && text !== info.text;
  const bytes = new TextEncoder().encode(text).length;
  const max = info?.max_bytes ?? 16000;
  const save = async () => {
    if (!project) return; setBusy(true); setError(null);
    try { const m = await api.putMemory(project.id, text); setInfo(m); setText(m.text); void refreshContext(); toast(m.text.trim() ? "Project memory saved." : "Project memory cleared."); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  };
  const add = async () => {
    if (!project || !note.trim()) return; setBusy(true); setError(null);
    try { const m = await api.addMemoryNote(project.id, note); setInfo(m); setText(m.text); setNote(""); void refreshContext(); }
    catch (e) { setError(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open={memoryOpen} onOpenChange={setMemoryOpen} title="Project memory" description={project ? `Notes every new conversation in ${project.name} starts with` : "Open a project first"} className="w-[min(720px,96vw)]">
      {!project ? <div className="text-xs text-muted">Open a project to edit its memory.</div> : (
        <div className="space-y-3 text-sm" data-testid="memory-dialog">
          <p className="flex items-start gap-2 text-xs text-muted"><BookMarked size={14} className="mt-0.5 shrink-0 text-accent" />
            Short, lasting facts: the stack, conventions, the test command, decisions, your preferences. Zeus-Agent reads this at the start of every turn; it can add a line itself when you ask it to remember something (with your approval). It stays on this computer in <span className="font-mono">{info?.path ?? ".zeus/memory.md"}</span> and is never committed.</p>
          <div className="flex gap-2">
            <input aria-label="Add a note" value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void add(); }} maxLength={400}
              placeholder="Add a note, for example: Tests run with .venv/bin/pytest -q" className="h-9 min-w-0 flex-1 rounded-md border border-border bg-surface-2 px-2 text-xs outline-none focus:border-accent" />
            <Button size="sm" variant="secondary" disabled={busy || !note.trim()} onClick={() => void add()}>Add</Button>
          </div>
          <textarea aria-label="Project memory" value={text} onChange={(e) => setText(e.target.value)} rows={12} spellCheck={false} data-testid="memory-text"
            placeholder={"- The backend is FastAPI with SQLite\n- Run tests with .venv/bin/pytest -q\n- Prefer small, focused commits"}
            className="w-full resize-y rounded-md border border-border bg-surface-2 p-2 font-mono text-xs outline-none focus:border-accent" />
          {error && <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger" role="alert">{error}</div>}
          <div className="flex items-center gap-3">
            <span className={bytes > max ? "text-xs text-danger" : "text-xs text-muted"}>{bytes.toLocaleString()} / {max.toLocaleString()} bytes{info && info.prompt_chars < text.length ? ` · the first ${info.prompt_chars.toLocaleString()} characters are sent to the model` : ""}</span>
            <Button className="ml-auto" variant="ghost" onClick={() => setMemoryOpen(false)}>Close</Button>
            <Button disabled={busy || !dirty || bytes > max} onClick={() => void save()} data-testid="memory-save"><Save size={13} /> Save</Button>
          </div>
        </div>)}
    </Dialog>
  );
}
