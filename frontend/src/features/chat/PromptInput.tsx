import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowUp, FileText, Loader2, Paperclip, Play, Square, X } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Select } from "../../components/ui/select";
import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";
import { api } from "../../services/api";

const MAX_DOC = 200_000;                      // Markdown attached directly
const MAX_UPLOAD = 25 * 1024 * 1024;          // documents converted by MarkItDown
// Shown until the backend reports what it can convert (and used if it cannot be reached).
const FALLBACK_EXT = ["md", "markdown", "txt", "pdf", "docx", "pptx", "xlsx", "xls", "csv", "html", "htm", "json", "xml", "epub", "ipynb", "zip"];

export function PromptInput() {
  const [text, setText] = useState("");
  const [rounds, setRounds] = useState("5");
  const [verifier, setVerifier] = useState<"same" | "think">("same");
  const [drag, setDrag] = useState(false);
  const [preview, setPreview] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const [exts, setExts] = useState<string[]>(FALLBACK_EXT);
  const [converting, setConverting] = useState<string | null>(null);
  useEffect(() => { api.convertFormats().then((f) => setExts(f.formats.map((x) => x.ext))).catch(() => { /* keep fallback */ }); }, []);
  const { running, sendPrompt, sendMission, stop, session, project, ollama, attachment, setAttachment, toast } = useApp();
  const noModel = !session?.model;
  const disabled = !project || noModel || !ollama?.connected;
  const isAuto = session?.model === "auto";

  const attach = async (f: File | undefined | null) => {
    if (!f) return;
    const ext = (f.name.split(".").pop() ?? "").toLowerCase();
    setPreview(false);
    if (ext === "md" || ext === "markdown") {                               // Markdown is used as is
      if (f.size > MAX_DOC) { toast(`"${f.name}" is ${Math.round(f.size / 1000)} KB; Markdown documents are limited to ${MAX_DOC / 1000} KB.`, "error"); return; }
      let content = "";
      try { content = await f.text(); } catch { toast(`Could not read "${f.name}".`, "error"); return; }
      if (!content.trim()) { toast(`"${f.name}" is empty.`, "error"); return; }
      setAttachment({ name: f.name, content, size: f.size });
      return;
    }
    if (!exts.includes(ext)) {
      toast(`"${f.name}" cannot be attached: .${ext || "?"} is not supported. ZEUS accepts ${exts.map((e) => "." + e).join(", ")}. Images and audio are not supported.`, "error");
      return;
    }
    if (f.size > MAX_UPLOAD) { toast(`"${f.name}" is ${(f.size / 2 ** 20).toFixed(1)} MB; the limit is ${MAX_UPLOAD / 2 ** 20} MB.`, "error"); return; }
    setConverting(f.name);
    try {
      const r = await api.convertFile(f);                                    // MarkItDown, in an isolated backend process
      setAttachment({ name: r.name, content: r.markdown, size: new Blob([r.markdown]).size, source: r.source, truncated: r.truncated, omitted: r.omitted_chars });
      if (r.truncated) toast(`"${r.source}" is long: only the first ${MAX_DOC / 1000} KB of its Markdown will be used.`, "info");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(/^Could not convert/.test(msg) ? msg : `Could not convert "${f.name}": ${msg}`, "error");
    } finally { setConverting(null); }
  };
  const submit = () => {
    if (running || disabled) return;
    if (attachment) { sendMission({ note: text.trim(), rounds: Number(rounds), verifier }); setText(""); return; }
    const t = text.trim();
    if (!t) return;
    sendPrompt(t); setText("");
  };
  const canSend = !disabled && !converting && (!!attachment || !!text.trim());
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-3">
      <div className={cn("rounded-2xl border bg-surface p-2 focus-within:border-accent", drag ? "border-accent bg-accent/5" : "border-border")}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDrag(true); } }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); void attach(e.dataTransfer.files[0]); }}>
        {converting && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-border bg-surface-2/60 p-2 text-xs" data-testid="converting-chip" role="status">
            <Loader2 size={14} className="animate-spin text-accent" />Converting <span className="font-medium">{converting}</span> to Markdown with MarkItDown…</div>)}
        {attachment && (
          <div className="mb-2 rounded-lg border border-border bg-surface-2/60 p-2 text-xs" data-testid="attachment-chip">
            <div className="flex items-center gap-2">
              <FileText size={14} className="shrink-0 text-accent" />
              <span className="truncate font-medium">{attachment.name}</span><span className="shrink-0 text-muted">{(attachment.size / 1000).toFixed(1)} KB · will run as a mission</span>
              <button className="ml-auto cursor-pointer text-muted hover:text-foreground" onClick={() => setPreview(!preview)}>{preview ? "Hide" : "Preview"}</button>
              <button aria-label="Remove attachment" className="cursor-pointer text-muted hover:text-danger" onClick={() => setAttachment(null)}><X size={13} /></button>
            </div>
            {attachment.source && <div className="mt-1 text-[11px] text-muted" data-testid="converted-note">Converted from <span className="font-mono">{attachment.source}</span> with MarkItDown. Check the preview: tables and layout may be approximated.</div>}
            {attachment.truncated && <div className="mt-1 flex items-center gap-1 text-[11px] text-warn"><AlertTriangle size={11} />Long document: cut at {MAX_DOC / 1000} KB ({attachment.omitted?.toLocaleString()} characters omitted).</div>}
            {preview && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-background p-2 font-mono text-[11px] text-muted">{attachment.content}</pre>}
            <div className="mt-2 flex flex-wrap items-center gap-3 text-muted">
              <label className="flex items-center gap-1.5">Max rounds
                <Select aria-label="Max rounds" value={rounds} onChange={(e) => setRounds(e.target.value)}>
                  {["2", "3", "5", "8", "10"].map((n) => <option key={n} value={n}>{n}</option>)}</Select></label>
              {isAuto && <label className="flex cursor-pointer items-center gap-1.5" title="Use the reasoning model (when one is installed) to review the work. Slower if it has to be loaded.">
                <input type="checkbox" checked={verifier === "think"} onChange={(e) => setVerifier(e.target.checked ? "think" : "same")} />Reasoning model reviews</label>}
              <span className="ml-auto">ZEUS works in rounds and reviews against the criteria until they are all met.</span>
            </div>
          </div>)}
        <textarea
          value={text} onChange={(e) => setText(e.target.value)} rows={attachment ? 2 : 3} aria-label="Prompt"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
          placeholder={!project ? "Open a project to begin" : noModel ? "Select a model to begin" : !ollama?.connected ? "Ollama is not connected"
            : attachment ? "Optional: add instructions for this document…  (Enter to start the mission)"
            : `Ask ZEUS to build, modify or explain code…  (Enter to send, Shift+Enter for newline${isAuto ? "; /think or /code forces a model" : ""}; attach a .md, PDF, Word… file to run it as a mission)`}
          disabled={disabled && !running}
          className="w-full resize-none bg-transparent px-2 py-1 outline-none placeholder:text-muted" />
        <div className="flex items-center justify-between">
          <div>
            <input ref={file} type="file" accept={exts.map((e) => "." + e).join(",")} className="hidden" aria-label="Attach Markdown file" data-testid="attach-input"
              onChange={async (e) => { const input = e.target; await attach(input.files?.[0]); input.value = ""; }} />
            <button type="button" aria-label="Attach a Markdown file" title={`Attach a document (.md, PDF, Word, PowerPoint, Excel, HTML, CSV…): ZEUS converts it to Markdown, reads it as the task and works on it until it is verified`} disabled={disabled || running || !!converting}
              onClick={() => file.current?.click()} className="grid h-8 w-8 cursor-pointer place-items-center rounded-md text-muted hover:bg-surface-2 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"><Paperclip size={16} /></button>
          </div>
          {running
            ? <Button variant="danger" size="sm" onClick={stop}><Square size={12} fill="currentColor" /> Stop</Button>
            : attachment
              ? <Button size="sm" onClick={submit} disabled={!canSend} data-testid="run-mission"><Play size={13} fill="currentColor" /> Run mission</Button>
              : <Button size="icon" onClick={submit} disabled={!canSend} aria-label="Send"><ArrowUp size={16} /></Button>}
        </div>
      </div>
    </div>
  );
}
