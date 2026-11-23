"""HTTP/WebSocket client for the Zeus-Agent backend, plus starting the backend when nothing is listening."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

DEFAULT_PORT = int(os.environ.get("ZEUS_PORT", "8765"))


class ApiError(Exception):
    pass


def listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def start_backend(port: int = DEFAULT_PORT, log: Path | None = None) -> subprocess.Popen | None:
    """Start the backend in this interpreter's environment unless one is already listening (e.g. the desktop app)."""
    if listening(port):
        return None
    log = log or (Path(os.environ.get("ZEUS_DATA_DIR", Path.home() / ".local" / "share" / "zeus")) / "cli-backend.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ZEUS_PORT": str(port)}
    pkg_root = str(Path(__file__).resolve().parents[2])      # directory containing the `app` package
    env["PYTHONPATH"] = pkg_root + os.pathsep + env.get("PYTHONPATH", "")
    fh = open(log, "ab")
    proc = subprocess.Popen([sys.executable, "-m", "app"], cwd=pkg_root, env=env, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
                            start_new_session=True)
    for _ in range(150):
        if listening(port):
            return proc
        if proc.poll() is not None:
            raise ApiError(f"The Zeus-Agent backend exited immediately (see {log}).")
        time.sleep(0.1)
    proc.terminate()
    raise ApiError(f"The Zeus-Agent backend did not start within 15 s (see {log}).")


def stop_backend(proc: subprocess.Popen | None) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class Api:
    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(60.0, connect=5.0))

    async def close(self):
        await self.http.aclose()

    async def req(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None, content: bytes | None = None) -> Any:
        try:
            r = await self.http.request(method, path, json=json_body, params=params, content=content)
        except httpx.HTTPError as e:
            raise ApiError(f"cannot reach the Zeus-Agent backend: {type(e).__name__}") from e
        if r.status_code >= 400:
            try:
                msg = r.json().get("detail", r.text)
            except Exception:
                msg = r.text
            raise ApiError(str(msg))
        return r.json() if r.content else None

    get = lambda self, p, **q: self.req("GET", p, params=q or None)  # noqa: E731
    post = lambda self, p, body=None: self.req("POST", p, json_body=body)  # noqa: E731
    put = lambda self, p, body=None: self.req("PUT", p, json_body=body)  # noqa: E731
    patch = lambda self, p, body=None: self.req("PATCH", p, json_body=body)  # noqa: E731

    async def events(self, sid: int, outgoing: dict) -> AsyncIterator[dict]:
        """Connect to the session socket, send `outgoing`, and yield events until the run ends. `.stop()` via the returned socket."""
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{self.port}/ws/sessions/{sid}", max_size=32 * 2**20, ping_interval=20) as ws:
            self.ws = ws
            await ws.send(json.dumps(outgoing))
            async for raw in ws:
                ev = json.loads(raw)
                yield ev
                if ev.get("type") == "run_end":
                    return

    async def stop(self):
        ws = getattr(self, "ws", None)
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "stop"}))
            except Exception:
                pass

    async def respond(self, req_id: str, decision: str):
        ws = getattr(self, "ws", None)
        if ws is not None:
            await ws.send(json.dumps({"type": "permission_response", "id": req_id, "decision": decision}))
