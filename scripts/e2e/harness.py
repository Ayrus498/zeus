"""Builds a throw-away Zeus-Agent stack for the end-to-end suite and tears it down again.

Everything is isolated: a temp ZEUS_HOME / ZEUS_DATA_DIR, free ports, a fake Ollama, the fake llama-server from the backend tests, GGUF
fixtures, a project folder, and the frontend built into a temp folder against this backend's port. Child processes are recorded by pid and
only those are stopped. The owner's ~/.zeus, database and installed app (port 8765) are never touched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(BACKEND / "tests"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def backend_python() -> str:
    for c in (os.environ.get("ZEUS_E2E_BACKEND_PYTHON", ""), str(BACKEND / ".venv" / "bin" / "python"), sys.executable):
        if c and Path(c).exists():
            return c
    raise RuntimeError("no Python for the backend: create backend/.venv")


def http(method: str, url: str, body=None, timeout=15):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


class Stack:
    def __init__(self, workdir: Path | None = None, build_ui: bool = True):
        self.tmp = Path(workdir or tempfile.mkdtemp(prefix="zeus-e2e-"))
        self.home, self.data, self.project_dir = self.tmp / "home", self.tmp / "data", self.tmp / "proj"
        self.models_dir, self.dist = self.tmp / "gguf", self.tmp / "dist"
        for d in (self.home, self.data, self.project_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.api_port, self.ollama_port, self.ui_port, self.hf_port = free_port(), free_port(), free_port(), free_port()
        self.api = f"http://127.0.0.1:{self.api_port}"
        self.ui = f"http://127.0.0.1:{self.ui_port}"
        self.build_ui = build_ui
        self.procs: list[subprocess.Popen] = []
        self._ui_server: ThreadingHTTPServer | None = None
        self.user_home = self.tmp / "userhome"                    # the stack's own HOME: what the app searches for llama.cpp is this folder, never the real one
        self.user_home.mkdir(parents=True, exist_ok=True)
        self.ollama_store = self.tmp / "ollama-store"             # Ollama's model files (read only for Zeus-Agent); OLLAMA_MODELS makes it THE store, never the machine's real one
        self.hf_log = self.tmp / "hf-requests.jsonl"
        self.env = dict(os.environ, HOME=str(self.user_home), OLLAMA_MODELS=str(self.ollama_store), ZEUS_HF_BASE=f"http://127.0.0.1:{self.hf_port}", FAKE_HF_LOG=str(self.hf_log), ZEUS_HOME=str(self.home), ZEUS_DATA_DIR=str(self.data), ZEUS_PORT=str(self.api_port),
                        ZEUS_OLLAMA_URL=f"http://127.0.0.1:{self.ollama_port}", PYTHONUNBUFFERED="1", NO_COLOR="")
        self.ollama_log = self.tmp / "ollama-requests.jsonl"
        self.env["FAKE_OLLAMA_LOG"] = str(self.ollama_log)
        self.project: dict = {}

    # ------------------------------------------------------------------ lifecycle
    def _spawn(self, argv, name, cwd=None, env=None) -> subprocess.Popen:
        log = open(self.tmp / f"{name}.log", "wb")
        p = subprocess.Popen(argv, cwd=cwd, env=env or self.env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(p)
        return p

    def _wait(self, url: str, what: str, timeout=40):
        end = time.time() + timeout
        while time.time() < end:
            try:
                urllib.request.urlopen(url, timeout=2).read()
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError(f"{what} did not start within {timeout}s (see {self.tmp})")

    def start(self) -> "Stack":
        from gguf_util import make_gguf
        make_gguf(self.models_dir / "e2e-coder-q4_k_m.gguf", name="E2E Coder", file_type=15)
        make_gguf(self.models_dir / "e2e-chat-q8_0.gguf", name="E2E Chat", file_type=7, template="{{ messages }}")
        # a llama.cpp "release" unpacked in Downloads, with nothing on PATH: a real ELF program (a copy of `true`) with libraries beside it
        rel = self.user_home / "Downloads" / "llama-b99999-bin-ubuntu-x64" / "llama-b99999"
        rel.mkdir(parents=True)
        shutil.copy2(shutil.which("true") or "/bin/true", rel / "llama-server")
        (rel / "libllama.so").write_bytes(b"x")
        (rel / "libggml.so").write_bytes(b"x")
        self.discovered_llama = rel / "llama-server"
        # a model that Ollama "already downloaded": manifest + a real GGUF blob, as in ~/.ollama/models
        hexd = "e2" * 32
        (self.ollama_store / "blobs").mkdir(parents=True)
        blob = make_gguf(self.ollama_store / "blobs" / f"sha256-{hexd}", name="E2E Ollama", pad=2 * 2**20)
        mf = self.ollama_store / "manifests" / "registry.ollama.ai" / "library" / "e2e-ollama" / "1b"
        mf.parent.mkdir(parents=True)
        mf.write_text(json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{hexd}", "size": blob.stat().st_size}]}))
        self.ollama_blob = blob
        payload = make_gguf(self.tmp / "hf-payload.gguf", name="E2E Tiny", file_type=15, pad=4096)
        self._spawn([sys.executable, str(Path(__file__).parent / "fake_hf.py"), str(self.hf_port), str(payload)], "fake-hf")
        self._wait(f"http://127.0.0.1:{self.hf_port}/api/models", "fake Hugging Face")
        fake_llama = BACKEND / "tests" / "fixtures" / "fake_llama_server.py"
        fake_llama.chmod(fake_llama.stat().st_mode | 0o111)
        self._spawn([sys.executable, str(Path(__file__).parent / "fake_ollama.py"), str(self.ollama_port)], "fake-ollama")
        self._wait(f"http://127.0.0.1:{self.ollama_port}/api/version", "fake Ollama")
        # settings.json is read by the backend at startup: llama.cpp points at the fake server and the GGUF folder
        (self.home / "settings.json").write_text(json.dumps({"llamacpp": {"binary": str(fake_llama), "models_dirs": [str(self.models_dir)]}, "engine": "ollama"}))
        self._spawn([backend_python(), "-m", "app"], "backend", cwd=str(BACKEND))
        self._wait(f"{self.api}/api/health", "the backend")
        self.project = http("POST", f"{self.api}/api/projects", {"path": str(self.project_dir)})
        http("PUT", f"{self.api}/api/projects/{self.project['id']}/permissions", {"mode": "trusted", "custom": {}})
        if self.build_ui:
            self._build_and_serve_ui()
        return self

    def _build_and_serve_ui(self):
        env = dict(os.environ, VITE_ZEUS_API=self.api)
        vite = FRONTEND / "node_modules" / ".bin" / "vite"
        if not vite.exists():
            raise RuntimeError("frontend dependencies missing: run `npm ci` in frontend/")
        r = subprocess.run([str(vite), "build", "--outDir", str(self.dist), "--emptyOutDir"], cwd=FRONTEND, env=env, capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise RuntimeError("frontend build failed:\n" + (r.stdout + r.stderr)[-2000:])
        handler = partial(type("Quiet", (SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=str(self.dist))
        self._ui_server = ThreadingHTTPServer(("127.0.0.1", self.ui_port), handler)
        threading.Thread(target=self._ui_server.serve_forever, daemon=True).start()

    def stop(self, keep: bool = False):
        if self._ui_server:
            self._ui_server.shutdown()
        for p in reversed(self.procs):                   # only the processes this stack started, with their children (llama-server)
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        for p in self.procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if not keep:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # ------------------------------------------------------------------ helpers
    def get(self, path: str):
        return http("GET", self.api + path)

    def state(self) -> dict:
        f = self.home / "state.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def settings(self) -> dict:
        f = self.home / "settings.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def hf_requests(self) -> list[dict]:
        return [json.loads(l) for l in self.hf_log.read_text().splitlines()] if self.hf_log.exists() else []

    def ollama_requests(self) -> list[list[dict]]:
        """The messages of every chat request the fake Ollama received, oldest first."""
        return [json.loads(l)["messages"] for l in self.ollama_log.read_text().splitlines()] if self.ollama_log.exists() else []

    def log_tail(self, name="backend", n=40) -> str:
        f = self.tmp / f"{name}.log"
        return "\n".join(f.read_text(errors="replace").splitlines()[-n:]) if f.exists() else ""


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[=>()][A-Za-z0-9]?|\r")


def strip_ansi(s: str) -> str:
    return ANSI.sub("", s)
