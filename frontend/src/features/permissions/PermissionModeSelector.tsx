import { useState } from "react";
import { Shield } from "lucide-react";
import { Select } from "../../components/ui/select";
import { Dialog } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { useApp } from "../../stores/app";
import type { Mode, Policy } from "../../services/types";

export const MODE_LABEL: Record<Mode, string> = {
  read_only: "Read only – AI cannot modify files", approval: "Approval mode – ask before each operation",
  trusted: "Trusted workspace – allow authorized changes", custom: "Custom permissions",
};
const ACTION_LABEL: Record<string, string> = {
  read: "Read project files", create_file: "Create files", edit_file: "Edit files", delete_file: "Delete files",
  run_command: "Execute terminal commands", install_packages: "Install packages", git_commit: "Git commit", git_push: "Git push",
};

export function PermissionModeSelector() {
  const { perms, setMode, project } = useApp();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, Policy>>({});
  if (!project || !perms) return null;
  const openCustom = () => { setDraft({ ...perms.policy }); setOpen(true); };
  return (
    <>
      <div className="flex items-center gap-1.5"><Shield size={14} className="text-accent" />
        <Select aria-label="Permission mode" value={perms.mode} onChange={(e) => { const m = e.target.value as Mode; if (m === "custom") openCustom(); else void setMode(m); }}>
          {(Object.keys(MODE_LABEL) as Mode[]).map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
        </Select>
        {perms.mode === "custom" && <Button variant="ghost" size="sm" onClick={openCustom}>Edit</Button>}
      </div>
      <Dialog open={open} onOpenChange={setOpen} title="Custom permissions" description="Per-operation policy for this project. Enforced by the backend.">
        <div className="space-y-2">
          {Object.keys(ACTION_LABEL).map((a) => (
            <div key={a} className="flex items-center justify-between text-sm"><span>{ACTION_LABEL[a]}</span>
              <Select value={draft[a] ?? "ask"} disabled={a === "read" && false} onChange={(e) => setDraft({ ...draft, [a]: e.target.value as Policy })}>
                <option value="allow">Allow</option><option value="ask">Ask</option><option value="deny">Deny</option></Select></div>))}
          <div className="flex justify-end pt-2"><Button onClick={async () => { await setMode("custom", draft); setOpen(false); }}>Save</Button></div>
        </div>
      </Dialog>
    </>
  );
}
