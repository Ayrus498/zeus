"""Tool execution engine.

Every tool call is validated against a JSON schema, then (for non-read operations) passed
through the PermissionManager BEFORE touching the filesystem or spawning a process.
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from ..config import COMMAND_TIMEOUT, MAX_FILE_BYTES
from ..database.db import Database, now
from ..permissions.manager import (Operation, PathViolation, PermissionManager, classify_command,
                                   resolve_in_workspace)
from ..projects.manager import IGNORE
from ..terminal.runner import Runner, truncate

Emit = Callable[[dict], Awaitable[None]]
Requester = Callable[[dict], Awaitable[str]]

TOOL_SCHEMAS = [
    {"name": "list_files", "description": "List files and directories in a project directory (non-recursive unless recursive=true).",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative directory, default '.'"}, "recursive": {"type": "boolean"}}, "required": []}},
    {"name": "read_file", "description": "Read a text file from the project.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "search_files", "description": "Search project text files for a regular expression. Returns file:line: match.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string", "description": "e.g. *.py"}}, "required": ["pattern"]}},
    {"name": "create_file", "description": "Create a NEW file with the given full content. Fails if the file exists (use edit_file). Always include a 'reason'.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "reason": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Edit an existing file by replacing an exact, unique snippet old_str with new_str.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}, "reason": {"type": "string"}}, "required": ["path", "old_str", "new_str"]}},
    {"name": "delete_file", "description": "Delete a file. Always requires approval in Approval/Trusted mode.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "reason": {"type": "string"}}, "required": ["path"]}},
    {"name": "run_command", "description": "Run a shell command in the project directory (e.g. 'pytest', '.venv/bin/pip install -r requirements.txt'). Output is returned.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "reason": {"type": "string"}}, "required": ["command"]}},
    {"name": "git_status", "description": "Show git status (porcelain) of the project.", "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "git_diff", "description": "Show git diff of uncommitted changes, optionally for one path.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": []}},
]
SCHEMA_BY_NAME = {t["name"]: t for t in TOOL_SCHEMAS}
TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float)}


def ollama_tools() -> list[dict]:
    return [{"type": "function", "function": t} for t in TOOL_SCHEMAS]


def validate_args(name: str, args) -> dict:
    schema = SCHEMA_BY_NAME.get(name)
    if not schema:
        raise ValueError(f"unknown tool '{name}'. Available: {', '.join(SCHEMA_BY_NAME)}")
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    props = schema["parameters"]["properties"]
    for req in schema["parameters"]["required"]:
        if req not in args:
            raise ValueError(f"missing required argument '{req}'")
    clean = {}
    for k, v in args.items():
        if k not in props:
            continue  # ignore unknown extras from small models
        if not isinstance(v, TYPES[props[k]["type"]]):
            if props[k]["type"] == "string" and v is not None:
                v = str(v)
            else:
                raise ValueError(f"argument '{k}' must be {props[k]['type']}")
        clean[k] = v
    return clean


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{path}", f"b/{path}"))


@dataclass
class ToolContext:
    project: dict
    session_id: int | None
    agent: str
    perms: PermissionManager
    db: Database
    runner: Runner
    emit: Emit
    request: Requester | None


class ToolEngine:
    """Stateless executor; all state lives in ToolContext."""

    async def execute(self, ctx: ToolContext, name: str, args) -> dict:
        """Returns {"ok": bool, "output": str}. Never raises for model mistakes."""
        try:
            clean = validate_args(name, args)
        except ValueError as e:
            return {"ok": False, "output": f"Invalid tool call: {e}"}
        try:
            return await getattr(self, f"t_{name}")(ctx, **clean)
        except PathViolation as e:
            return {"ok": False, "output": f"Blocked: {e}"}
        except Exception as e:  # tool bugs / OS errors are reported to the model, not raised
            return {"ok": False, "output": f"{type(e).__name__}: {e}"}

    # ---- helpers
    def _root(self, ctx):
        return Path(ctx.project["path"])

    async def _authorize(self, ctx, op: Operation):
        d = await ctx.perms.authorize(op, ctx.project["name"], ctx.agent, ctx.request)
        await ctx.emit({"type": "permission_decision", "action": op.action, "target": op.target,
                        "allowed": d.allowed, "scope": d.scope})
        return d

    def _record_change(self, ctx, rel, op, before, after):
        ctx.db.x("INSERT INTO file_changes(session_id,project_id,path,op,before,after,created) VALUES(?,?,?,?,?,?,?)",
                 (ctx.session_id, ctx.project["id"], rel, op, before, after, now()))

    # ---- read-only tools (action 'read' is permitted by every mode)
    async def t_list_files(self, ctx, path=".", recursive=False):
        root = self._root(ctx).resolve()
        base = resolve_in_workspace(root, path or ".")
        if not base.is_dir():
            return {"ok": False, "output": f"Not a directory: {path}"}
        lines = []
        if recursive:
            for dp, dns, fns in os.walk(base):
                dns[:] = sorted(d for d in dns if d not in IGNORE)
                for f in sorted(fns):
                    lines.append(str((Path(dp) / f).relative_to(root)))
                    if len(lines) >= 500:
                        break
        else:
            for e in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                if e.name in IGNORE:
                    continue
                lines.append(str(e.relative_to(root)) + ("/" if e.is_dir() else ""))
        return {"ok": True, "output": "\n".join(lines) or "(empty directory)"}

    async def t_read_file(self, ctx, path):
        p = resolve_in_workspace(self._root(ctx), path)
        if not p.is_file():
            return {"ok": False, "output": f"File not found: {path}"}
        if p.stat().st_size > MAX_FILE_BYTES:
            return {"ok": False, "output": "File too large to read"}
        data = p.read_bytes()
        if b"\x00" in data[:4096]:
            return {"ok": False, "output": "Binary file; not readable as text"}
        return {"ok": True, "output": data.decode(errors="replace")}

    async def t_search_files(self, ctx, pattern, path=".", glob="*"):
        root = self._root(ctx).resolve()
        base = resolve_in_workspace(root, path or ".")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return {"ok": False, "output": f"Bad regex: {e}"}
        hits = []
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in IGNORE]
            for f in fns:
                if not fnmatch.fnmatch(f, glob or "*"):
                    continue
                fp = Path(dp) / f
                try:
                    if fp.stat().st_size > MAX_FILE_BYTES or fp.is_symlink():
                        continue
                    for i, line in enumerate(fp.read_text(errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            hits.append(f"{fp.relative_to(root)}:{i}: {line.strip()[:200]}")
                            if len(hits) >= 100:
                                return {"ok": True, "output": "\n".join(hits) + "\n(truncated at 100)"}
                except OSError:
                    continue
        return {"ok": True, "output": "\n".join(hits) or "No matches"}

    # ---- mutating tools
    async def t_create_file(self, ctx, path, content, reason=""):
        root = self._root(ctx)
        p = resolve_in_workspace(root, path)
        rel = str(p.relative_to(root.resolve()))
        if p.exists():
            return {"ok": False, "output": f"{rel} already exists. Use edit_file to modify it (never overwritten silently)."}
        if len(content) > MAX_FILE_BYTES:
            return {"ok": False, "output": "Content too large"}
        op = Operation("create_file", rel, reason, {"content": content, "diff": unified_diff(rel, "", content)})
        d = await self._authorize(ctx, op)
        if not d.allowed:
            return {"ok": False, "output": f"Permission denied: {d.why}. File was NOT created."}
        # Re-validate right before the write (TOCTOU: path may have changed during approval)
        p = resolve_in_workspace(root, path)
        if p.exists():
            return {"ok": False, "output": f"{rel} appeared during approval; not overwritten."}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        self._record_change(ctx, rel, "create", None, content)
        await ctx.emit({"type": "file_change", "op": "create", "path": rel})
        return {"ok": True, "output": f"Created {rel} ({len(content)} chars)"}

    async def t_edit_file(self, ctx, path, old_str, new_str, reason=""):
        root = self._root(ctx)
        p = resolve_in_workspace(root, path)
        rel = str(p.relative_to(root.resolve()))
        if not p.is_file():
            return {"ok": False, "output": f"File not found: {rel}. Use create_file for new files."}
        before = p.read_text()
        n = before.count(old_str) if old_str else 0
        if n == 0:
            return {"ok": False, "output": "old_str not found in file. Re-read the file and copy the snippet exactly."}
        if n > 1:
            return {"ok": False, "output": f"old_str matches {n} places; include more surrounding context to make it unique."}
        after = before.replace(old_str, new_str, 1)
        op = Operation("edit_file", rel, reason, {"diff": unified_diff(rel, before, after)})
        d = await self._authorize(ctx, op)
        if not d.allowed:
            return {"ok": False, "output": f"Permission denied: {d.why}. File was NOT modified."}
        p = resolve_in_workspace(root, path)
        if p.read_text() != before:
            return {"ok": False, "output": "File changed during approval; re-read and retry."}
        p.write_text(after)
        self._record_change(ctx, rel, "edit", before, after)
        await ctx.emit({"type": "file_change", "op": "edit", "path": rel})
        return {"ok": True, "output": f"Edited {rel}"}

    async def t_delete_file(self, ctx, path, reason=""):
        root = self._root(ctx)
        p = resolve_in_workspace(root, path)
        rel = str(p.relative_to(root.resolve()))
        if not p.is_file():
            return {"ok": False, "output": f"File not found (only files can be deleted): {rel}"}
        before = p.read_text(errors="replace")
        op = Operation("delete_file", rel, reason, {"diff": unified_diff(rel, before, "")})
        d = await self._authorize(ctx, op)
        if not d.allowed:
            return {"ok": False, "output": f"Permission denied: {d.why}. File was NOT deleted."}
        p = resolve_in_workspace(root, path)
        p.unlink()
        self._record_change(ctx, rel, "delete", before, None)
        await ctx.emit({"type": "file_change", "op": "delete", "path": rel})
        return {"ok": True, "output": f"Deleted {rel}"}

    async def t_run_command(self, ctx, command, reason=""):
        action = classify_command(command)
        if action == "blocked":
            return {"ok": False, "output": "Blocked: this command is never allowed (sudo/destructive/pipe-to-shell)."}
        op = Operation(action, command, reason, {"cwd": ctx.project["path"]})
        d = await self._authorize(ctx, op)
        if not d.allowed:
            return {"ok": False, "output": f"Permission denied: {d.why}. Command was NOT executed."}
        res = await self.run_approved_command(ctx, command)
        if not res["ok"] and re.search(r"No module named|ModuleNotFoundError|ImportError", res["output"]):
            res["output"] += ("\n[hint] Import failed. If this is the project's own module, run tests via "
                              "`.venv/bin/python -m pytest` (adds the project root to sys.path) and check the file/package names with list_files.")
        return res

    async def run_approved_command(self, ctx, command: str, timeout: int = COMMAND_TIMEOUT) -> dict:
        """Only call after authorization (agent path) or an explicit user action (terminal panel)."""
        run_id = uuid.uuid4().hex[:8]
        exec_id = ctx.db.x("INSERT INTO executions(project_id,session_id,command,started,status) VALUES(?,?,?,?,?)",
                           (ctx.project["id"], ctx.session_id, command, now(), "running"))
        await ctx.emit({"type": "command_start", "run_id": run_id, "command": command, "exec_id": exec_id})

        async def chunk(stream, text):
            await ctx.emit({"type": "command_output", "run_id": run_id, "stream": stream, "text": text})

        res = await ctx.runner.run(run_id, command, ctx.project["path"], timeout, chunk)
        ctx.db.x("UPDATE executions SET finished=?,exit_code=?,status=?,output=? WHERE id=?",
                 (now(), res["exit_code"], res["status"], truncate(res["output"]), exec_id))
        await ctx.emit({"type": "command_end", "run_id": run_id, "exit_code": res["exit_code"],
                        "status": res["status"], "duration": res["duration"]})
        out = truncate(res["output"])
        if res["status"] == "timeout":
            out += f"\n[timed out after {timeout}s and was killed]"
        elif res["status"] == "cancelled":
            out += "\n[cancelled by user]"
        return {"ok": res["exit_code"] == 0, "output": f"exit code: {res['exit_code']}\n{out}"}

    async def _git(self, ctx, *args):
        root = self._root(ctx)
        if not (root / ".git").exists():
            return {"ok": False, "output": "Not a git repository"}
        r = await asyncio.to_thread(subprocess.run, ["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
        return {"ok": r.returncode == 0, "output": truncate(r.stdout + r.stderr) or "(no changes)"}

    async def t_git_status(self, ctx):
        return await self._git(ctx, "status", "--short", "--branch")

    async def t_git_diff(self, ctx, path=""):
        if path:
            resolve_in_workspace(self._root(ctx), path)
            return await self._git(ctx, "diff", "--", path)
        return await self._git(ctx, "diff")
