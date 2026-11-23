#!/usr/bin/env python3
"""Real end-to-end demo against a running Zeus-Agent backend and a real Ollama model (no mocks).

An auto-approver stands in for the human clicking "Approve"; every request is printed.
Usage: e2e_demo.py [--model qwen2.5-coder:7b] [--parent /tmp/dir] [--name zeus-notes] [--url http://127.0.0.1:8765]
"""
import argparse, asyncio, json, sys, urllib.request

import websockets

PROMPT = ("Create a simple FastAPI-based notes application with SQLite. Implement create, read, update and delete "
          "operations. Include a requirements.txt, README.md and unit tests.")


def http(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5-coder:7b"); ap.add_argument("--parent", required=True)
    ap.add_argument("--name", default="zeus-notes"); ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--ctx", type=int, default=16384); ap.add_argument("--session", type=int)
    ap.add_argument("--prompt", default=PROMPT); ap.add_argument("--mode", default="approval")
    a = ap.parse_args()
    if a.session:
        sess = http(f"{a.url}/api/sessions/{a.session}") if False else [s for s in http(f"{a.url}/api/sessions") if s["id"] == a.session][0]
        pid = sess["project_id"]
    else:
        proj = http(f"{a.url}/api/projects/create", "POST", {"parent": a.parent, "name": a.name})
        pid = proj["id"]
        sess = http(f"{a.url}/api/sessions", "POST", {"project_id": pid, "model": a.model, "num_ctx": a.ctx})
    http(f"{a.url}/api/projects/{pid}/permissions", "PUT", {"mode": a.mode})
    print(f"project={pid} session={sess['id']} model={sess['model']} mode={a.mode}")
    ws_url = a.url.replace("http", "ws") + f"/ws/sessions/{sess['id']}"
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "prompt", "content": a.prompt}))
        async for raw in ws:
            e = json.loads(raw); t = e["type"]
            if t == "token": sys.stdout.write(e["text"]); sys.stdout.flush()
            elif t == "permission_request":
                tgt = e["target"].replace("\n", " ")[:90]
                print(f"\n[APPROVAL] {e['action']} :: {tgt}  (reason: {e['reason'][:70]})")
                await ws.send(json.dumps({"type": "permission_response", "id": e["id"], "decision": "once"}))
            elif t == "tool_call": print(f"\n[TOOL] {e['name']} {json.dumps(e['args'])[:110]}")
            elif t == "tool_result": print(f"[RESULT ok={e['ok']}] {e['output'][:160]!r}")
            elif t == "usage": print(f"\n[USAGE] in={e['prompt_tokens']} out={e['output_tokens']} tps={e['tokens_per_sec']}")
            elif t == "error": print("\n[ERROR]", e["message"])
            elif t == "run_end": print("\n[RUN END]", e["status"]); break


asyncio.run(main())
