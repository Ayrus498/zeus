import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/utils";

export function Dialog({ open, onOpenChange, title, description, children, className, dismissable = true }: {
  open: boolean; onOpenChange?: (o: boolean) => void; title: string; description?: string; children: ReactNode; className?: string; dismissable?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <D.Content
          onInteractOutside={(e) => !dismissable && e.preventDefault()}
          onEscapeKeyDown={(e) => !dismissable && e.preventDefault()}
          className={cn("fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(640px,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-xl border border-border bg-surface p-5 shadow-2xl", className)}>
          <div className="mb-3 flex items-start justify-between">
            <div>
              <D.Title className="text-base font-semibold">{title}</D.Title>
              <D.Description className={description ? "mt-0.5 text-xs text-muted" : "sr-only"}>{description ?? title}</D.Description>
            </div>
            {dismissable && <D.Close className="cursor-pointer rounded p-1 text-muted hover:bg-surface-2" aria-label="Close"><X size={16} /></D.Close>}
          </div>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
