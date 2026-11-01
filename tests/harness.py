"""A headless outer terminal for tests and benchmarks.

`Term` runs a command (normally `pys attach`) in a real pty and feeds what it
prints into a pyte screen, so a test sees what a person would see.
"""

from __future__ import annotations

import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from pathlib import Path

import pyte

PYS = [sys.executable, "-m", "py_screen"]


def session_dir(tag: str = "t") -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/pys-{os.getuid()}"
    return Path(base) / f"pys-{tag}-{uuid.uuid4().hex[:8]}"


class Session:
    """Environment for one isolated py-screen server (its own socket dir)."""

    def __init__(self, tag: str = "t", shell: str = "/bin/sh") -> None:
        self.dir = session_dir(tag)
        self.env = dict(os.environ, PYS_DIR=str(self.dir), SHELL=shell,
                        TERM="xterm-256color", PS1="$ ", PYS_PREFIX="C-]",
                        PYS_CONFIG="/nonexistent")
        self.env.pop("PROMPT_COMMAND", None)

    def pys(self, *args: str, check: bool = True) -> str:
        r = subprocess.run(PYS + list(args), env=self.env, capture_output=True,
                           text=True, timeout=15)
        if check and r.returncode:
            raise RuntimeError(f"pys {args}: {r.stderr}")
        return r.stdout

    def kill(self) -> None:
        self.pys("kill", check=False)
        time.sleep(0.2)
        shutil.rmtree(self.dir, ignore_errors=True)


class Term:
    def __init__(self, argv: list[str], env: dict, cols: int = 100, rows: int = 30,
                 emulate: bool = True) -> None:
        self.cols, self.rows = cols, rows
        self.screen = pyte.Screen(cols, rows) if emulate else None
        self.stream = pyte.ByteStream(self.screen) if emulate else None
        self.raw = bytearray()
        self.nbytes = 0
        self.lock = threading.Lock()
        self.on_data = None                      # optional callback(bytes, t)
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(argv[0], argv, env)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.alive = True
        self.thread = threading.Thread(target=self.pump, daemon=True)
        self.thread.start()

    def pump(self) -> None:
        while True:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.5)
                if not r:
                    if not self.alive:
                        return
                    continue
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if not data:
                self.alive = False
                return
            t = time.monotonic()
            with self.lock:
                self.nbytes += len(data)
                if self.on_data:
                    self.on_data(data, t)
                if self.stream is not None:
                    self.stream.feed(data)
                if len(self.raw) < 1 << 20:
                    self.raw += data

    def send(self, s: str | bytes) -> None:
        os.write(self.fd, s.encode() if isinstance(s, str) else s)

    def text(self) -> str:
        with self.lock:
            return "\n".join(self.screen.display) if self.screen else ""

    def wait_for(self, needle: str, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return
            time.sleep(0.02)
        raise AssertionError(f"{needle!r} not on screen after {timeout}s:\n{self.text()}")

    def wait_until(self, pred, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred(self.text()):
                return
            time.sleep(0.02)
        raise AssertionError(f"condition not met after {timeout}s:\n{self.text()}")

    def wait_exit(self, timeout: float = 10.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.02)
        os.kill(self.pid, signal.SIGKILL)
        raise AssertionError("client did not exit")

    def close(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(self.fd)
