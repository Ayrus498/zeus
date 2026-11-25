"""Drives the real terminal app through a pseudo-terminal: type, wait for text, read the screen text."""
from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import termios
import time

from harness import BACKEND, Stack, backend_python, strip_ansi


class Term:
    def __init__(self, stack: Stack, args: list[str], rows=44, cols=150):
        self.buf = ""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(BACKEND)
            env = dict(stack.env, TERM="xterm-256color")
            os.execvpe(backend_python(), [backend_python(), "-m", "app.cli", *args], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def pump(self, seconds: float = 0.5):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                self.buf += strip_ansi(data.decode("utf-8", "replace"))

    def wait_for(self, text: str, timeout=30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if text in self.buf:
                return True
            self.pump(0.4)
        return text in self.buf

    def send(self, line: str, enter=True):
        os.write(self.fd, line.encode() + (b"\r" if enter else b""))

    def screen(self, n=2500) -> str:
        return self.buf[-n:]

    def close(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(self.pid, sig)
                os.waitpid(self.pid, os.WNOHANG)
            except (ProcessLookupError, ChildProcessError):
                break
            time.sleep(0.3)
        try:
            os.close(self.fd)
        except OSError:
            pass
