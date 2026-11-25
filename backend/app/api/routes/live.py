"""The two WebSockets: the agent conversation and the user's terminal."""
from __future__ import annotations

import asyncio
import os
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...agents.mission import MAX_DOC_BYTES
from ...agents.router import AUTO
from ...core import config
from ...modelconnectors import registry as eng
from ...toolchain.engine import ToolContext
from ...toolchain.permissions import classify_command, parse_custom
from ...workspace.projects import ProjectError


def router(ctx) -> APIRouter:
    r = APIRouter()
    db, projects, sessions, runner, tools, engines, agent, mission_runner, record_state, project_perms, runtime = ctx.db, ctx.projects, ctx.sessions, ctx.runner, ctx.tools, ctx.engines, ctx.agent, ctx.mission_runner, ctx.record_state, ctx.project_perms, ctx.runtime

    async def start_run(sid: int, text: str, client: str = "gui"):
        rt = runtime(sid)
        s = sessions.get(sid)
        if rt.running:
            await rt.emit({"type": "error", "message": "A run is already in progress for this session."})
            return
        if not s["model"]:
            await rt.emit({"type": "error", "message": "No model selected. Choose a model first."})
            return
        if s["model"] == AUTO and engines.name_of(s) != "ollama":
            await rt.emit({"type": "error", "message": "Auto mode picks among Ollama models. With llama.cpp, choose a model from the list."})
            return
        proj = projects.get(s["project_id"])
        rt.perms.mode, rt.perms.custom = proj["permission_mode"], parse_custom(proj["custom_permissions"])
        sessions.auto_title(sid, text)
        record_state(sid, client)
        ctx = ToolContext(project=proj, session_id=sid, agent="coder", perms=rt.perms, db=db, runner=runner,
                          emit=rt.emit, request=rt.request_permission)

        async def job():
            eng.current_engine.set(engines.name_of(sessions.get(sid)))         # this run (and only this run) uses its session's engine
            try:
                await agent.run(ctx, sessions.get(sid), text)
            except asyncio.CancelledError:
                rt.reject_all()
                await rt.emit({"type": "run_end", "status": "stopped"})
            except Exception:
                pass  # already reported via 'error' event

        rt.task = asyncio.create_task(job())

    async def start_mission(sid: int, m: dict):
        rt = runtime(sid)
        s = sessions.get(sid)
        if rt.running:
            await rt.emit({"type": "error", "message": "A run is already in progress for this session."})
            return
        if not s["model"]:
            await rt.emit({"type": "error", "message": "No model selected. Choose a model first."})
            return
        name = re.sub(r"[^\w .()+-]", "_", os.path.basename(str(m.get("name") or "mission.md")))[:120] or "mission.md"
        content = str(m.get("content") or "")
        if not name.lower().endswith((".md", ".markdown")):
            await rt.emit({"type": "error", "message": "Only Markdown (.md) documents can be attached."})
            return
        if not content.strip() or len(content.encode("utf-8")) > MAX_DOC_BYTES:
            await rt.emit({"type": "error", "message": f"The document is empty or larger than {MAX_DOC_BYTES // 1000} KB."})
            return
        proj = projects.get(s["project_id"])
        rt.perms.mode, rt.perms.custom = proj["permission_mode"], parse_custom(proj["custom_permissions"])
        ctx = ToolContext(project=proj, session_id=sid, agent="coder", perms=rt.perms, db=db, runner=runner,
                          emit=rt.emit, request=rt.request_permission)

        async def job():
            eng.current_engine.set(engines.name_of(sessions.get(sid)))
            try:
                await mission_runner.run(ctx, sessions.get(sid), name, content, str(m.get("note") or "")[:4000],
                                         int(m.get("max_rounds") or 5), "think" if m.get("verifier") == "think" else "same")
            except asyncio.CancelledError:
                rt.reject_all()
                await rt.emit({"type": "run_end", "status": "stopped"})
            except Exception:
                pass  # already reported via 'error' event

        rt.task = asyncio.create_task(job())

    @r.websocket("/ws/sessions/{sid}")
    async def ws_session(ws: WebSocket, sid: int):
        await ws.accept()
        if not sessions.get(sid):
            await ws.close(4404)
            return
        rt = runtime(sid)
        rt.subscribers.add(ws)
        await ws.send_json({"type": "state", "session_id": sid, "running": rt.running,
                            "pending": [r for _, r in rt.pending.values()]})
        try:
            while True:
                m = await ws.receive_json()
                t = m.get("type")
                if t == "prompt" and str(m.get("content", "")).strip():
                    await start_run(sid, m["content"].strip(), "cli" if ws.query_params.get("client") == "cli" else "gui")
                elif t == "mission":
                    await start_mission(sid, m)
                elif t == "stop" and rt.running:
                    rt.reject_all()
                    runner.cancel_all()
                    rt.task.cancel()
                elif t == "permission_response":
                    rt.respond(m.get("id", ""), m.get("decision", "reject"))
        except WebSocketDisconnect:
            pass
        finally:
            rt.subscribers.discard(ws)

    @r.websocket("/ws/terminal/{pid}")
    async def ws_terminal(ws: WebSocket, pid: int):
        await ws.accept()
        try:
            proj = projects.get(pid)
        except ProjectError:
            await ws.close(4404)
            return
        lock = asyncio.Lock()

        async def send(ev):
            try:
                async with lock:
                    await ws.send_json(ev)
            except Exception:
                pass

        tasks: set[asyncio.Task] = set()
        try:
            while True:
                m = await ws.receive_json()
                if m.get("type") == "run":
                    cmd = str(m.get("command", "")).strip()
                    proj = projects.get(pid)
                    pm = project_perms(proj)
                    action = classify_command(cmd)
                    if not cmd:
                        continue
                    if action == "blocked" or pm.policy_for("run_command") == "deny":
                        await send({"type": "command_denied", "command": cmd,
                                    "reason": "blocked command" if action == "blocked" else "read-only mode forbids commands"})
                        continue
                    # A command typed by the user in the terminal panel is their own explicit action.
                    ctx = ToolContext(project=proj, session_id=None, agent="user", perms=pm, db=db, runner=runner, emit=send, request=None)
                    t = asyncio.create_task(tools.run_approved_command(ctx, cmd, int(m.get("timeout") or config.COMMAND_TIMEOUT)))
                    tasks.add(t); t.add_done_callback(tasks.discard)
                elif m.get("type") == "cancel":
                    runner.cancel(m.get("run_id", ""))
        except WebSocketDisconnect:
            pass
        finally:
            for t in tasks:
                t.cancel()

    return r
