import { CodeBox } from "../../components/ui/copy";
import { ShieldAlert } from "lucide-react";
import { Dialog } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { DiffView } from "../chat/DiffView";
import { useApp } from "../../stores/app";

const LABEL: Record<string, string> = {
  create_file: "Create file", edit_file: "Edit file", delete_file: "Delete file", run_command: "Run command",
  install_packages: "Install packages", git_commit: "Git commit", git_push: "Git push",
};

export function PermissionDialog() {
  const { pending, respond, session } = useApp();
  const req = pending.find((p) => !p.session_id || p.session_id === session?.id);
  if (!req) return null;
  const isCmd = ["run_command", "install_packages", "git_commit", "git_push"].includes(req.action);
  return (
    <Dialog open title="ZEUS permission request" description={pending.length > 1 ? `${pending.length - 1} more waiting` : undefined} dismissable={false} className="w-[min(720px,94vw)]">
      <div className="space-y-3 text-sm">
        <div className="flex items-center gap-2 rounded-md bg-warn/10 px-3 py-2 text-warn"><ShieldAlert size={16} />{LABEL[req.action] ?? req.action}</div>
        <dl className="grid grid-cols-[80px_1fr] gap-y-1 text-xs">
          <dt className="text-muted">Agent</dt><dd>{req.agent}</dd>
          <dt className="text-muted">Project</dt><dd>{req.project}</dd>
          <dt className="text-muted">{isCmd ? "Command" : "File"}</dt>
          <dd className="font-mono">{isCmd ? "" : req.target}</dd>
        </dl>
        {isCmd && <CodeBox text={req.target}><pre className="overflow-auto whitespace-pre-wrap rounded-md border border-border bg-[#0d1117] p-3 pr-10 font-mono text-[13px]" data-testid="exact-command">{req.target}</pre></CodeBox>}
        {isCmd && req.detail?.cwd && <div className="text-xs text-muted">Working directory: <span className="font-mono">{req.detail.cwd}</span></div>}
        {req.reason && <div><div className="text-xs text-muted">Reason</div><div>{req.reason}</div></div>}
        {!isCmd && req.detail?.diff && <div><div className="mb-1 text-xs text-muted">{req.action === "create_file" ? "New file content" : "Changes"}</div><DiffView diff={req.detail.diff} /></div>}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={() => respond(req.id, "reject")}>Reject</Button>
          <Button variant="secondary" onClick={() => respond(req.id, "session")} title="Applies only to this exact operation, for this session">Approve for Session</Button>
          <Button onClick={() => respond(req.id, "once")}>Approve Once</Button>
        </div>
      </div>
    </Dialog>
  );
}
