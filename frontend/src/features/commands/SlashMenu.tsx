import { useEffect, useRef } from "react";
import { cn } from "../../lib/utils";
import type { Cmd } from "./commands";

/** The list that opens above the prompt box when you type "/": arrow keys or the mouse choose, Tab completes, Enter runs. */
export function SlashMenu({ items, index, onPick, onHover }: { items: Cmd[]; index: number; onPick: (c: Cmd) => void; onHover: (i: number) => void }) {
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => { list.current?.querySelector<HTMLElement>("[aria-selected=true]")?.scrollIntoView({ block: "nearest" }); }, [index]);
  let last = "";
  return (
    <ul ref={list} role="listbox" aria-label="Commands" data-testid="slash-menu"
      className="absolute bottom-full left-0 right-0 z-30 mb-2 max-h-72 overflow-auto rounded-xl border border-border bg-surface py-1 shadow-2xl">
      {items.map((c, i) => {
        const head = c.group !== last; last = c.group;
        return (
          <li key={c.name} role="presentation">
            {head && <div className="px-3 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">{c.group}</div>}
            <div role="option" aria-selected={i === index} data-testid={`slash-${c.name}`} onMouseEnter={() => onHover(i)} onMouseDown={(e) => { e.preventDefault(); onPick(c); }}
              className={cn("flex cursor-pointer items-baseline gap-3 px-3 py-1.5 text-xs", i === index ? "bg-accent/20" : "hover:bg-surface-2")}>
              <code className="shrink-0 font-mono text-accent">/{c.name}{c.args ? <span className="text-muted"> {c.args}</span> : null}</code>
              <span className="min-w-0 truncate text-muted">{c.desc}</span>
            </div>
          </li>);
      })}
      <li role="presentation" className="border-t border-border px-3 py-1 text-[10px] text-muted">↑↓ choose · Tab completes · Enter runs · Esc closes</li>
    </ul>
  );
}
