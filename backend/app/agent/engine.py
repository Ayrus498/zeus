"""Agent execution loop: stream model -> validate tool calls -> authorize -> execute -> repeat.

`agent` is an explicit identifier on every message/event so Phase 2 can run several agents
(different roles/models) through the same loop and event bus.
"""
from __future__ import annotations

import json
import re
import time

from ..config import MAX_AGENT_STEPS
from ..database.db import Database, now
from ..providers.ollama import OllamaError, OllamaProvider
from ..tools.engine import ToolContext, ToolEngine, ollama_tools
from .prompts import SYSTEM_PROMPT
from .modelprefs import Prefs, build_prefs, plan_context, prepare_messages
from .rescue import find_printed_file, find_printed_files
from .router import AUTO
from .toolparse import extract_tool_calls, looks_fabricated_tool_output, looks_like_tool_attempt


class AgentSpec:
    def __init__(self, name="coder", system_prompt=SYSTEM_PROMPT, tools=None):
        self.name, self.system_prompt = name, system_prompt
        self.tools = tools if tools is not None else ollama_tools()


_ZMARK = re.compile(r"^<!--zeus:[^>]*-->\n?")      # bookkeeping line on mission/feedback messages; the model never needs to see it


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
            m = {"role": r["role"], "content": _ZMARK.sub("", r["content"]) if r["role"] == "user" else r["content"]}
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

    # ------------------------------------------------------------------ model / prompt preparation
    _meta: dict | None = None

    async def meta(self, model: str) -> dict:
        """Ollama's metadata for a model (capabilities, recommended parameters, context length); cached, never raises."""
        self._meta = self._meta if self._meta is not None else {}
        key = (getattr(self.provider, "base_url", ""), model)
        if key not in self._meta:
            try:
                self._meta[key] = await self.provider.show(model)
            except Exception:  # noqa: BLE001 - fakes in tests, offline models, odd versions: fall back to defaults
                self._meta[key] = {}
        return self._meta[key]

    async def prefs(self, model: str, role: str = "agent") -> Prefs:
        return build_prefs(model, await self.meta(model), role)

    async def choose_model(self, session: dict, text: str, emit, force_role: str | None = None):
        """Resolve the session's model (Auto routes per prompt). Returns (model, tools_on, session) or None after reporting why."""
        model = session["model"]
        if model != AUTO:
            return model, True, session
        choice = await self.router.route(text, session.get("last_model")) if self.router else None
        if not choice:
            await emit({"type": "error", "message": "Auto mode found no chat-capable model. Download one in Settings → Models, or pick a model manually."})
            return None
        self.db.x("UPDATE sessions SET last_model=? WHERE id=?", (choice.model, session["id"]))
        await emit({"type": "model_selected", "model": choice.model, "role": choice.role, "tools": choice.tools,
                    "reason": choice.reason, "previous": session.get("last_model")})
        return choice.model, choice.tools, {**session, "last_model": choice.model}

    # ------------------------------------------------------------------ entry point for a normal prompt
    async def run(self, ctx: ToolContext, session: dict, user_text: str, spec: AgentSpec | None = None):
        spec = spec or AgentSpec()
        emit, sid = ctx.emit, session["id"]
        ctx.agent = spec.name
        self._save(sid, "user", "user", user_text)
        self.db.x("UPDATE sessions SET updated=? WHERE id=?", (now(), sid))
        await emit({"type": "run_start", "agent": spec.name})
        try:
            chosen = await self.choose_model(session, user_text, emit)
            if not chosen:
                await emit({"type": "run_end", "status": "error"})
                return
            model, tools_on, session = chosen
            res = await self.loop(ctx, session, spec, model, tools_on, user_text)
            await emit({"type": "run_end", "status": res["status"]})
        except OllamaError as e:
            await emit({"type": "error", "message": str(e)})
            await emit({"type": "run_end", "status": "error"})
        except Exception as e:
            await emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            await emit({"type": "run_end", "status": "error"})
            raise

    # ------------------------------------------------------------------ the agent loop (one "round")
    async def loop(self, ctx: ToolContext, session: dict, spec: AgentSpec, model: str, tools_on: bool, user_text: str,
                   step_limit: int = MAX_AGENT_STEPS, role: str = "agent", require_action: bool = False, action_hint: str = "") -> dict:
        """stream model -> parse tool calls -> authorize -> execute -> repeat, until the model stops calling tools.
        Returns {"status": completed|loop_detected|step_limit, "text": final assistant text, "wrote": bool, "steps": n}.
        Emits everything except run_start/run_end (the caller owns those, so a mission can chain several rounds)."""
        emit, sid = ctx.emit, session["id"]
        prefs = await self.prefs(model, role)
        mmax = prefs.context_length
        recent: list[str] = []
        malformed, nudge, wrote, last_text, steps, noted = 0, None, False, "", 0, set()
        denied = False                        # the user refused something: never push the model to retry it
        acted, act_nudges = False, 0          # `acted`: a file was written or a command ran (reading and listing do not count)
        for step in range(step_limit):
            steps += 1
            msgs = self._history(sid, spec, ctx.project, no_tools=not tools_on)
            if nudge:                                   # one-off correction; not stored in the conversation
                msgs.append({"role": "user", "content": nudge})
                nudge = None
            native = tools_on and prefs.native_tools
            msgs = prepare_messages(msgs, prefs, tools_on)
            num_ctx, ctx_note = plan_context(msgs, session.get("num_ctx"), mmax)
            if ctx_note and num_ctx not in noted:               # once per window size, not on every step as the estimate creeps up
                noted.add(num_ctx)
                await emit({"type": "notice", "level": "info", "text": ctx_note})
            content, native_calls, usage = "", [], None
            await emit({"type": "model_loading", "model": model})
            first = True
            async for chunk in self.provider.chat_stream(model, msgs, spec.tools if native else None, num_ctx, prefs.options, prefs.think):
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
            if not calls and tools_on and content and malformed < 2 and looks_like_tool_attempt(content):
                # The model tried to call a tool but the call could not be parsed (e.g. invalid JSON in a big file).
                # Don't end the run silently: tell it what went wrong and let it try again.
                malformed += 1
                await emit({"type": "assistant_end", "agent": spec.name, "has_tool_calls": True, "text": ""})   # drop the streamed garbage
                await emit({"type": "notice", "level": "info", "text": f"The model's tool call was malformed; asking it to retry ({malformed}/2)."})
                nudge = ("Your previous reply tried to call a tool, but the call could not be parsed (invalid JSON or format), so NOTHING was executed. "
                         "Call the tool again using the native tool-calling mechanism with valid arguments. "
                         "If the file is large, make sure every quote inside the content is properly escaped, or split the work into several smaller files/edits.")
                continue
            if not calls and tools_on and content and malformed < 2 and looks_fabricated_tool_output(content):
                malformed += 1
                await emit({"type": "assistant_end", "agent": spec.name, "has_tool_calls": True, "text": ""})
                await emit({"type": "notice", "level": "info", "text": f"The model wrote a made-up tool result instead of calling the tool; making it try again ({malformed}/2)."})
                nudge = ("Your previous reply contained text that looks like a tool result (for example <tool_response> or \"Created x (N chars)\"). "
                         "Nothing was executed: only the system produces tool results. Do NOT write results yourself. Call the tool now with the native tool-calling mechanism.")
                continue
            self._save(sid, spec.name, "assistant", content, calls or None)
            await emit({"type": "assistant_end", "agent": spec.name, "has_tool_calls": bool(calls), "text": content})
            last_text = content or last_text
            if not calls:
                printed = None if (wrote or not tools_on) else find_printed_file(user_text, content)
                if printed:
                    # The model printed the whole file instead of saving it: do it through the normal create_file path
                    # (approval card, diff, never overwrites) rather than ending the run with nothing on disk.
                    await emit({"type": "notice", "level": "info", "text": f"The model printed the file in chat instead of saving it. Saving it as {printed['path']} (needs your approval in Approval mode)."})
                    await emit({"type": "tool_call", "name": "create_file", "args": printed})
                    res = await self.tools.execute(ctx, "create_file", printed)
                    await emit({"type": "tool_result", "name": "create_file", "ok": res["ok"], "output": res["output"][:4000]})
                    self._save(sid, spec.name, "tool", res["output"], tool_name="create_file")
                    wrote = wrote or res["ok"]
                    acted = acted or res["ok"]
                if require_action and tools_on and not acted and not denied:
                    saved = []
                    for pf in find_printed_files(content):
                        await emit({"type": "notice", "level": "info", "text": f"The model printed {pf['path']} in chat instead of creating it. Saving it for you (needs your approval in Approval mode)."})
                        await emit({"type": "tool_call", "name": "create_file", "args": pf})
                        res = await self.tools.execute(ctx, "create_file", pf)
                        await emit({"type": "tool_result", "name": "create_file", "ok": res["ok"], "output": res["output"][:4000]})
                        self._save(sid, spec.name, "tool", res["output"], tool_name="create_file")
                        if res["ok"]:
                            saved.append(pf["path"])
                        elif res["output"].startswith("Permission denied"):
                            denied = True
                    if saved:
                        acted = wrote = True
                        nudge = (f"ZEUS saved the file(s) you printed ({', '.join(saved)}) because you did not call a tool. From now on call create_file yourself "
                                 "(with the complete content). Continue the mission: create any files that are still missing, then run the tests with run_command.")
                        continue
                if require_action and tools_on and not acted and not denied and act_nudges < 2:
                    # Missions need real work. Small models often answer with a plan and stop: send them back to act.
                    act_nudges += 1
                    await emit({"type": "notice", "level": "info", "text": f"The model described what to do but did not do it; asking it to start now ({act_nudges}/2)."})
                    nudge = ("You described a plan, but NOTHING has been created or run yet. Do the work now: call create_file with the complete file content "
                             "(or run_command) for the first missing item. Do not describe or list steps again - act." + (f"\n{action_hint}" if action_hint else ""))
                    continue
                return {"status": "completed", "text": last_text, "wrote": wrote, "steps": steps}
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
                wrote = wrote or name in ("create_file", "edit_file")
                if stuck:
                    res = {"ok": False, "output": "You have repeated the same tool call several times without progress. Stop and summarize for the user what is blocking you."}
                else:
                    res = await self.tools.execute(ctx, name, args)
                    refused = res["output"].startswith(("Permission denied", "Blocked", "Invalid tool call", "unknown tool"))
                    # a command that ran and failed (e.g. a failing test run) is still work done; a refused or invalid call is not
                    acted = acted or (name in ("create_file", "edit_file", "delete_file", "run_command") and not refused)
                    denied = denied or res["output"].startswith(("Permission denied", "Blocked"))
                await emit({"type": "tool_result", "name": name, "ok": res["ok"], "output": res["output"][:4000]})
                self._save(sid, spec.name, "tool", res["output"], tool_name=name)
            if stuck >= 2:
                await emit({"type": "error", "message": "Stopped: the model kept repeating the same action. Try rephrasing, a larger model, or fewer constraints."})
                return {"status": "loop_detected", "text": last_text, "wrote": wrote, "steps": steps}
        return {"status": "step_limit", "text": last_text, "wrote": wrote, "steps": steps}

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
