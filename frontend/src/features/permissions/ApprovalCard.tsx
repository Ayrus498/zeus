import { ShieldAlert } from "lucide-react";
import { CodeBox } from "../../components/ui/copy";
import { Button } from "../../components/ui/button";
import { DiffView } from "../chat/DiffView";
import { useApp } from "../../stores/app";

const LABEL: Record<string, string> = {
  create_file: "Create file", edit_file: "Edit file", delete_file: "Delete file", run_command: "Run command",
  install_packages: "Install packages", git_commit: "Git commit", git_push: "Git push",
};
const CMD_ACTIONS = ["run_command", "install_packages", "git_commit", "git_push"];

/** Inline approval prompt, rendered in the conversation where the agent is waiting. Replaces the old modal dialog. */
export function ApprovalCard() {
  const { pending, respond, session } = useApp();
  const mine = pending.filter((p) => !p.session_id || p.session_id === session?.id);
  const req = mine[0];
  if (!req) return null;
  const isCmd = CMD_ACTIONS.includes(req.action);
  return (
    <div className="my-2 overflow-hidden rounded-lg border border-warn/50 bg-surface shadow-lg" role="alertdialog" aria-label="Permission request" data-testid="approval-card">
      <div className="flex items-center gap-2 border-b border-warn/30 bg-warn/10 px-3 py-2 text-sm text-warn">
        <ShieldAlert size={16} /><span className="font-medium">{LABEL[req.action] ?? req.action}</span>
        <span className="ml-auto text-xs text-muted">{mine.length > 1 ? `${mine.length - 1} more waiting · ` : ""}{req.agent} · {req.project}</span>
      </div>
      <div className="space-y-2 p-3 text-sm">
        {!isCmd && <div className="text-xs"><span className="text-muted">File </span><span className="font-mono">{req.target}</span></div>}
        {isCmd && <CodeBox text={req.target}><pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-[#0d1117] p-3 pr-10 font-mono text-[13px]" data-testid="exact-command">{req.target}</pre></CodeBox>}
        {isCmd && req.detail?.cwd && <div className="text-xs text-muted">Working directory: <span className="font-mono">{req.detail.cwd}</span></div>}
        {req.reason && <div className="text-xs"><span className="text-muted">Reason </span>{req.reason}</div>}
        {!isCmd && req.detail?.diff && <div><div className="mb-1 text-xs text-muted">{req.action === "create_file" ? "New file content" : "Changes"}</div><DiffView diff={req.detail.diff} maxHeight={280} /></div>}
        <div className="flex flex-wrap justify-end gap-2 pt-1">
          <Button variant="outline" data-testid="approval-reject" onClick={() => respond(req.id, "reject")}>Reject</Button>
          <Button variant="secondary" onClick={() => respond(req.id, "session")} title="Applies only to this exact operation, for this session">Approve for Session</Button>
          <Button data-testid="approval-once" autoFocus onClick={() => respond(req.id, "once")}>Approve Once</Button>
        </div>
      </div>
    </div>
  );
}
