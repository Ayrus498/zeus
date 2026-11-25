"""Rescue: the user asked for a file, the model printed the whole file in chat instead of calling create_file.

Small local models often do this (especially when the prompt says "output only the code"). Instead of ending the run with
nothing saved, Zeus-Agent turns the printed file into a normal create_file call, which still goes through the permission engine
(approval card with the diff, never overwrites). Deliberately conservative: it only fires when the intent is unambiguous.
"""
from __future__ import annotations

import re

MIN_LINES = 12
_ASK = re.compile(r"\b(generate|create|write|build|make|produce|output|give me|implement|scaffold)\b[^.\n]{0,80}\b(file|page|html|script|program|app|website|landing|component|module|class)\b", re.I)
_NO_FILE = re.compile(r"\b(don'?t|do not|without) (save|create|write) (a |any |the )?files?\b|\bjust (show|print|explain)\b|\b(an?|some) (example|snippet)\b|\bhow (do|can|should|to)\b", re.I)
_FENCE = re.compile(r"```([\w+#.-]*)[ \t]*\n(.*?)\n?```", re.S)
_NAMED = re.compile(r"(?<![\w/.-])([\w][\w./-]*\.(?:html?|css|js|jsx|ts|tsx|py|c|cc|cpp|h|hpp|java|go|rs|rb|php|sh|sql|json|ya?ml|toml|md|txt|xml))\b", re.I)

_BY_LANG = {"html": "index.html", "htm": "index.html", "css": "style.css", "javascript": "script.js", "js": "script.js", "typescript": "main.ts",
            "ts": "main.ts", "python": "main.py", "py": "main.py", "c": "main.c", "cpp": "main.cpp", "c++": "main.cpp", "java": "Main.java",
            "go": "main.go", "rust": "main.rs", "bash": "script.sh", "sh": "script.sh", "json": "data.json", "yaml": "config.yaml",
            "yml": "config.yaml", "sql": "query.sql", "php": "index.php", "ruby": "main.rb"}


def _sniff(code: str) -> str | None:
    head = code.lstrip()[:200].lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        return "index.html"
    if head.startswith("#include"):
        return "main.cpp" if "iostream" in head else "main.c"
    if head.startswith("#!/bin/") or head.startswith("#!/usr/bin/env bash"):
        return "script.sh"
    if re.match(r"(import|from) \w+", head):
        return "main.py"
    return None


_EXT_GROUPS = [{"html", "htm"}, {"css"}, {"js", "mjs", "jsx"}, {"ts", "tsx"}, {"py"}, {"c", "h"}, {"cc", "cpp", "hpp", "h"}, {"java"}, {"go"}, {"rs"},
               {"sh"}, {"json"}, {"yaml", "yml"}, {"sql"}, {"php"}, {"rb"}, {"md"}, {"xml"}]


def _ext_fits(named: str, default: str | None) -> bool:
    """A requested file name is only trusted if its extension matches the kind of code that was printed."""
    if not default:
        return True                            # unknown kind of code: the name from the request is the best information we have
    ext, dext = named.rsplit(".", 1)[-1].lower(), default.rsplit(".", 1)[-1].lower()
    return any(ext in g and dext in g for g in _EXT_GROUPS) or ext == dext


def find_printed_file(prompt: str, reply: str) -> dict | None:
    """-> {"path", "content", "reason"} or None. Only for a clear 'generate a file' request answered with one big code block."""
    if not prompt or not reply or "```" not in reply:
        return None
    head = prompt.strip()[:400]            # the request is stated up front; long specs mention "example" etc. later on
    if _NO_FILE.search(head) or not _ASK.search(head):
        return None
    blocks = [(m.group(1).lower(), m.group(2)) for m in _FENCE.finditer(reply)]
    big = [(lang, code) for lang, code in blocks if code.count("\n") + 1 >= MIN_LINES]
    if len(big) != 1:                       # none, or several files: too ambiguous to guess - the Save buttons remain
        return None
    lang, code = big[0]
    default = _BY_LANG.get(lang) or _sniff(code)
    name = None
    named = _NAMED.findall(head)               # only a file named in the request itself; long specs mention other files as examples
    if len(set(n.lower() for n in named)) == 1 and _ext_fits(named[0], default):
        name = named[0]
    name = name or default
    if not name or name.startswith(("/", "~")) or ".." in name.split("/"):
        return None
    return {"path": name, "content": code if code.endswith("\n") else code + "\n",
            "reason": "You asked for a file but the model only printed it in the chat; saving that exact code."}


# ---------------------------------------------------------------- several files printed in a mission
_NAME_OK = re.compile(r"^[\w][\w./-]*\.(?:html?|css|js|jsx|ts|tsx|py|c|cc|cpp|h|hpp|java|go|rs|rb|php|sh|sql|json|ya?ml|toml|md|txt|xml|cfg|ini|env|lock)$", re.I)
_HEADER = re.compile(r"^\s*(?:#|//|--|;|<!--|/\*)\s*(?:file(?:name)?\s*:\s*)?`?([\w][\w./-]*\.[A-Za-z0-9]{1,5})`?\s*(?:-->|\*/)?\s*$", re.I)
_LEAD = re.compile(r"(?:^|\n)[^\n`]*[`*]{1,2}([\w][\w./-]*\.[A-Za-z0-9]{1,5})[`*]{1,2}[^\n`]*:?\s*\n+\s*$|(?:^|\n)#{1,6}\s*`?([\w][\w./-]*\.[A-Za-z0-9]{1,5})`?\s*\n+\s*$|(?:^|\n)\s*(?:file(?:name)?|path)\s*:\s*`?([\w][\w./-]*\.[A-Za-z0-9]{1,5})`?\s*\n+\s*$", re.I)


def find_printed_files(reply: str, max_files: int = 6) -> list[dict]:
    """Files a model printed in chat instead of creating them: each fenced block that is clearly labelled with its file name,
    either by a first-line comment (`# todo.py`, `// app.js`, `<!-- index.html -->`) or by the line right before the fence
    (`**todo.py**:`, `### todo.py`, `File: todo.py`). Unlabelled blocks are never guessed. -> [{"path","content","reason"}]"""
    out, seen = [], set()
    for m in _FENCE.finditer(reply or ""):
        lang, code = m.group(1).lower(), m.group(2)
        name, body = None, code
        first, _, rest = code.partition("\n")
        hm = _HEADER.match(first)
        if hm and _NAME_OK.match(hm.group(1)):
            name, body = hm.group(1), rest
        else:
            lm = _LEAD.search(reply[max(0, m.start() - 200):m.start()])
            cand = next((g for g in (lm.groups() if lm else ()) if g), None)
            if cand and _NAME_OK.match(cand):
                name = cand
        if not name or name.startswith(("/", "~")) or ".." in name.split("/") or name.lower() in seen or not body.strip():
            continue
        seen.add(name.lower())
        out.append({"path": name, "content": body.strip("\n") + "\n",
                    "reason": "The model printed this file in chat instead of creating it; saving that exact code."})
        if len(out) >= max_files:
            break
    return out
