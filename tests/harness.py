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
BSU, ESU = b"\x1b[?2026h", b"\x1b[?2026l"   # begin / end synchronized update
SYNC_MAX = 1.0                               # an update left open shows after this (s)


def session_dir(tag: str = "t") -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/pys-{os.getuid()}"
    return Path(base) / f"pys-{tag}-{uuid.uuid4().hex[:8]}"


class Session:
    """Environment for one isolated py-screen server (its own socket dir)."""

    def __init__(self, tag: str = "t", shell: str = "/bin/sh") -> None:
        self.dir = session_dir(tag)
        self.env = dict(os.environ, PYS_DIR=str(self.dir),
                        XDG_STATE_HOME=str(self.dir / "state"), SHELL=shell,
                        TERM="xterm-256color", PS1="$ ", PYS_PREFIX="C-]",
                        PYS_CONFIG="/nonexistent")
        self.env.pop("PROMPT_COMMAND", None)
        for k in ("PYS_WINDOW", "PYS_SESSION"):   # tests may run inside a pys window
            self.env.pop(k, None)

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
        self.held = bytearray()                  # an update not shown yet (see show())
        self.sync_since: float | None = None     # inside a synchronized update since then
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(argv[0], argv, env)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.alive = True
        self.closing = False
        self.thread = threading.Thread(target=self.pump, daemon=True)
        self.thread.start()

    def pump(self) -> None:
        while not self.closing:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.1 if self.held else 0.5)
                if not r:
                    if self.held:
                        with self.lock:
                            self.show(b"")       # an update left open: shown after SYNC_MAX
                    if not self.alive:
                        return
                    continue
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if not data:
                self.alive = False
                if self.held:
                    with self.lock:
                        self.show(b"", flush=True)   # an update cut off by the exit
                return
            t = time.monotonic()
            with self.lock:
                self.nbytes += len(data)
                if self.on_data:
                    self.on_data(data, t)
                if self.stream is not None:
                    self.show(data)
                if len(self.raw) < 1 << 20:
                    self.raw += data

    def show(self, data: bytes, flush: bool = False) -> None:
        """Feed the screen as a terminal that knows synchronized output (mode 2026, as
        kitty, iTerm2, WezTerm, foot, Windows Terminal do) shows it: what comes between
        ESC[?2026h and ESC[?2026l reaches the screen whole, at the ESC[?2026l. pys sends
        every frame that way. Fed piece by piece as the pty hands it over, a test could
        read a frame half drawn: a whole redraw starts with ESC[2J, so between two reads
        the status line is blank and a wait for some text to be gone from the screen
        passes before the frame that removes it (the test_attach_detach_reattach flake:
        it saw no "ended:" on the half-drawn frame that brings it, typed "exit" into
        the key hold, and the client never exited). Like a terminal, an update left open
        longer than SYNC_MAX is shown anyway. Called with self.lock held."""
        buf = self.held + data
        i = 0
        while True:
            if self.sync_since is None:
                j = buf.find(BSU, i)
                if j < 0:
                    break
                self.stream.feed(bytes(buf[i:j]))
                i, self.sync_since = j, time.monotonic()
            else:
                j = buf.find(ESU, i)
                if j < 0:
                    break
                self.stream.feed(bytes(buf[i:j + len(ESU)]))
                i, self.sync_since = j + len(ESU), None
        rest = buf[i:]
        if flush or (self.sync_since is not None
                     and time.monotonic() - self.sync_since > SYNC_MAX):
            self.stream.feed(bytes(rest))
            rest, self.sync_since = bytearray(), None
        elif self.sync_since is None:            # keep a cut ESC[?2026h for the next read
            k = next((k for k in range(min(len(rest), len(BSU) - 1), 0, -1)
                      if BSU.startswith(rest[-k:])), 0)
            self.stream.feed(bytes(rest[:len(rest) - k]))
            rest = rest[len(rest) - k:]
        self.held = rest

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
            if pid:                               # and what it printed last is read: the
                self.thread.join(max(0.0, deadline - time.monotonic()))   # pump stops at EOF
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.02)
        os.kill(self.pid, signal.SIGKILL)
        raise AssertionError("client did not exit")

    def close(self) -> None:
        """Kill the command, stop the pump, then free the pty. In that order: the kernel
        hands the next open() the lowest free fd number, so a pump still reading
        self.fd after os.close() reads whatever gets that number next, e.g. the stdout
        pipe of the test's next `pys ls`, whose JSON then never reaches the test (the
        test_two_clients flake: exit 0, empty output)."""
        self.closing = True
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        self.thread.join(5)                       # select wakes every 0.5 s at most
        if self.thread.is_alive():
            raise RuntimeError("pump thread didn't stop; not closing its fd under it")
        os.close(self.fd)
