"""Missions: an attached Markdown document becomes the objective; Zeus-Agent works on it in rounds until it is *verified*.

Flow per mission
  1. parse the document (title, objective, acceptance criteria from checkboxes / "Acceptance criteria"-style sections);
     if it has none, ask the model to extract verifiable criteria
  2. round loop: run the normal tool-using agent loop  ->  collect evidence (files written, commands run, referenced files)
     -> a strict reviewer pass judges every criterion from that evidence only  ->  if all are met the mission is accepted,
     otherwise the unmet criteria go back to the agent as feedback and the next round starts
  3. stop when accepted, when the user stops it, when a round makes no progress (stalled), or at the round limit
The reviewer never executes anything; commands/files still go through the permission engine like any other run.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..database.db import Database, now
from ..projects.manager import IGNORE
from .engine import AgentEngine, AgentSpec
from .modelprefs import prepare_messages

MAX_DOC_BYTES = 200_000
MAX_CRITERIA = 12
STEPS_PER_ROUND = 30
EVIDENCE_BUDGET = 28_000
MARK_RE = re.compile(r"^<!--zeus:[^>]*-->\n?")

_H = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)
_OBJ = re.compile(r"objective|goal|mission|task|overview|summary|description|purpose|about|background|problem", re.I)
_CRIT = re.compile(r"acceptance|success criteria|definition of done|done when|requirements?|deliverables?|verification|checklist|must have|expected (?:outcome|result)s?|criteria", re.I)
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+?)\s*$")
_BOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]\s*(.+?)\s*$", re.M)


@dataclass
class Parsed:
    title: str
    objective: str
    criteria: list[str] = field(default_factory=list)


def _sections(md: str) -> list[tuple[str, str]]:
    heads = list(_H.finditer(md))
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
        out.append((h.group(2).strip(), md[h.end():end].strip()))
    return out


def parse_mission_md(md: str, filename: str = "mission.md") -> Parsed:
    md = md.replace("\r\n", "\n")
    h1 = re.search(r"^#\s+(.+?)\s*$", md, re.M)
    title = (h1.group(1) if h1 else Path(filename).stem.replace("-", " ").replace("_", " ")).strip()[:120] or "Mission"
    secs = _sections(md)
    criteria: list[str] = [m.group(1) for m in _BOX.finditer(md)]
    for head, body in secs:
        if _CRIT.search(head):
            for line in body.splitlines():
                m = _ITEM.match(line)
                if m:
                    criteria.append(m.group(1))
    seen, uniq = set(), []
    for c in criteria:
        c = re.sub(r"\s+", " ", c).strip()
        if c and c.lower() not in seen:
            seen.add(c.lower()); uniq.append(c[:300])
    objective = next((b for h, b in secs if _OBJ.search(h) and b), "")
    if not objective:
        parts = md.split("\n", 1)
        intro = parts[1] if h1 and md.lstrip().startswith("#") and len(parts) > 1 else ("" if h1 and md.lstrip().startswith("#") else md)
        objective = re.split(r"\n#{1,6}\s", intro, maxsplit=1)[0]
        objective = "\n".join(l for l in objective.splitlines() if not _BOX.match(l)).strip()    # the checklist is not the objective
    return Parsed(title, re.sub(r"\n{3,}", "\n\n", objective)[:1500] or title, uniq[:MAX_CRITERIA])


def extract_json(text: str) -> dict | None:
    """First JSON object found in a model reply (tolerates fences, prose and <think> blocks)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    dec = json.JSONDecoder()
    pos = 0
    while (pos := text.find("{", pos)) != -1:
        try:
            obj, end = dec.raw_decode(text, pos)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        pos += 1
    return None


def _fence(md: str) -> str:
    n = 3
    while "`" * n in md:
        n += 1
    return "`" * n


def build_task(mission_id: int, name: str, md: str, criteria: list[str], note: str) -> str:
    f = _fence(md)
    crit = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    parts = [f"<!--zeus:mission id={mission_id} name={name}-->",
             f"# MISSION from the attached document `{name}`",
             "Complete this mission autonomously with your tools (create/edit files, run commands and tests). Keep going until EVERY acceptance "
             "criterion below is satisfied. Zeus-Agent will review your work against the criteria and send you back if anything is missing, so do the "
             "whole job and check it yourself first (run the tests/commands that prove it). When you believe it is complete, reply with a short "
             "summary and no tool call.",
             "", "## Acceptance criteria (checked by Zeus-Agent)", crit]
    if note.strip():
        parts += ["", "## Extra instructions from the user", note.strip()]
    parts += ["", "## Mission document", f"{f}markdown", md.strip(), f]
    return "\n".join(parts)


# ------------------------------------------------------------------ evidence
_PATH_TOKEN = re.compile(r"(?<![\w/@.-])((?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z][A-Za-z0-9]{0,5})(?![\w/-])")


def referenced_files(text: str) -> list[str]:
    out = []
    for m in _PATH_TOKEN.finditer(text):
        t = m.group(1)
        if t.startswith(("http", "www.")) or re.fullmatch(r"\d+(\.\d+)+", t) or t.lower().endswith((".com", ".org", ".io", ".net", ".dev")):
            continue
        if t not in out:
            out.append(t)
    return out[:20]


def build_evidence(db: Database, project: dict, session_id: int, since: float, objective: str, criteria: list[str], worker_text: str) -> str:
    root = Path(project["path"])
    parts: list[str] = []
    # files written during this mission (latest content), most recent first
    rows = db.q("SELECT path,op,after,created FROM file_changes WHERE session_id=? AND created>=? ORDER BY id DESC", (session_id, since))
    seen, files = set(), []
    for r in rows:
        if r["path"] in seen:
            continue
        seen.add(r["path"])
        files.append(r)
    per = max(800, min(4500, EVIDENCE_BUDGET // max(1, len(files)) if files else 4500))
    parts.append("## Files created or changed during this mission")
    if not files:
        parts.append("(none)")
    for r in files:
        if r["op"] == "delete":
            parts.append(f"- {r['path']}: DELETED")
            continue
        body = r["after"] or ""
        ex = body if len(body) <= per else body[: int(per * 0.75)] + f"\n...[{len(body) - per:,} chars omitted]...\n" + body[-int(per * 0.25):]
        parts.append(f"### {r['path']} ({len(body):,} chars)\n```\n{ex}\n```")
    # what is on disk now
    listing = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in IGNORE)
        for fn in sorted(fns):
            fp = Path(dp) / fn
            try:
                listing.append(f"{fp.relative_to(root)} ({fp.stat().st_size:,} B)")
            except OSError:
                pass
        if len(listing) > 120:
            break
    parts.append("\n## Project files on disk now\n" + ("\n".join(listing[:120]) or "(empty)"))
    # files named in the objective/criteria
    refs = referenced_files(objective + "\n" + "\n".join(criteria))
    if refs:
        lines = []
        for rel in refs:
            fp = (root / rel)
            lines.append(f"- {rel}: " + (f"EXISTS ({fp.stat().st_size:,} B)" if fp.is_file() else "MISSING"))
        parts.append("\n## Files mentioned in the mission (existence check)\n" + "\n".join(lines))
    # commands the agent ran
    ex = db.q("SELECT command,exit_code,status,output FROM executions WHERE session_id=? AND started>=? ORDER BY id DESC LIMIT 12", (session_id, since))
    parts.append("\n## Commands run during this mission (newest first)")
    parts.append("\n".join(f"$ {e['command']}\n  -> {e['status']}, exit {e['exit_code']}\n  {(e['output'] or '').strip()[-500:]}" for e in ex) or "(none)")
    parts.append("\n## The agent's final message\n" + (worker_text.strip()[-1500:] or "(empty)"))
    out = "\n".join(parts)
    return out if len(out) <= EVIDENCE_BUDGET + 6000 else out[: EVIDENCE_BUDGET + 6000] + "\n...[evidence truncated]"


VERIFIER_SYSTEM = ("You are a strict, evidence-based QA reviewer. You decide whether work meets acceptance criteria using ONLY the evidence given. "
                   "A criterion is met only if the evidence clearly shows it (file content, command output, file exists). No evidence means NOT met. "
                   "Do not be generous and do not do the work yourself.")


def verifier_messages(objective: str, criteria: list[str], evidence: str) -> list[dict]:
    crit = "\n".join(f"{i}. {c}" for i, c in enumerate(criteria, 1))
    user = (f"# Objective\n{objective}\n\n# Acceptance criteria\n{crit}\n\n# Evidence\n{evidence}\n\n"
            "Judge every criterion. Reply with ONLY one JSON object, no prose, in exactly this shape:\n"
            '{"criteria":[{"id":1,"met":true,"evidence":"one short sentence citing the evidence"}],'
            '"accepted":true,"feedback":"concrete, actionable instructions for what is still missing or wrong; empty string if accepted"}\n'
            "`accepted` is true only if every criterion is met.")
    return [{"role": "system", "content": VERIFIER_SYSTEM}, {"role": "user", "content": user}]


def read_verdict(reply: str, criteria: list[str]) -> dict | None:
    """-> {"results":[{"id","text","met","evidence"}], "accepted": bool, "feedback": str}. Zeus-Agent computes `accepted` itself."""
    j = extract_json(reply)
    if not j or not isinstance(j.get("criteria"), list):
        return None
    by_id = {}
    for c in j["criteria"]:
        if isinstance(c, dict) and isinstance(c.get("id"), (int, str)) and str(c["id"]).strip().isdigit():
            by_id[int(str(c["id"]).strip())] = c
    results = []
    for i, text in enumerate(criteria, 1):
        c = by_id.get(i)
        met = bool(c and c.get("met") is True)
        results.append({"id": i, "text": text, "met": met, "evidence": str((c or {}).get("evidence") or ("no verdict returned" if not c else ""))[:300]})
    unmet = [r for r in results if not r["met"]]
    fb = str(j.get("feedback") or "").strip()
    if unmet and not fb:
        fb = "These criteria are not met yet: " + "; ".join(f"({r['id']}) {r['text']}" for r in unmet)
    return {"results": results, "accepted": not unmet, "feedback": fb[:1500]}


# ------------------------------------------------------------------ objective checks (do not rely on the model's opinion)
_TEST_CRIT = re.compile(r"\b(tests?|unittest|pytest|test suite|specs?)\b.*\b(pass|passes|passing|succeed|green|run)\b|\b(passing|green)\b.*\btests?\b", re.I | re.S)
_TEST_CMD = re.compile(r"\b(unittest|pytest|py\.test|npm (?:run )?test|yarn test|go test|cargo test|make test|ctest|mvn test|gradle test|rspec|jest|vitest)\b")
_EXISTS_CRIT = re.compile(r"\b(exists?|created?|named|file|saved|stored|persist)", re.I)


def last_failure(executions: list[dict]) -> dict | None:
    """Newest failed command (non-zero exit) among executions ordered newest-first."""
    return next((e for e in executions if e["exit_code"] not in (0, None)), None)


def hard_checks(results: list[dict], criteria: list[str], executions: list[dict], root: Path) -> list[dict]:
    """Override the reviewer where the facts are objective: a 'tests pass' criterion needs the latest test command to have exited 0,
    and a criterion that names a file needs that file to exist. A model can be talked into 'met'; exit codes and the disk cannot."""
    out = []
    tests = [e for e in executions if _TEST_CMD.search(e["command"] or "")]           # newest first
    for r, text in zip(results, criteria):
        r = dict(r)
        if r["met"] and _TEST_CRIT.search(text):
            if not tests:
                r.update(met=False, evidence="No test command was run, so passing tests cannot be confirmed.")
            elif tests[0]["exit_code"] not in (0,):
                r.update(met=False, evidence=f"The latest test run (`{tests[0]['command'][:60]}`) failed with exit code {tests[0]['exit_code']}.")
        if r["met"] and _EXISTS_CRIT.search(text):
            missing = [f for f in referenced_files(text) if not (root / f).exists() and not re.search(r"\b(no|not|without|never|delete|remove)\b", text, re.I)]
            if missing:
                r.update(met=False, evidence=f"{', '.join(missing)}: MISSING on disk.")
        out.append(r)
    return out


# ------------------------------------------------------------------ the runner
class MissionRunner:
    def __init__(self, db: Database, engine: AgentEngine):
        self.db, self.engine = db, engine

    def _update(self, mid: int, **kw):
        kw["updated"] = now()
        for k in ("criteria", "plan", "log"):
            if k in kw and not isinstance(kw[k], str):
                kw[k] = json.dumps(kw[k])
        self.db.x(f"UPDATE missions SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", (*kw.values(), mid))

    async def _confirm_tests(self, ctx, criteria: list[str], since: float) -> None:
        """If a criterion says tests must pass and the agent's own test run succeeded, run that command once more.
        Catches flaky or state-dependent suites (tests that only pass on a clean slate) before Zeus-Agent accepts anything.
        Goes through the normal run_command path, so the permission mode applies (Approval mode asks again)."""
        if not any(_TEST_CRIT.search(c) for c in criteria):
            return
        execs = self.db.q("SELECT command,exit_code FROM executions WHERE session_id=? AND started>=? ORDER BY id DESC", (ctx.session_id, since))
        tests = [e for e in execs if _TEST_CMD.search(e["command"] or "")]
        if not tests or tests[0]["exit_code"] != 0:
            return                                                    # nothing passed yet (or never ran): hard_checks reports that
        cmd = tests[0]["command"]
        await ctx.emit({"type": "notice", "level": "info", "text": f"Confirming the tests pass by running `{cmd[:80]}` again."})
        args = {"command": cmd, "reason": "Zeus-Agent re-runs the tests to confirm they pass (catches flaky or state-dependent tests)"}
        await ctx.emit({"type": "tool_call", "name": "run_command", "args": args})
        res = await self.engine.tools.execute(ctx, "run_command", args)
        await ctx.emit({"type": "tool_result", "name": "run_command", "ok": res["ok"], "output": res["output"][:4000]})
        self.engine._save(ctx.session_id, "coder", "tool", res["output"], tool_name="run_command")

    async def _chat_json(self, model: str, messages: list[dict], role: str, num_ctx: int | None) -> str:
        prefs = await self.engine.prefs(model, role)
        from .modelprefs import plan_context
        msgs = prepare_messages(messages, prefs, False)
        ctx_n, _ = plan_context(msgs, num_ctx, prefs.context_length, reserve=1500)
        return await self.engine.provider.chat_once(model, msgs, ctx_n, prefs.options, prefs.think)

    async def _extract_criteria(self, model: str, md: str, num_ctx: int | None) -> tuple[str, list[str]]:
        msgs = [{"role": "system", "content": "You turn task documents into verifiable acceptance criteria. Be concrete and testable."},
                {"role": "user", "content": "Read this task document and reply with ONLY a JSON object: "
                 '{"objective":"one or two sentences","criteria":["3 to 8 short, independently checkable acceptance criteria"]}\n\n'
                 + _fence(md) + "markdown\n" + md[:12000] + "\n" + _fence(md)}]
        try:
            j = extract_json(await self._chat_json(model, msgs, "plan", num_ctx))
        except Exception:  # noqa: BLE001
            j = None
        crit = [str(c).strip()[:300] for c in (j or {}).get("criteria", []) if str(c).strip()][:MAX_CRITERIA] if isinstance((j or {}).get("criteria"), list) else []
        return str((j or {}).get("objective") or "")[:1500], crit

    async def run(self, ctx, session: dict, name: str, md: str, note: str = "", max_rounds: int = 5, verifier: str = "same") -> dict:
        emit, sid = ctx.emit, session["id"]
        max_rounds = max(1, min(10, int(max_rounds or 5)))
        spec = AgentSpec()
        ctx.agent = spec.name
        parsed = parse_mission_md(md, name)
        await emit({"type": "run_start", "agent": spec.name})
        mid, status, since = 0, "error", now()
        try:
            chosen = await self.engine.choose_model(session, "/code " + parsed.objective, emit)
            if not chosen:
                await emit({"type": "run_end", "status": "error"})
                return {"status": "error"}
            model, tools_on, session = chosen
            objective, criteria = parsed.objective, parsed.criteria
            if not criteria:
                await emit({"type": "notice", "level": "info", "text": "The document has no checklist or 'Acceptance criteria' section: asking the model to extract verifiable criteria."})
                obj2, criteria = await self._extract_criteria(model, md, session.get("num_ctx"))
                objective = obj2 or objective
                if not criteria:
                    criteria = ["The objective described in the document is fully achieved and works as described."]
            plan_prefs = (await self.engine.prefs(model, "agent")).public()
            mid = self.db.x("INSERT INTO missions(session_id,name,title,objective,criteria,status,round,max_rounds,plan,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (sid, name, parsed.title, objective, json.dumps([{"id": i, "text": c, "met": None, "evidence": ""} for i, c in enumerate(criteria, 1)]),
                             "running", 0, max_rounds, json.dumps(plan_prefs), since, since))
            task = build_task(mid, name, md, criteria, note)
            self.engine._save(sid, "user", "user", task)
            self.db.x("UPDATE sessions SET updated=? WHERE id=?", (now(), sid))
            await emit({"type": "mission_start", "id": mid, "name": name, "title": parsed.title, "objective": objective, "max_rounds": max_rounds,
                        "criteria": [{"id": i, "text": c, "met": None, "evidence": ""} for i, c in enumerate(criteria, 1)], "plan": plan_prefs})
            log, prev_unmet, prev_activity = [], None, None
            status = "limit"
            for rnd in range(1, max_rounds + 1):
                self._update(mid, round=rnd)
                await emit({"type": "mission_round", "id": mid, "round": rnd})
                res = await self.engine.loop(ctx, session, spec, model, tools_on, task, step_limit=STEPS_PER_ROUND, require_action=True,
                                             action_hint="Acceptance criteria still to satisfy:\n" + "\n".join(f"- {c}" for c in criteria))
                vmodel = model
                if verifier == "think" and self.engine.router:
                    ch = await self.engine.router.route("/think review this work", model)
                    vmodel = ch.model if ch else model
                await self._confirm_tests(ctx, criteria, since)
                await emit({"type": "mission_verifying", "id": mid, "round": rnd, "model": vmodel})
                evidence = build_evidence(self.db, ctx.project, sid, since, objective, criteria, res["text"])
                verdict = None
                for attempt in range(2):
                    reply = await self._chat_json(vmodel, verifier_messages(objective, criteria, evidence) + (
                        [{"role": "user", "content": "Your previous reply was not valid JSON. Reply with ONLY the JSON object."}] if attempt else []),
                        "verify", session.get("num_ctx"))
                    verdict = read_verdict(reply, criteria)
                    if verdict:
                        break
                if not verdict:
                    verdict = {"results": [{"id": i, "text": c, "met": False, "evidence": "reviewer reply could not be read"} for i, c in enumerate(criteria, 1)],
                               "accepted": False, "feedback": "The reviewer could not produce a verdict. Re-check each criterion yourself, make any missing change, and summarise the evidence for each one."}
                execs = self.db.q("SELECT command,exit_code FROM executions WHERE session_id=? AND started>=? ORDER BY id DESC", (sid, since))
                before = verdict["results"]
                verdict["results"] = hard_checks(before, criteria, execs, Path(ctx.project["path"]))
                flipped = [r for r, o in zip(verdict["results"], before) if o["met"] and not r["met"]]
                if flipped:                         # facts overruled the reviewer: say so in the feedback the agent receives
                    verdict["accepted"] = False
                    verdict["feedback"] = (verdict["feedback"] + "\n" if verdict["feedback"] else "") + "; ".join(f"({r['id']}) {r['evidence']}" for r in flipped)
                log.append({"round": rnd, "status": res["status"], "steps": res["steps"], "unmet": [r["id"] for r in verdict["results"] if not r["met"]],
                            "feedback": verdict["feedback"]})
                self._update(mid, criteria=verdict["results"], log=log)
                await emit({"type": "mission_check", "id": mid, "round": rnd, "results": verdict["results"], "accepted": verdict["accepted"],
                            "feedback": verdict["feedback"], "agent_status": res["status"], "verifier": vmodel})
                if verdict["accepted"]:
                    status = "accepted"
                    break
                unmet = tuple(r["id"] for r in verdict["results"] if not r["met"])
                activity = (self.db.one("SELECT COUNT(*) n FROM file_changes WHERE session_id=? AND created>=?", (sid, since))["n"],
                            self.db.one("SELECT COUNT(*) n FROM executions WHERE session_id=? AND started>=?", (sid, since))["n"])
                if rnd > 1 and unmet == prev_unmet and activity == prev_activity:
                    status = "stalled"          # same gaps, nothing new on disk: more rounds would not help
                    break
                prev_unmet, prev_activity = unmet, activity
                if rnd == max_rounds:
                    status = "limit"
                    break
                failing = last_failure(self.db.q("SELECT command,exit_code,output FROM executions WHERE session_id=? AND started>=? ORDER BY id DESC", (sid, since)))
                fail_txt = (f"\n\nThe most recent failing command was `{failing['command'][:100]}` (exit {failing['exit_code']}). Its output:\n"
                            f"{(failing['output'] or '').strip()[-900:]}") if failing else ""
                fb = (f"<!--zeus:feedback round={rnd}-->\n[Zeus-Agent review, round {rnd}/{max_rounds}] The mission is NOT accepted yet.\n"
                      f"Unmet criteria:\n" + "\n".join(f"- ({r['id']}) {r['text']} - {r['evidence']}" for r in verdict["results"] if not r["met"]) +
                      f"\n\nWhat to do:\n{verdict['feedback']}{fail_txt}\n\nFix this with your tools, verify it yourself, then reply with a short summary.")
                self.engine._save(sid, "user", "user", fb)
                task = fb
            summary = {"accepted": "Mission accepted: every acceptance criterion is met.",
                       "stalled": "Stopped: the last round changed nothing and the same criteria are still unmet. Give more guidance or adjust the document.",
                       "limit": f"Stopped at the round limit ({max_rounds}) with criteria still unmet."}[status]
            self._update(mid, status=status)
            await emit({"type": "mission_end", "id": mid, "status": status, "summary": summary})
            await emit({"type": "run_end", "status": "completed" if status == "accepted" else status})
            return {"status": status, "id": mid}
        except asyncio.CancelledError:
            if mid:
                self._update(mid, status="stopped")
                await emit({"type": "mission_end", "id": mid, "status": "stopped", "summary": "Stopped by you."})
            raise
        except Exception as e:  # noqa: BLE001
            if mid:
                self._update(mid, status="error")
                await emit({"type": "mission_end", "id": mid, "status": "error", "summary": f"{type(e).__name__}: {e}"})
            await emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            await emit({"type": "run_end", "status": "error"})
            raise
