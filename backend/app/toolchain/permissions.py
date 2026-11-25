"""Permission engine. The ONLY authority on whether an operation may run.

The UI merely renders requests; the agent/tool layers call `PermissionManager.authorize`
and cannot execute a mutating operation without it returning allowed.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

ALLOW, ASK, DENY = "allow", "ask", "deny"

# folders that file listing, search and the file tree skip (Zeus-Agent's own data, VCS, dependencies, build output)
IGNORE = {".zeus", ".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache",
          "target", "dist", "build", ".idea", ".next", ".cache"}


ACTIONS = ["read", "create_file", "edit_file", "delete_file", "run_command",
           "install_packages", "git_commit", "git_push"]

DEFAULT_POLICY = {"read": ALLOW, "create_file": ASK, "edit_file": ASK, "delete_file": ASK,
                  "run_command": ASK, "install_packages": ASK, "git_commit": ASK, "git_push": ASK}

MODES = {
    "read_only": {**{a: DENY for a in ACTIONS}, "read": ALLOW},
    "approval": DEFAULT_POLICY,
    # Trusted: routine file changes and commands are allowed inside the workspace. Deletion,
    # package installs and git commit/push still need a human ("never silently delete").
    "trusted": {**DEFAULT_POLICY, "create_file": ALLOW, "edit_file": ALLOW, "run_command": ALLOW},
}

# Commands that never run, in any mode.
BLOCKED_COMMAND_PATTERNS = [
    r"(^|[\s;&|(])sudo(\s|$)", r"(^|[\s;&|(])su(\s|$)", r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|~|\$HOME)(\s|/?$)",
    r"\bmkfs\b", r"\bdd\s+.*of=/dev/", r":\(\)\s*\{", r">\s*/dev/sd", r"\bchmod\s+-R\s+\S+\s+/(\s|$)",
    r"(curl|wget)[^|;]*\|\s*(sudo\s+)?(ba|z)?sh",
]
INSTALL_PATTERNS = [
    r"\bpip3?\s+install\b", r"\bpython3?\s+-m\s+pip\s+install\b", r"\.venv/bin/pip\d?\s+install\b",
    r"\bnpm\s+(install|i|add)\b", r"\byarn\s+add\b", r"\bpnpm\s+(add|install|i)\b", r"\bcargo\s+(add|install)\b",
    r"\bapt(-get)?\s+install\b", r"\bgo\s+(get|install)\b", r"\bgem\s+install\b", r"\buv\s+(pip|add)\b",
    r"\bpoetry\s+(add|install)\b", r"\bpipx\s+install\b",
]


class PermissionError_(Exception):
    pass


class PathViolation(PermissionError_):
    pass


def resolve_in_workspace(root: Path | str, rel: str) -> Path:
    """Resolve `rel` inside `root`, rejecting traversal and symlink escapes.

    Symlinks are resolved (including parents of not-yet-existing files); the final real
    path must be inside the real workspace root.
    """
    root_real = Path(root).resolve()
    if rel is None or str(rel).strip() == "":
        raise PathViolation("empty path")
    if "\x00" in rel:
        raise PathViolation("invalid path")
    p = Path(rel)
    cand = p if p.is_absolute() else root_real / p
    real = cand.resolve()  # non-strict: resolves existing symlinked ancestors
    try:
        real.relative_to(root_real)
    except ValueError:
        raise PathViolation(f"path escapes workspace: {rel}")
    # Reject a final component that is itself a symlink pointing outside (resolve handled);
    # also refuse to write *through* any symlink so changes land where displayed.
    return real


def classify_command(cmd: str) -> str:
    if is_blocked(cmd):
        return "blocked"
    if re.search(r"\bgit\s+push\b", cmd):
        return "git_push"
    if re.search(r"\bgit\s+commit\b", cmd):
        return "git_commit"
    if any(re.search(p, cmd) for p in INSTALL_PATTERNS):
        return "install_packages"
    return "run_command"


def is_blocked(cmd: str) -> bool:
    return any(re.search(p, cmd) for p in BLOCKED_COMMAND_PATTERNS)


@dataclass
class Operation:
    action: str            # one of ACTIONS
    target: str            # relative path, or exact command string
    reason: str = ""
    detail: dict = field(default_factory=dict)  # e.g. {"diff": "...", "content_preview": "..."}

    @property
    def key(self) -> tuple[str, str]:
        return (self.action, self.target)


@dataclass
class Decision:
    allowed: bool
    why: str
    scope: str = ""  # policy | once | session | rejected | denied


Requester = Callable[[dict], Awaitable[str]]  # returns "once" | "session" | "reject"


class PermissionManager:
    def __init__(self, mode: str = "approval", custom: dict | None = None):
        self.mode = mode
        self.custom = custom or {}
        self.session_grants: set[tuple[str, str]] = set()

    def policy_for(self, action: str) -> str:
        if self.mode == "custom":
            return self.custom.get(action, DEFAULT_POLICY[action])
        return MODES.get(self.mode, DEFAULT_POLICY)[action]

    def policy_table(self) -> dict:
        return {a: self.policy_for(a) for a in ACTIONS}

    async def authorize(self, op: Operation, project_name: str, agent: str, request: Requester | None) -> Decision:
        if op.action not in ACTIONS:
            return Decision(False, f"unknown action {op.action}", "denied")
        policy = self.policy_for(op.action)
        if policy == DENY:
            return Decision(False, f"'{op.action}' is not permitted in {self.mode} mode", "denied")
        if policy == ALLOW:
            return Decision(True, "allowed by policy", "policy")
        if op.key in self.session_grants:
            return Decision(True, "approved for this session", "session")
        if request is None:
            return Decision(False, "approval required but no approver is connected", "denied")
        answer = await request({
            "id": uuid.uuid4().hex, "agent": agent, "project": project_name,
            "action": op.action, "target": op.target, "reason": op.reason, "detail": op.detail,
        })
        if answer == "session":
            self.session_grants.add(op.key)
            return Decision(True, "approved for session", "session")
        if answer == "once":
            return Decision(True, "approved once", "once")
        return Decision(False, "rejected by user", "rejected")


def parse_custom(raw: str | None) -> dict:
    try:
        d = json.loads(raw or "{}")
        return {k: v for k, v in d.items() if k in ACTIONS and v in (ALLOW, ASK, DENY)}
    except Exception:
        return {}
