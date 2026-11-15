import { Dialog } from "./dialog";
import { Button } from "./button";

export interface ConfirmRequest { title: string; description: string; confirmLabel?: string; onConfirm: () => void | Promise<void> }

/** In-app replacement for window.confirm(), which the Tauri/WebKitGTK webview silently answers with "false". */
export function ConfirmDialog({ request, onClose }: { request: ConfirmRequest | null; onClose: () => void }) {
  return (
    <Dialog open={!!request} onOpenChange={(o) => !o && onClose()} title={request?.title ?? ""} description={request?.description} className="w-[min(420px,94vw)]">
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button variant="danger" autoFocus data-testid="confirm-ok" onClick={() => { const r = request; onClose(); void r?.onConfirm(); }}>{request?.confirmLabel ?? "Delete"}</Button>
      </div>
    </Dialog>
  );
}
