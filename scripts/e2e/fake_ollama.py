#!/usr/bin/env python3
"""A stand-in for Ollama used by the end-to-end suite: the endpoints Zeus-Agent calls, with scripted answers (no model, no GPU).

Chosen by the last user message: "CREATE" streams a create_file tool call (hello.py), anything else streams "Hello from fake ollama".
After a tool result it answers "Done." Usage: fake_ollama.py PORT
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "fake-coder:1b"
TAGS = {"models": [{"name": MODEL, "size": 800_000_000, "modified_at": "2026-01-01T00:00:00Z",
                    "details": {"family": "qwen2", "parameter_size": "1B", "quantization_level": "Q4_K_M"}}]}
SHOW = {"model_info": {"general.architecture": "qwen2", "qwen2.context_length": 32768, "qwen2.block_count": 24, "qwen2.attention.head_count": 14,
                       "qwen2.attention.head_count_kv": 2, "qwen2.embedding_length": 896, "qwen2.attention.key_length": 64},
        "capabilities": ["completion", "tools"], "template": "{{ .Prompt }}", "parameters": "", "details": TAGS["models"][0]["details"]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/version":
            return self._json({"version": "0.0.0-fake"})
        if self.path == "/api/tags":
            return self._json(TAGS)
        if self.path == "/api/ps":
            return self._json({"models": []})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/show":
            return self._json(SHOW)
        if self.path != "/api/chat":
            return self._json({"error": "not found"}, 404)
        msgs = body.get("messages", [])
        if os.environ.get("FAKE_OLLAMA_LOG"):                       # one line per request: lets a test check what the model was really sent
            with open(os.environ["FAKE_OLLAMA_LOG"], "a") as f:
                f.write(json.dumps({"messages": msgs}) + "\n")
        last = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        out = lambda o: (self.wfile.write(json.dumps(o).encode() + b"\n"), self.wfile.flush())  # noqa: E731
        if msgs and msgs[-1]["role"] == "tool":
            out({"message": {"role": "assistant", "content": "Done."}, "done": False})
        elif "CREATE" in last and body.get("tools"):
            out({"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "create_file", "arguments": {
                "path": "hello.py", "content": "print('hi from e2e')\n", "reason": "e2e"}}}]}, "done": False})
        else:
            for t in ("Hello", " from", " fake ollama"):
                out({"message": {"role": "assistant", "content": t}, "done": False})
        out({"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 11, "eval_count": 7, "eval_duration": 70_000_000})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
