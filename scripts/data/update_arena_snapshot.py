#!/usr/bin/env python3
"""Rebuild backend/app/modelhub/data/arena_snapshot.json from https://freellmarena.com/data.json (or a local copy).

    python scripts/data/update_arena_snapshot.py [path-or-url]

The data is Arena's public leaderboard (CC BY 4.0, Arena Intelligence) filtered and augmented with parameter counts and architecture
numbers by FreeLLMArena (not affiliated with either). The app also refreshes the same file itself on request (backend/app/modelhub/arena.py,
`normalize`), so this script only refreshes what ships in the package.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.modelhub.arena import normalize  # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else "https://freellmarena.com/data.json"
if src.startswith("http"):
    req = urllib.request.Request(src, headers={"User-Agent": "zeus-agent snapshot builder"})
    raw = urllib.request.urlopen(req, timeout=60).read()
else:
    raw = Path(src).read_bytes()
snap = normalize(json.loads(raw))
out = ROOT / "backend" / "app" / "modelhub" / "data" / "arena_snapshot.json"
out.write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
print(f"{out}: {out.stat().st_size // 1024} KiB, data as of {snap['updated']}, " + ", ".join(f"{c['id']}={len(c['models'])}" for c in snap["categories"]))
