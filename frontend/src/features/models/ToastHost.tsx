import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";

/** Page-level notifications. Errors stay until dismissed; others fade after a few seconds. */
export function ToastHost() {
  const { toasts, dismissToast } = useApp();
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed bottom-10 right-4 z-[60] flex w-[min(420px,92vw)] flex-col gap-2" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} role={t.level === "error" ? "alert" : "status"}
          className={cn("pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-xs shadow-xl",
            t.level === "error" ? "border-danger/50 bg-[#2a1716] text-[#f2b8b3]" : t.level === "success" ? "border-ok/40 bg-surface text-foreground" : "border-border bg-surface text-foreground")}>
          {t.level === "error" ? <AlertTriangle size={14} className="mt-0.5 shrink-0 text-danger" /> : t.level === "success" ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-ok" /> : <Info size={14} className="mt-0.5 shrink-0 text-accent" />}
          <div className="min-w-0 flex-1 select-text break-words">{t.text}</div>
          <button aria-label="Dismiss" className="cursor-pointer text-muted hover:text-foreground" onClick={() => dismissToast(t.id)}><X size={13} /></button>
        </div>))}
    </div>
  );
}
