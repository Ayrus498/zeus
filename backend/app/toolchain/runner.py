"""Controlled subprocess execution with streaming, timeout and cancellation."""
import asyncio
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Awaitable, Callable

from ..core.config import DATA_DIR, MAX_OUTPUT_CHARS

OnChunk = Callable[[str, str], Awaitable[None]]  # (stream, text)


class RunResult(dict):
    pass


def shim_dir() -> str | None:
    """Debian/Ubuntu ship `python3` only. Models habitually write `python`, so expose a shim
    (appended to PATH, never shadowing a real `python`)."""
    if shutil.which("python"):
        return None
    py3 = shutil.which("python3")
    if not py3:
        return None
    d = Path(DATA_DIR) / "shims"
    try:
        d.mkdir(parents=True, exist_ok=True)
        link = d / "python"
        if not link.exists():
            link.symlink_to(py3)
        return str(d)
    except OSError:
        return None


class Runner:
    """Tracks running processes by id so they can be cancelled."""

    def __init__(self):
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.cancelled: set[str] = set()

    async def run(self, run_id: str, command: str, cwd: str, timeout: int, on_chunk: OnChunk | None = None) -> RunResult:
        start = time.time()
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME")}
        env["PYTHONUNBUFFERED"] = "1"
        shim = shim_dir()
        if shim:
            env["PATH"] = env.get("PATH", "") + os.pathsep + shim
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self.procs[run_id] = proc
        buf: list[str] = []
        size = 0

        async def pump(stream, name):
            nonlocal size
            while True:
                data = await stream.read(4096)
                if not data:
                    return
                text = data.decode(errors="replace")
                if size < MAX_OUTPUT_CHARS * 4:
                    buf.append(text if name == "stdout" else text)
                    size += len(text)
                if on_chunk:
                    await on_chunk(name, text)

        pumps = asyncio.gather(pump(proc.stdout, "stdout"), pump(proc.stderr, "stderr"))
        status = "completed"
        try:
            await asyncio.wait_for(asyncio.shield(pumps), timeout)
            await proc.wait()
        except asyncio.TimeoutError:
            status = "timeout"
            self._kill(proc)
            await proc.wait()
        except asyncio.CancelledError:
            self._kill(proc)
            raise
        finally:
            self.procs.pop(run_id, None)
        if run_id in self.cancelled:
            self.cancelled.discard(run_id)
            status = "cancelled"
        try:
            await asyncio.wait_for(pumps, 2)
        except Exception:
            pass
        out = "".join(buf)
        return RunResult(exit_code=proc.returncode, status=status, output=out, duration=round(time.time() - start, 2))

    def cancel(self, run_id: str) -> bool:
        p = self.procs.get(run_id)
        if not p:
            return False
        self.cancelled.add(run_id)
        self._kill(p)
        return True

    def cancel_all(self):
        for rid in list(self.procs):
            self.cancel(rid)

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [{len(text) - limit} chars truncated] ...\n" + text[-half:]
