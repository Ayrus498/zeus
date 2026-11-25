#!/usr/bin/env python3
"""A stand-in for llama.cpp's `llama-server` used by the tests: same command line and HTTP endpoints, scripted answers.

Behaviour is chosen by the last user message: "TOOL" streams a tool call, "THINK" streams reasoning first, "ERROR" returns HTTP 500,
"SLOW" streams slowly (for tests that overlap requests); anything else streams "Hello from fake llama". Environment:
FAKE_LLAMA_LOG (append one JSON line per request and per start), FAKE_LLAMA_DELAY (seconds before /health is ready),
FAKE_LLAMA_STRICT (answer 400 like Gemma's chat template does for a system role, tool messages or non-alternating turns).
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

args = sys.argv[1:]
if "--version" in args:
    print("version: 12345 (fakebuild)", file=sys.stderr)
    sys.exit(0)
if "--help" in args or "-h" in args:
    fit = "" if os.environ.get("FAKE_LLAMA_NOFIT") else "  --fit [on|off]   fit the model to memory\n"
    print("usage: llama-server [options]\n  --jinja   use jinja templating\n" + fit + "  --flash-attn [on|off|auto]\n  -ngl N")
    sys.exit(0)
if "--list-devices" in args:
    print("Available devices:\n  CUDA0: NVIDIA Fake GPU (12000 MiB, 11000 MiB free)")
    sys.exit(0)


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default


port = int(opt("--port", "8080"))
LOG = os.environ.get("FAKE_LLAMA_LOG")
READY_AT = time.time() + float(os.environ.get("FAKE_LLAMA_DELAY", "0"))


def log(obj):
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps(obj) + "\n")


log({"event": "start", "argv": args})


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            if time.time() < READY_AT:
                return self._json(503, {"error": {"message": "Loading model"}})
            return self._json(200, {"status": "ok"})
        self._json(404, {"error": "not found"})

    def _sse(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        log({"event": "chat", "body": body})
        if os.environ.get("FAKE_LLAMA_STRICT"):                  # a Gemma-style template: no system role, no tool role, user/assistant strictly alternating
            roles = [m["role"] for m in body["messages"]]
            bad = ("system" in roles or "tool" in roles or any(m.get("tool_calls") for m in body["messages"])
                   or any(a == b for a, b in zip(roles, roles[1:])) or (roles and roles[0] != "user"))
            if bad:
                return self._json(400, {"error": {"code": 400, "message": "Unable to generate parser for this template. Jinja Exception: Conversation roles must alternate user/assistant/user/assistant/..."}})
        last = next((m["content"] for m in reversed(body["messages"]) if m["role"] == "user"), "")
        if "ERROR" in last:
            return self._json(500, {"error": {"message": "the model exploded"}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = lambda delta, finish=None: {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}  # noqa: E731
        usage = {"usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}, "timings": {"predicted_ms": 70.0, "predicted_n": 7, "prompt_n": 11},
                 "choices": []}
        if "TOOL" in last and body.get("tools") and body["messages"][-1]["role"] != "tool":          # after the tool result it just answers
            self._sse(chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "x", "type": "function", "function": {"name": "create_file", "arguments": ""}}]}))
            for piece in ('{"path": "hel', 'lo.py", "content"', ': "print(1)"}'):
                self._sse(chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}))
            self._sse(chunk({}, "tool_calls"))
        else:
            if "THINK" in last:
                for t in ("Let me ", "think."):
                    self._sse(chunk({"reasoning_content": t}))
            for t in ("Hello", " from", " fake llama"):
                self._sse(chunk({"content": t}))
                if "SLOW" in last:
                    time.sleep(0.4)
            self._sse(chunk({}, "stop"))
        self._sse(usage)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
