import type { ReactNode } from "react";
import { ExternalLink as Icon } from "lucide-react";
import { api } from "../../services/api";
import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";

/** Opens an Ollama documentation link in the user's default browser (the desktop webview cannot open new windows). */
export function ExternalLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  const toast = useApp((s) => s.toast);
  return (
    <a href={href} target="_blank" rel="noreferrer" title={href}
      onClick={async (e) => {
        e.preventDefault();
        try { await api.openUrl(href); } catch (err) { toast(`Could not open the browser. Open this link manually: ${href}  (${err instanceof Error ? err.message : err})`, "error"); }
      }}
      className={cn("inline-flex cursor-pointer items-center gap-1 text-accent underline-offset-2 hover:underline", className)}>
      {children}<Icon size={11} />
    </a>
  );
}
