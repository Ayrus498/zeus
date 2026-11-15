import { Children, isValidElement, useEffect, useRef, useState } from "react";
import type { ChangeEvent, OptionHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "../../lib/utils";

type Opt = { value: string; label: ReactNode };

function collect(children: ReactNode, out: Opt[] = []): Opt[] {
  Children.forEach(children, (c) => {
    if (!isValidElement(c)) return;
    const p = c.props as OptionHTMLAttributes<HTMLOptionElement> & { children?: ReactNode };
    if (c.type === "option") out.push({ value: String(p.value ?? ""), label: p.children });
    else if (p.children) collect(p.children, out);
  });
  return out;
}

/**
 * Custom dropdown with the same props as a native <select> (value, onChange(e.target.value), <option> children).
 * Native select popups are unreliable in the WebKitGTK webview (unstyled, clicks dropped), so we draw our own.
 */
export function Select({ className, value, onChange, disabled, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const opts = collect(children);
  const cur = opts.find((o) => o.value === String(value ?? ""));

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", down); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key); };
  }, [open]);

  const pick = (v: string) => {
    setOpen(false);
    if (v !== String(value ?? "")) onChange?.({ target: { value: v }, currentTarget: { value: v } } as unknown as ChangeEvent<HTMLSelectElement>);
  };

  return (
    <div ref={root} className="relative inline-block max-w-full">
      <button type="button" disabled={disabled} aria-label={rest["aria-label"]} aria-haspopup="listbox" aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={cn("flex h-8 max-w-full cursor-pointer items-center gap-2 rounded-md border border-border bg-surface-2 px-2 text-xs text-foreground outline-none hover:border-accent focus:border-accent disabled:cursor-not-allowed disabled:opacity-50", className)}>
        <span className="truncate">{cur?.label ?? "Select…"}</span>
        <ChevronDown size={12} className="shrink-0 text-muted" />
      </button>
      {open && (
        <ul role="listbox" className="absolute left-0 top-full z-50 mt-1 max-h-72 min-w-full w-max max-w-[90vw] overflow-auto rounded-md border border-border bg-surface-2 py-1 shadow-xl">
          {opts.map((o) => {
            const sel = o.value === String(value ?? "");
            return (
              <li key={o.value} role="option" aria-selected={sel} onClick={() => pick(o.value)}
                className={cn("flex cursor-pointer items-center gap-2 whitespace-nowrap px-3 py-1.5 text-xs", sel ? "bg-accent text-accent-foreground" : "text-foreground hover:bg-border")}>
                <Check size={12} className={sel ? "opacity-100" : "opacity-0"} />{o.label}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
