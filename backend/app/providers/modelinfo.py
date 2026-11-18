"""Live model details from the public Ollama library page (https://ollama.com/library/<family>).

Only the model family name is sent to ollama.com (a plain GET of a public page); nothing about the user or project.
The page is parsed with the standard library: summary line, README (converted to Markdown), tag list with sizes and
context windows, capability badges and last-updated text. Results are cached in memory and the last good copy is kept
in the settings table so the Models window still shows details when offline.
"""
from __future__ import annotations

import html
import json
import re
import time
from html.parser import HTMLParser

import httpx

BASE = "https://ollama.com/library"
FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,80}$")
TTL = 6 * 3600
MAX_README = 7000
_mem: dict[str, tuple[float, dict]] = {}


class _Readme(HTMLParser):
    """Converts the README <div id="display"> into simple Markdown."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.depth = 0
        self.active = False
        self.list_stack: list[str] = []
        self.in_pre = False
        self.href: str | None = None
        self.row: list[str] | None = None
        self.header_done = False
        self.in_cell = False
        self.cell: list[str] = []

    def _w(self, s: str):
        if self.in_cell:
            self.cell.append(s)
        else:
            self.out.append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if not self.active:
            if tag == "div" and a.get("id") == "display":
                self.active, self.depth = True, 1
            return
        if tag == "div":
            self.depth += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._w("\n\n" + "#" * min(int(tag[1]) + 1, 6) + " ")  # demote: the window already has an h3
        elif tag == "p":
            self._w("\n\n")
        elif tag in ("ul", "ol"):
            self.list_stack.append(tag)
            self._w("\n")
        elif tag == "li":
            self._w("\n" + "  " * max(0, len(self.list_stack) - 1) + ("1. " if self.list_stack[-1:] == ["ol"] else "- "))
        elif tag == "pre":
            self.in_pre = True
            self._w("\n\n```\n")
        elif tag == "code" and not self.in_pre:
            self._w("`")
        elif tag in ("strong", "b"):
            self._w("**")
        elif tag in ("em", "i"):
            self._w("*")
        elif tag == "a":
            h = a.get("href") or ""
            self.href = h if h.startswith("https://") else None
            if self.href:
                self._w("[")
        elif tag == "br":
            self._w("\n")
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.in_cell, self.cell = True, []
        elif tag == "blockquote":
            self._w("\n\n> ")

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag == "div":
            self.depth -= 1
            if self.depth <= 0:
                self.active = False
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            self._w("\n")
        elif tag == "pre":
            self.in_pre = False
            self._w("\n```\n")
        elif tag == "code" and not self.in_pre:
            self._w("`")
        elif tag in ("strong", "b"):
            self._w("**")
        elif tag in ("em", "i"):
            self._w("*")
        elif tag == "a" and self.href:
            self._w(f"]({self.href})")
            self.href = None
        elif tag in ("td", "th"):
            if self.row is not None:
                self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "\\|"))
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            self.out.append("\n| " + " | ".join(self.row) + " |")
            if not self.header_done:
                self.out.append("\n|" + " --- |" * len(self.row))
                self.header_done = True
            self.row = None
        elif tag == "table":
            self.header_done = False
            self.out.append("\n")

    def handle_data(self, data):
        if self.active:
            self._w(data if self.in_pre else re.sub(r"\s+", " ", data))


def _clean(md: str) -> str:
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


def parse(page: str, family: str) -> dict:
    """Extract details from the library page HTML. Pure function (unit tested against a saved fixture)."""
    m = re.search(r'id="summary-content"[^>]*>\s*(.*?)\s*</span>', page, re.S)
    summary = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
    rp = _Readme()
    rp.feed(page)
    readme = _clean("".join(rp.out))
    if len(readme) > MAX_README:
        readme = readme[:MAX_README].rsplit("\n", 1)[0] + "\n\n…(truncated; see the full page on ollama.com)"
    caps = []
    for c in re.findall(r'bg-indigo-50[^>]*>\s*([A-Za-z][\w .\-]{0,24}?)\s*</span>', page):
        if c not in caps:
            caps.append(c)
    tags, seen = [], set()
    for tm in re.finditer(r'<a href="/library/([^"]+)" class="sm:hidden[^>]*>(.*?)</a>', page, re.S):
        name, inner = tm.group(1), tm.group(2)
        if name in seen:
            continue
        seen.add(name)
        info = re.search(r'<p class="flex text-neutral-500">\s*(.*?)\s*</p>', inner, re.S)
        parts = [p.strip() for p in html.unescape(re.sub(r"<[^>]+>", "", info.group(1))).split("·")] if info else []
        size = next((p for p in parts if re.fullmatch(r"[\d.]+\s?[KMGT]?B", p)), None)
        ctx = next((p.replace("context window", "").strip() for p in parts if "context" in p), None)
        tags.append({"tag": name, "size": size, "context": ctx, "latest": "latest" in inner.split("</p>", 1)[-1],
                     "kind": next((p for p in parts if p in ("Text", "Image", "Audio")), None),
                     "updated": parts[-1] if parts and "ago" in parts[-1] else None})
        if len(tags) >= 40:
            break
    um = re.search(r"Updated&nbsp;</span>\s*<span\s*>([^<]+)</span>", page)
    return {"family": family, "url": f"{BASE}/{family}", "summary": summary, "readme": readme, "capabilities": caps,
            "tags": tags, "updated": um.group(1).strip() if um else None}


async def fetch(family: str, db=None, force: bool = False) -> dict:
    """Return details for a library family: live if possible, else cached, else an 'offline' stub."""
    if not FAMILY_RE.match(family or ""):
        raise ValueError("invalid model family")
    now = time.time()
    hit = _mem.get(family)
    if hit and not force and now - hit[0] < TTL:
        return {**hit[1], "source": "cache"}
    err = None
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "zeus-agent"}) as c:
            r = await c.get(f"{BASE}/{family}")
        if r.status_code == 404:
            err = "This model is not in the public Ollama library."
        elif r.status_code >= 400:
            err = f"ollama.com answered {r.status_code}."
        else:
            data = parse(r.text, family)
            if data["summary"] or data["readme"] or data["tags"]:
                data["fetched_at"] = int(now)
                _mem[family] = (now, data)
                if db is not None:
                    try:
                        db.set_setting(f"modelinfo:{family}", json.dumps(data))
                    except Exception:
                        pass
                return {**data, "source": "live"}
            err = "ollama.com returned a page ZEUS could not read."
    except (httpx.HTTPError, OSError) as e:
        err = f"Could not reach ollama.com ({type(e).__name__})."
    if db is not None:
        try:
            raw = db.get_setting(f"modelinfo:{family}")
            if raw:
                return {**json.loads(raw), "source": "offline-cache", "error": err}
        except Exception:
            pass
    return {"family": family, "url": f"{BASE}/{family}", "summary": "", "readme": "", "capabilities": [], "tags": [],
            "updated": None, "source": "offline", "error": err}
