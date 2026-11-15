import { useState } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "../../components/ui/button";
import { useApp } from "../../stores/app";

export function PromptInput() {
  const [text, setText] = useState("");
  const { running, sendPrompt, stop, session, project, ollama } = useApp();
  const noModel = !session?.model;
  const disabled = !project || noModel || !ollama?.connected;
  const submit = () => { const t = text.trim(); if (!t || running || disabled) return; sendPrompt(t); setText(""); };
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-3">
      <div className="rounded-2xl border border-border bg-surface p-2 focus-within:border-accent">
        <textarea
          value={text} onChange={(e) => setText(e.target.value)} rows={3} aria-label="Prompt"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
          placeholder={!project ? "Open a project to begin" : noModel ? "Select a model to begin" : !ollama?.connected ? "Ollama is not connected" : `Ask ZEUS to build, modify or explain code…  (Enter to send, Shift+Enter for newline${session?.model === "auto" ? "; /think or /code forces a model" : ""})`}
          disabled={disabled && !running}
          className="w-full resize-none bg-transparent px-2 py-1 outline-none placeholder:text-muted" />
        <div className="flex items-center justify-end">
          {running
            ? <Button variant="danger" size="sm" onClick={stop}><Square size={12} fill="currentColor" /> Stop generation</Button>
            : <Button size="icon" onClick={submit} disabled={disabled || !text.trim()} aria-label="Send"><ArrowUp size={16} /></Button>}
        </div>
      </div>
    </div>
  );
}
