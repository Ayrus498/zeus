"""Agent execution loop: stream model -> validate tool calls -> authorize -> execute -> repeat.

`agent` is an explicit identifier on every message/event so Phase 2 can run several agents
(different roles/models) through the same loop and event bus.
"""
from __future__ import annotations

import json
import time

from ..config import MAX_AGENT_STEPS
from ..database.db import Database, now
from ..providers.ollama import OllamaError, OllamaProvider
from ..tools.engine import ToolContext, ToolEngine, ollama_tools
from .prompts import SYSTEM_PROMPT
from .router import AUTO
from .toolparse import extract_tool_calls


class AgentSpec:
    def __init__(self, name="coder", system_prompt=SYSTEM_PROMPT, tools=None):
        self.name, self.system_prompt = name, system_prompt
        self.tools = tools if tools is not None else ollama_tools()


class AgentEngine:
    def __init__(self, db: Database, provider: OllamaProvider, tools: ToolEngine):
        self.db, self.provider, self.tools = db, provider, tools

    router = None      # optional AutoRouter; enables model == 'auto'

    def _history(self, session_id: int, spec: AgentSpec, project: dict, no_tools: bool = False) -> list[dict]:
        rows = self.db.q("SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))
        sys_prompt = spec.system_prompt.format(path=project["path"], name=project["name"])
        if no_tools:
            sys_prompt += ("\n\nIMPORTANT: no tools are available for this turn. Do not call tools or print tool-call JSON; "
                           "answer directly and thoroughly from the conversation and your own knowledge. If the user needs files "
                           "changed or commands run, say so and suggest they ask for it (a coding model will handle it).")
        msgs = [{"role": "system", "content": sys_prompt}]
        last_tools = {r["id"] for r in [r for r in rows if r["role"] == "tool"][-8:]}
        for r in rows:
            m = {"role": r["role"], "content": r["content"]}
            if r["role"] == "tool":
                if r["id"] not in last_tools and len(m["content"]) > 1200:  # keep context small
                    m["content"] = m["content"][:1200] + "\n...[older output truncated]"
                m["tool_name"] = r["tool_name"]
            if r["tool_calls"]:
                m["tool_calls"] = json.loads(r["tool_calls"])
            msgs.append(m)
        return msgs

    on_change = None   # optional callback(session_id): lets the app mirror the session to .zeus/

    def _save(self, session_id, agent, role, content, tool_calls=None, tool_name=None):
        mid = self.db.x("INSERT INTO messages(session_id,agent,role,content,tool_calls,tool_name,created) VALUES(?,?,?,?,?,?,?)",
                        (session_id, agent, role, content, json.dumps(tool_calls) if tool_calls else None, tool_name, now()))
        if self.on_change:
            self.on_change(session_id)
        return mid

    async def run(self, ctx: ToolContext, session: dict, user_text: str, spec: AgentSpec | None = None):
        spec = spec or AgentSpec()
        emit, sid = ctx.emit, session["id"]
        ctx.agent = spec.name
        self._save(sid, "user", "user", user_text)
        self.db.x("UPDATE sessions SET updated=? WHERE id=?", (now(), sid))
        await emit({"type": "run_start", "agent": spec.name})
        model, tools_on = session["model"], True
        try:
            if model == AUTO:
                choice = await self.router.route(user_text, session.get("last_model")) if self.router else None
                if not choice:
                    await emit({"type": "error", "message": "Auto mode found no chat-capable model. Download one in the Models tab, or pick a model manually."})
                    await emit({"type": "run_end", "status": "error"})
                    return
                model, tools_on = choice.model, choice.tools
                self.db.x("UPDATE sessions SET last_model=? WHERE id=?", (model, sid))
                await emit({"type": "model_selected", "model": model, "role": choice.role, "tools": tools_on,
                            "reason": choice.reason, "previous": session.get("last_model")})
                session = {**session, "last_model": model}
            recent: list[str] = []
            for step in range(MAX_AGENT_STEPS):
                msgs = self._history(sid, spec, ctx.project, no_tools=not tools_on)
                content, native_calls, usage = "", [], None
                await emit({"type": "model_loading", "model": model})
                first = True
                async for chunk in self.provider.chat_stream(model, msgs, spec.tools if tools_on else None, session.get("num_ctx")):
                    if first:
                        first = False
                        await emit({"type": "model_ready"})
                    m = chunk.get("message", {})
                    if m.get("content"):
                        content += m["content"]
                        await emit({"type": "token", "agent": spec.name, "text": m["content"]})
                    if m.get("tool_calls"):
                        native_calls += m["tool_calls"]
                    if chunk.get("done"):
                        usage = chunk
                calls = native_calls
                if not calls and content:
                    calls, content = extract_tool_calls(content)   # content: JSON call text removed
                if usage:
                    await self._usage(session, usage, emit)
                self._save(sid, spec.name, "assistant", content, calls or None)
                await emit({"type": "assistant_end", "agent": spec.name, "has_tool_calls": bool(calls), "text": content})
                if not calls:
                    await emit({"type": "run_end", "status": "completed"})
                    return
                stuck = self._repeat_guard(recent, calls)
                for call in calls:
                    fn = call.get("function", {})
                    name, args = fn.get("name", ""), fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    await emit({"type": "tool_call", "name": name, "args": args})
                    if stuck:
                        res = {"ok": False, "output": "You have repeated the same tool call several times without progress. Stop and summarize for the user what is blocking you."}
                    else:
                        res = await self.tools.execute(ctx, name, args)
                    await emit({"type": "tool_result", "name": name, "ok": res["ok"], "output": res["output"][:4000]})
                    self._save(sid, spec.name, "tool", res["output"], tool_name=name)
                if stuck >= 2:
                    await emit({"type": "error", "message": "Stopped: the model kept repeating the same action. Try rephrasing, a larger model, or fewer constraints."})
                    await emit({"type": "run_end", "status": "loop_detected"})
                    return
            await emit({"type": "run_end", "status": "step_limit"})
        except OllamaError as e:
            await emit({"type": "error", "message": str(e)})
            await emit({"type": "run_end", "status": "error"})
        except Exception as e:
            await emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            await emit({"type": "run_end", "status": "error"})
            raise

    @staticmethod
    def _repeat_guard(recent: list[str], calls: list[dict]) -> int:
        """Detects non-progress: 3+ identical consecutive batches, or the same batch 5+ times
        within the last 14 (catches A-B-A-B oscillation). Returns >0 once triggered."""
        sig = json.dumps(calls, sort_keys=True, default=str)
        recent.append(sig)
        n = 0
        for s in reversed(recent):
            if s != sig:
                break
            n += 1
        if n >= 3:
            return n - 2
        return 2 if recent[-14:].count(sig) >= 5 else 0

    async def _usage(self, session, u, emit):
        pin, pout = u.get("prompt_eval_count", 0) or 0, u.get("eval_count", 0) or 0
        dur = (u.get("eval_duration") or 0) / 1e9
        self.db.x("UPDATE sessions SET input_tokens=input_tokens+?, output_tokens=output_tokens+? WHERE id=?", (pin, pout, session["id"]))
        row = self.db.one("SELECT input_tokens,output_tokens FROM sessions WHERE id=?", (session["id"],))
        if self.on_change:
            self.on_change(session["id"])
        await emit({"type": "usage", "prompt_tokens": pin, "output_tokens": pout,
                    "tokens_per_sec": round(pout / dur, 1) if dur else None,
                    "session_input": row["input_tokens"], "session_output": row["output_tokens"],
                    "context_used": pin + pout, "source": "ollama"})
