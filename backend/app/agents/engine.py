"""Agent execution loop: stream model -> validate tool calls -> authorize -> execute -> repeat.

`agent` is an explicit identifier on every message/event so Phase 2 can run several agents
(different roles/models) through the same loop and event bus.
"""
from __future__ import annotations

import json
import re
import time

from ..core.config import MAX_AGENT_STEPS
from ..core.database import Database, now
from ..memory import project_memory as memory
from ..modelconnectors.base import Connector, ConnectorError
from ..toolchain.engine import ToolContext, ToolEngine, ollama_tools
from .prompts import CHAT_PROMPT, SYSTEM_PROMPT
from ..memory import summary as summary_mod
from .modelprefs import Prefs, build_prefs, est_tokens, plan_context, prepare_messages
from .rescue import find_printed_file, find_printed_files
from .intent import is_chitchat
from .router import AUTO
from ..toolchain.toolparse import extract_tool_calls, looks_fabricated_tool_output, looks_like_tool_attempt


class AgentSpec:
    def __init__(self, name="coder", system_prompt=SYSTEM_PROMPT, tools=None):
        self.name, self.system_prompt = name, system_prompt
        self.tools = tools if tools is not None else ollama_tools()


_ZMARK = re.compile(r"^<!--zeus:[^>]*-->\n?")      # bookkeeping line on mission/feedback messages; the model never needs to see it


class AgentEngine:
    def __init__(self, db: Database, provider: Connector, tools: ToolEngine):
        self.db, self.provider, self.tools = db, provider, tools

    router = None      # optional AutoRouter; enables model == 'auto'

    def _history(self, session_id: int, spec: AgentSpec, project: dict, no_tools: bool = False) -> list[dict]:
        rows = self.db.q("SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,))
        sys_prompt = (CHAT_PROMPT if no_tools else spec.system_prompt).format(path=project["path"], name=project["name"])
        sys_prompt += memory.prompt_block(project["path"])                                  # durable project notes (persistence/memory.py)
        srow = self.db.one("SELECT summary, summary_msgs FROM sessions WHERE id=?", (session_id,)) or {}
        covered = int(srow.get("summary_msgs") or 0)
        if srow.get("summary") and covered:                                                 # the older part is replaced by its rolling summary
            sys_prompt += summary_mod.system_block(srow["summary"])
            rows = rows[covered:]
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

    # ------------------------------------------------------------------ rolling summary
    def context_info(self, session: dict, project: dict, spec: AgentSpec | None = None) -> dict:
        """What the next request would contain, for the context meter and /context."""
        spec = spec or AgentSpec()
        msgs = self._history(session["id"], spec, project)
        window = session.get("num_ctx") or 16384
        tokens = est_tokens(msgs)
        srow = self.db.one("SELECT summary, summary_msgs, summary_updated FROM sessions WHERE id=?", (session["id"],)) or {}
        total = self.db.one("SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (session["id"],))["n"]
        return {"tokens": tokens, "window": window, "percent": round(100 * tokens / window), "trigger_percent": int(summary_mod.TRIGGER * 100),
                "messages_total": total, "messages_in_context": max(0, total - int(srow.get("summary_msgs") or 0)),
                "summary": {"text": srow["summary"], "covers": int(srow["summary_msgs"] or 0), "updated": srow["summary_updated"]} if srow.get("summary") else None,
                "memory": memory.info(project["path"])}

    async def compact(self, session: dict, project: dict, model: str, force: bool = False, spec: AgentSpec | None = None) -> dict:
        """Fold the older part of the conversation into the stored summary. `force` (the /compact command) keeps only the last user turn verbatim.
        Never raises: returns {"ok": False, "reason": ...} when there is nothing to do or the model could not summarise."""
        spec = spec or AgentSpec()
        sid = session["id"]
        rows = self.db.q("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,))
        covered = int((self.db.one("SELECT summary_msgs FROM sessions WHERE id=?", (sid,)) or {}).get("summary_msgs") or 0)
        b = summary_mod.boundary(rows, covered, 1 if force else summary_mod.KEEP_TURNS)
        if b is None:
            return {"ok": False, "reason": "Nothing to compact yet: the conversation is short, or the older part is already summarised."}
        window = session.get("num_ctx") or 16384
        before = est_tokens(self._history(sid, spec, project))
        previous = (self.db.one("SELECT summary FROM sessions WHERE id=?", (sid,)) or {}).get("summary") or ""
        text = summary_mod.render(rows[covered:b])
        limit = max(4000, int(window * 3.3 * 0.45))                     # characters of transcript per request: half the window, the rest is for the answer
        pieces = summary_mod.chunks(text, limit)[:6]
        try:
            for piece in pieces:
                out = (await self.provider.chat_once(model, summary_mod.prompt(previous, piece), window, {"temperature": 0.1})).strip()
                if not out:
                    return {"ok": False, "reason": "The model returned an empty summary; nothing was changed."}
                previous = out[:summary_mod.MAX_SUMMARY]
        except Exception as e:  # noqa: BLE001 - offline engine, model unloaded, template errors: keep the full conversation
            return {"ok": False, "reason": f"Could not summarise ({type(e).__name__}: {e}); the full conversation is kept."}
        self.db.x("UPDATE sessions SET summary=?, summary_msgs=?, summary_updated=? WHERE id=?", (previous, b, now(), sid))
        if self.on_change:
            self.on_change(sid)
        after = est_tokens(self._history(sid, spec, project))
        return {"ok": True, "covers": b, "previous_covers": covered, "summary": previous, "tokens_before": before, "tokens_after": after}

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
            if tools_on and is_chitchat(user_text):          # "HI" needs an answer, not a project inspection (small models answer it with list_files)
                tools_on = False
            res = await self.loop(ctx, session, spec, model, tools_on, user_text)
            await emit({"type": "run_end", "status": res["status"]})
        except ConnectorError as e:
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
        compact_failed = False
        last_result: tuple[str, str] | None = None
        forced_text = False                   # after a repetition loop: one last turn that must answer in words
        acted, act_nudges = False, 0          # `acted`: a file was written or a command ran (reading and listing do not count)
        for step in range(step_limit):
            steps += 1
            msgs = self._history(sid, spec, ctx.project, no_tools=not tools_on)
            if not compact_failed and summary_mod.needs_compaction(est_tokens(msgs), session.get("num_ctx") or 16384):
                await emit({"type": "notice", "level": "info", "text": "The conversation is filling the context window: summarising the older part…"})
                r = await self.compact(session, ctx.project, model, spec=spec)
                if r["ok"]:
                    await emit({"type": "compaction", "covers": r["covers"], "tokens_before": r["tokens_before"], "tokens_after": r["tokens_after"], "summary": r["summary"]})
                    msgs = self._history(sid, spec, ctx.project, no_tools=not tools_on)
                else:
                    compact_failed = True                       # try once per run; the existing fallbacks (larger window, shortened tool output) still apply
                    await emit({"type": "notice", "level": "info", "text": r["reason"]})
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
            raw_content = content
            if not calls and content:
                calls, content = extract_tool_calls(content)   # content: JSON call text removed
            ignored_calls = False
            if not tools_on and calls:                          # no tools this turn: a call is not executed; whatever text there is stays the answer
                ignored_calls, calls, content = True, [], raw_content if not native_calls else content
            if forced_text and not calls and ignored_calls and not content.strip():
                await emit({"type": "assistant_end", "agent": spec.name, "has_tool_calls": True, "text": ""})
                await emit({"type": "error", "message": "Stopped: the model kept repeating the same action. Try rephrasing, a larger model, or fewer constraints."})
                return {"status": "loop_detected", "text": last_text, "wrote": wrote, "steps": steps}
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
                        nudge = (f"Zeus-Agent saved the file(s) you printed ({', '.join(saved)}) because you did not call a tool. From now on call create_file yourself "
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
                if not stuck:                                   # remember what the tool really answered, not the guard's own message
                    last_result = (name, res["output"])
            if stuck >= 2:
                if not forced_text:
                    # Before giving up, ask once for an answer in plain words: usually the model already has what it needs (an empty folder, a failing command)
                    # and only cannot stop calling the tool.
                    forced_text, tools_on = True, False
                    await emit({"type": "notice", "level": "info", "text": "The model kept calling the same tool, so Zeus-Agent is asking it to answer in words instead."})
                    seen = f" The last result was: {last_result[1].strip()[:300]!r}." if last_result else ""
                    nudge = ("You keep repeating the same tool call and it does not help. Do NOT call any tool again." + seen + " Reply to the user now in plain words, using what the "
                             "conversation already shows (for example: the folder is empty, or the command failed and why). If the user only said hello, greet them back. "
                             "If you need something from the user, ask.")
                    continue
                await emit({"type": "error", "message": "Stopped: the model kept repeating the same action. Try rephrasing, a larger model, or fewer constraints."})
                return {"status": "loop_detected", "text": last_text, "wrote": wrote, "steps": steps}
            if len(recent) >= 2 and recent[-1] == recent[-2] and not nudge:
                seen = f" The result was: {last_result[1].strip()[:300]!r}." if last_result else ""
                nudge = ("You just made the same tool call twice and got the same result, so calling it again will not change anything." + seen + " Use what you already know: "
                         "either take a different step, or answer the user in plain words now.")
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
