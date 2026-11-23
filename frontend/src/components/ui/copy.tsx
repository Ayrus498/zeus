import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "../../lib/utils";
import { useStickScroll } from "../../hooks/useStickScroll";

async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy"); ta.remove(); return ok;
  } catch { return false; }
}

export function CopyButton({ getText, className }: { getText: () => string; className?: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  return (
    <button type="button" title={state === "ok" ? "Copied" : "Copy to clipboard"} aria-label="Copy to clipboard"
      onClick={async (e) => {
        e.stopPropagation();
        setState((await copyText(getText())) ? "ok" : "fail");
        setTimeout(() => setState("idle"), 1500);
      }}
      className={cn("grid h-6 w-6 cursor-pointer place-items-center rounded-md border border-white/20 bg-black/40 text-white/80 backdrop-blur hover:bg-black/60 hover:text-white", className)}>
      {state === "ok" ? <Check size={13} className="text-[#6fb67a]" /> : <Copy size={13} className={state === "fail" ? "text-[#e5645a]" : ""} />}
    </button>
  );
}

/** Wraps a code block and shows a copy button on hover. Pass `text`, or omit to copy the rendered text content. */
export function CodeBox({ text, children, className, follow = false }: { text?: string; children: ReactNode; className?: string; follow?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  // While streaming, keep the block scrolled to the newest line - unless the reader scrolled up to look at earlier code.
  useStickScroll(() => ref.current?.querySelector("pre"), children, follow);
  return (
    <div ref={ref} data-codebox className={cn("group/code relative", className)}>
      {children}
      <CopyButton className="absolute right-2 top-2 opacity-0 transition-opacity focus:opacity-100 group-hover/code:opacity-100"
        getText={() => text ?? ref.current?.querySelector("pre")?.textContent ?? ""} />
    </div>
  );
}
