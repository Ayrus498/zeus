import { cn } from "../../lib/utils";
import { CodeBox } from "../../components/ui/copy";

export function DiffView({ diff, maxHeight = 320 }: { diff: string; maxHeight?: number }) {
  if (!diff.trim()) return <div className="text-xs text-muted">(no textual changes)</div>;
  return (
    <CodeBox text={diff}><pre className="overflow-auto rounded-md border border-border bg-[#0d1117] p-2 font-mono text-[12px] leading-5" style={{ maxHeight }}>
      {diff.split("\n").map((l, i) => (
        <div key={i} className={cn("whitespace-pre", l.startsWith("+") && !l.startsWith("+++") && "bg-ok/15 text-ok",
          l.startsWith("-") && !l.startsWith("---") && "bg-danger/15 text-danger", l.startsWith("@@") && "text-accent")}>{l || " "}</div>
      ))}
    </pre></CodeBox>
  );
}
