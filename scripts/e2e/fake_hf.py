#!/usr/bin/env python3
"""A stand-in for huggingface.co used by the end-to-end suite: model search, a file listing and the file download. Usage: fake_hf.py PORT PAYLOAD.gguf

Every request is appended to $FAKE_HF_LOG (path and query), so a test can check what the app sends: only the text typed into the search box.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT, PAYLOAD = int(sys.argv[1]), open(sys.argv[2], "rb").read()
REPOS = [{"id": "e2e/tiny-GGUF", "downloads": 1_200_000, "likes": 321, "lastModified": "2026-10-01"}, {"id": "e2e/other-GGUF", "downloads": 5000, "likes": 4, "lastModified": "2026-09-01"}]
SIBLINGS = [{"rfilename": "tiny-Q4_K_M.gguf", "size": len(PAYLOAD)}, {"rfilename": "tiny-Q8_0.gguf", "size": 400_000_000}, {"rfilename": "tiny-F16.gguf", "size": 90_000_000_000},
            {"rfilename": "tiny-00001-of-00002.gguf", "size": 1000}, {"rfilename": "tiny-00002-of-00002.gguf", "size": 1000}, {"rfilename": "README.md", "size": 10}]


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
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if os.environ.get("FAKE_HF_LOG"):
            with open(os.environ["FAKE_HF_LOG"], "a") as f:
                f.write(json.dumps({"path": u.path, "query": q, "headers": {k.lower(): v for k, v in self.headers.items() if k.lower() in ("user-agent", "cookie", "authorization")}}) + "\n")
        if u.path == "/api/models":
            s = q.get("search", "").lower()
            return self._json([r for r in REPOS if s in r["id"].lower()])
        if u.path.startswith("/api/models/"):
            repo = u.path[len("/api/models/"):]
            return self._json({"siblings": SIBLINGS}) if repo in [r["id"] for r in REPOS] else self._json({"error": "not found"}, 404)
        if "/resolve/main/" in u.path and u.path.endswith("tiny-Q4_K_M.gguf"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return
        self._json({"error": "not found"}, 404)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
