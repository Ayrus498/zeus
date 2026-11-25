"""Rolling summary: keep a long conversation inside the model's context window without forgetting it.

The full transcript always stays in the database. When what would be sent to the model reaches 75% of the context window, the older part (everything
before the last two user turns) is summarised by the same model into a short structured note, stored on the session (`summary`, covering the first
`summary_msgs` messages). From then on the model receives: system prompt, project memory, that summary, and only the newer messages. Each later
compaction folds the previous summary and the newly aged messages into one updated summary, so it never grows past a few hundred words.

The boundary is always a user message, so an assistant tool call is never separated from its result. Pure helpers here; the model call is in
`AgentEngine.compact`.
"""
from __future__ import annotations

import json

TRIGGER = 0.75                 # compact when the prompt would fill this much of the window
KEEP_TURNS = 2                 # user turns (and everything after them) that stay verbatim
MIN_COVER = 2                  # do not compact fewer new messages than this
MAX_SUMMARY = 6000             # characters
TOOL_CLIP, MSG_CLIP = 500, 3000
RESERVE = 2048                 # room kept for the answer

SYSTEM = ("You maintain the memory of a coding-assistant conversation. You are given the current summary (may be empty) and the next part of the transcript. "
          "Return the UPDATED summary only: no preface. Be factual and do not invent anything.\n"
          "Keep: the user's goal; decisions and the reasons; files created or changed (paths); commands run and results that matter; the current state; "
          "open problems and next steps; the user's preferences and constraints; names, versions, numbers and error messages worth remembering.\n"
          "Drop: greetings, repetition and long tool output. Use short bullet points under these headings: Goal, Decisions, Files, State, Next steps, Preferences. "
          "Under 350 words.")


def boundary(rows: list[dict], covered: int, keep_turns: int = KEEP_TURNS) -> int | None:
    """Index of the first message that stays verbatim, or None when there is nothing worth compacting.
    It is the user message that starts the last `keep_turns` turns, and must lie beyond what is already summarised."""
    users = [i for i, r in enumerate(rows) if r["role"] == "user"]
    if keep_turns < 1 or len(users) < keep_turns:
        return None
    b = users[-keep_turns]
    return b if b - covered >= MIN_COVER else None


def needs_compaction(tokens: int, window: int) -> bool:
    return tokens + RESERVE > TRIGGER * window


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n // 2] + f"\n...[{len(s) - n} characters omitted]...\n" + s[-n // 2:]


def render(rows: list[dict]) -> str:
    """The part of the transcript to summarise, as readable text. Tool output is clipped hard: the summary needs the outcome, not the dump."""
    out = []
    for r in rows:
        role, c = r["role"], r.get("content") or ""
        if role == "user":
            out.append("USER: " + _clip(c, MSG_CLIP))
        elif role == "assistant":
            calls = r.get("tool_calls")
            if isinstance(calls, str):
                try:
                    calls = json.loads(calls)
                except ValueError:
                    calls = None
            if c.strip():
                out.append("ASSISTANT: " + _clip(c, MSG_CLIP))
            for t in calls or []:
                f = t.get("function", {})
                a = f.get("arguments")
                a = a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)
                out.append(f"ASSISTANT CALLED {f.get('name', '?')}({_clip(a, 300)})")
        elif role == "tool":
            out.append(f"TOOL RESULT ({r.get('tool_name') or '?'}): " + _clip(c, TOOL_CLIP))
    return "\n".join(out)


def chunks(text: str, limit: int) -> list[str]:
    """Split a transcript on line boundaries into pieces of at most `limit` characters (a single longer line is cut)."""
    out, cur, size = [], [], 0
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append("\n".join(cur)); cur, size = [], 0
            out.append(line[:limit]); line = line[limit:]
        if size + len(line) + 1 > limit and cur:
            out.append("\n".join(cur)); cur, size = [], 0
        cur.append(line); size += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def prompt(previous: str, piece: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"CURRENT SUMMARY:\n{previous.strip() or '(none yet)'}\n\nNEW TRANSCRIPT:\n{piece}\n\nWrite the updated summary."}]


def system_block(summary: str) -> str:
    return ("\n\nConversation so far (a summary of the earlier part of this conversation; those messages are no longer shown below, "
            "so rely on this summary for what happened before):\n" + summary.strip())
