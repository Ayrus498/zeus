import { useEffect, useRef, useState } from "react";
import { Boxes, Info, Link2, Settings, Sparkles, Package } from "lucide-react";
import { useApp } from "../../stores/app";
import { cn } from "../../lib/utils";
import type { SettingsView } from "../../services/types";

/** Settings button with a drop-up menu: Models, Ollama endpoint, current model, installed models, About. */
export function SettingsMenu() {
  const { ollama, models, session, overview, setModelsOpen, openSettings } = useApp();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const downloading = overview?.downloads.filter((d) => d.status === "queued" || d.status === "running").length ?? 0;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); root.current?.querySelector<HTMLButtonElement>("[data-settings-trigger]")?.focus(); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const items = [...(root.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
        const i = items.indexOf(document.activeElement as HTMLButtonElement);
        items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
        e.preventDefault();
      }
    };
    document.addEventListener("mousedown", onDown); document.addEventListener("keydown", onKey);
    root.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const go = (fn: () => void) => () => { setOpen(false); fn(); };
  const view = (v: SettingsView) => go(() => openSettings(v));
  const items: { id: string; icon: typeof Boxes; label: string; hint: string; run: () => void; badge?: string }[] = [
    { id: "models", icon: Boxes, label: "Models", hint: "Browse, download, uninstall, Auto mode", run: go(() => setModelsOpen(true)), badge: downloading ? `${downloading} downloading` : undefined },
    { id: "endpoint", icon: Link2, label: "Ollama endpoint", hint: ollama?.connected ? `Connected · Ollama ${ollama.version ?? ""}` : "Not connected", run: view("endpoint") },
    { id: "model", icon: Sparkles, label: "Current model", hint: session?.model ?? "No session open", run: view("model") },
    { id: "installed", icon: Package, label: "Installed models", hint: `${models.length} installed`, run: view("installed") },
    { id: "about", icon: Info, label: "About ZEUS", hint: "Version, device and memory", run: view("about") },
  ];
  return (
    <div ref={root} className="relative border-t border-border p-2">
      {open && (
        <div role="menu" aria-label="Settings" data-testid="settings-menu"
          className="absolute bottom-full left-2 right-2 z-30 mb-1 overflow-hidden rounded-lg border border-border bg-surface shadow-2xl">
          {items.map((it) => (
            <button key={it.id} type="button" role="menuitem" data-testid={`settings-item-${it.id}`} onClick={it.run}
              className="flex w-full cursor-pointer items-center gap-2.5 border-b border-border/60 px-3 py-2 text-left outline-none last:border-b-0 hover:bg-surface-2 focus-visible:bg-surface-2">
              <it.icon size={15} className="shrink-0 text-accent" />
              <span className="min-w-0 flex-1"><span className="block text-sm">{it.label}</span>
                <span className="block truncate text-[10px] text-muted">{it.hint}</span></span>
              {it.badge && <span className="rounded bg-accent px-1.5 text-[10px] text-accent-foreground">{it.badge}</span>}
            </button>))}
        </div>)}
      <button type="button" data-settings-trigger aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        className={cn("inline-flex h-9 w-full cursor-pointer items-center justify-start gap-1.5 rounded-md px-3 text-sm font-medium outline-none hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent", open && "bg-surface-2")}>
        <Settings size={15} /> Settings
        {downloading > 0 && <span className="ml-auto rounded bg-accent px-1.5 text-[10px] text-accent-foreground" title="Model downloads in progress">{downloading}</span>}
      </button>
    </div>
  );
}
