"""The public Ollama model library as a browsable list.

`fetch_library` returns every model family listed on https://ollama.com/library (name, one-line description, capability badges,
parameter sizes, pull count, tag count, last update) so the Models window can show models that are not in the built-in guide.
`search` asks https://ollama.com/search for a query typed in the Models window, which also finds community models (`user/model`).

Privacy: both are plain GETs of public pages. The library list sends nothing but the request itself; a search sends the text the
user typed into the Models search box and nothing else (no project, path or conversation data). Pages are parsed with the standard
library. The list is cached in memory for a few hours and the last good copy is kept in the settings table, so the Models window
still shows the library when ollama.com cannot be reached.
"""
from __future__ import annotations

import html
import json
import re
import time

import httpx

BASE = "https://ollama.com"
LIST_URL = f"{BASE}/library"
SEARCH_URL = f"{BASE}/search"
TTL = 6 * 3600
SEARCH_TTL = 600
MIN_LIST = 20                                   # a listing with fewer entries than this is treated as a page we could not read
SIZE_RE = re.compile(r"^e?\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?[kmbt]$", re.I)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,80}(?:/[A-Za-z0-9][A-Za-z0-9._\-]{0,80})?$")
_mem: dict[str, tuple[float, dict]] = {}
_HEADERS = {"User-Agent": "zeus-agent"}


def _text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def count(s: str) -> int:
    """'797.4K' -> 797400, '44.4M' -> 44400000, '1,234' -> 1234, '5' -> 5; 0 when it is not a number."""
    m = re.fullmatch(r"([\d,]+(?:\.\d+)?)\s*([KMB]?)", s.strip(), re.I)
    if not m:
        return 0
    return int(float(m.group(1).replace(",", "")) * {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2).upper()])


def _badges(block: str) -> tuple[list[str], list[str]]:
    caps = [c for c in dict.fromkeys(_text(x) for x in re.findall(r'bg-indigo-50[^>]*>(.*?)</span>', block, re.S)) if c]
    sizes = [s for s in dict.fromkeys(_text(x) for x in re.findall(r'bg-\[#ddf4ff\][^>]*>(.*?)</span>', block, re.S)) if SIZE_RE.match(s)]
    return caps, sizes


def _entry(name: str, description: str, block: str, pulls: str, tags: int | None, updated: str | None) -> dict:
    caps, sizes = _badges(block)
    community = "/" in name
    return {"name": name, "description": description, "capabilities": caps, "sizes": sizes, "pulls": pulls, "pulls_n": count(pulls),
            "tags": tags, "updated": updated, "community": community, "url": f"{BASE}/{name}" if community else f"{LIST_URL}/{name}"}


def parse_library(page: str) -> list[dict]:
    """Entries of the /library page, in the order the site lists them (most popular first). Pure function (tested on a fixture)."""
    out: list[dict] = []
    for block in re.split(r"<li\b", page)[1:]:
        m = re.search(r'href="/library/([A-Za-z0-9._\-]+)"', block)
        if not m:
            continue
        d = re.search(r'<p class="max-w-lg[^"]*">(.*?)</p>', block, re.S)
        pulls = re.search(r"<span\s*>([^<]+)</span>\s*<span class=\"hidden sm:flex\">&nbsp;Pulls", block)
        tags = re.search(r"<span\s*>([\d,]+)</span>\s*<span class=\"hidden sm:flex\">&nbsp;Tags", block)
        upd = re.search(r"Updated&nbsp;</span>\s*<span\s*>([^<]+)</span>", block)
        out.append(_entry(m.group(1), _text(d.group(1)) if d else "", block, pulls.group(1).strip() if pulls else "",
                          int(tags.group(1).replace(",", "")) if tags else None, upd.group(1).strip() if upd else None))
    return out


def parse_search(page: str) -> list[dict]:
    """Results of /search?q=…: official models (`/library/name`) and community models (`/user/name`)."""
    out: list[dict] = []
    for block in re.split(r"<li\b", page)[1:]:
        m = re.search(r'<a href="/([^"]+)" class="group grid', block)
        if not m:
            continue
        path = m.group(1)
        name = path[len("library/"):] if path.startswith("library/") else path
        if not NAME_RE.match(name):
            continue
        d = re.search(r'<p class="mt-1 line-clamp-2[^"]*">(.*?)</p>', block, re.S)
        pulls = re.search(r'title="([\d,]+) downloads"[^>]*>.*?<span\s*>([^<]+)</span>', block, re.S)
        tags = re.search(r">\s*(\d+) tags?\s*</span>", block)
        upd = re.search(r'title="Updated [^"]*">([^<]+)</span>', block)
        out.append(_entry(name, _text(d.group(1)) if d else "", block, pulls.group(2).strip() if pulls else "",
                          int(tags.group(1)) if tags else None, upd.group(1).strip() if upd else None))
    return out


async def _get(url: str, params: dict | None = None) -> str:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=_HEADERS) as c:
        r = await c.get(url, params=params)
    r.raise_for_status()
    return r.text


async def fetch_library(db=None, force: bool = False) -> dict:
    """Every family in the public library: live if possible, else cached, else the last good copy, else empty with an error."""
    now = time.time()
    hit = _mem.get("list")
    if hit and not force and now - hit[0] < TTL:
        return {**hit[1], "source": "cache"}
    err = None
    try:
        models = parse_library(await _get(LIST_URL))
        if len(models) >= MIN_LIST:
            data = {"models": models, "fetched_at": int(now)}
            _mem["list"] = (now, data)
            if db is not None:
                try:
                    db.set_setting("library:list", json.dumps(data))
                except Exception:
                    pass
            return {**data, "source": "live"}
        err = "ollama.com returned a page Zeus-Agent could not read."
    except (httpx.HTTPError, OSError) as e:
        err = f"Could not reach ollama.com ({type(e).__name__})."
    if db is not None:
        try:
            raw = db.get_setting("library:list")
            if raw:
                return {**json.loads(raw), "source": "offline-cache", "error": err}
        except Exception:
            pass
    return {"models": [], "fetched_at": None, "source": "offline", "error": err}


async def search(query: str) -> dict:
    """Search the public library (official and community models). Only the typed text is sent."""
    q = " ".join((query or "").split())[:60]
    if len(q) < 2:
        return {"query": q, "models": [], "source": "none", "error": None}
    now = time.time()
    hit = _mem.get("q:" + q.lower())
    if hit and now - hit[0] < SEARCH_TTL:
        return {**hit[1], "source": "cache"}
    try:
        data = {"query": q, "models": parse_search(await _get(SEARCH_URL, {"q": q}))}
        _mem["q:" + q.lower()] = (now, data)
        return {**data, "source": "live", "error": None}
    except (httpx.HTTPError, OSError) as e:
        return {"query": q, "models": [], "source": "offline", "error": f"Could not reach ollama.com ({type(e).__name__})."}
