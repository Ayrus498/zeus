"""Is this message just conversation (a greeting, thanks, "who are you")? Then there is nothing to do in the project and no tool should be called.

Small models, handed a long list of tools and "inspect the project first", answer "HI" with `list_files`. Messages that are only chit-chat are therefore
answered directly, without tools. The match is deliberately narrow: the WHOLE message (ignoring punctuation and case) must be one of these phrases, so
"hi, create a todo app" or "thanks, now add tests" are never treated as chit-chat.
"""
from __future__ import annotations

import re

_PHRASES = [
    r"hi+", r"hello+", r"hey+( there)?", r"hi there", r"hello there", r"yo", r"hola", r"howdy", r"hiya", r"sup", r"greetings", r"good (morning|afternoon|evening|night|day)",
    r"how are you( doing| today)?", r"how is it going", r"how's it going", r"what's up", r"whats up", r"are you (there|alive|working|ready)", r"anyone (there|home)",
    r"thanks?( you)?( (so|very) much)?", r"thank you( (so|very) much)?", r"thx", r"ty", r"cheers", r"great|nice|cool|awesome|perfect|good|ok|okay|alright|got it|i see",
    r"bye|goodbye|see you|see ya|good ?bye", r"test|testing|ping|hello world",
    r"who are you", r"what are you", r"what('s| is) your name", r"what can you do", r"what do you do", r"help",
]
_RE = re.compile(r"^(?:" + "|".join(f"(?:{p})" for p in _PHRASES) + r")$")
_JOIN = re.compile(r"\s+(?:and|,)\s+|\s+")


def is_chitchat(text: str) -> bool:
    """True when the whole message is only conversation. Up to three such phrases in a row count ("hi, how are you")."""
    t = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or len(t) > 60:
        return False
    if _RE.match(t):
        return True
    # "hi how are you": split the words into at most three phrases that each match
    words = t.split(" ")
    if len(words) > 8:
        return False

    def parts(ws: list[str], left: int) -> bool:
        if not ws:
            return True
        if left == 0:
            return False
        return any(_RE.match(" ".join(ws[:i])) and parts(ws[i:], left - 1) for i in range(1, len(ws) + 1))
    return parts(words, 3)
