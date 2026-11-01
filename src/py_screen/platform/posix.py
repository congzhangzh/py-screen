"""Linux and macOS: ptys from the `pty` module, Unix sockets, setsid, termios."""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import fcntl
import os
import pty
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Callable

# ---- ptys -------------------------------------------------------------------


class PosixPty:
    """One child in a pseudo terminal; the keeper is the only reader of its master.

    Contract (the Windows Pty follows it too):
    - `start(on_data, on_exit)`: from now on `on_data(bytes)` gets output and
      `on_exit(code)` is called once when the child is gone. Both run on the loop.
    - `set_reading(bool)`: stop or resume reading (credit and back-pressure); idempotent.
      While not reading, output waits in the kernel and the child blocks once it is full.
    - `write(bytes)`, `resize(cols, rows)`, `hangup()` (the child's session gets SIGHUP).
    """

    def __init__(self, cmd: list[str], cwd: str | None, env: dict, cols: int, rows: int):
        pid, fd = pty.fork()                       # child: setsid + pty slave as ctty
        if pid == 0:
            try:
                if cwd:
                    os.chdir(cwd)
                os.execvpe(cmd[0], cmd, env)
            finally:
                os._exit(127)
        os.set_blocking(fd, False)
        self.pid, self.fd = pid, fd
        self.reading = False
        self.closed = False
        self.on_data: Callable[[bytes], None] = lambda b: None
        self.on_exit: Callable[[int | None], None] = lambda c: None
        self.resize(cols, rows)

    def start(self, on_data: Callable[[bytes], None],
              on_exit: Callable[[int | None], None]) -> None:
        self.on_data, self.on_exit = on_data, on_exit

    def set_reading(self, on: bool) -> None:
        if self.closed or on == self.reading:
            return
        self.reading = on
        loop = asyncio.get_running_loop()
        if on:
            loop.add_reader(self.fd, self._readable)
        else:
            loop.remove_reader(self.fd)

    def _readable(self) -> None:
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:                            # EIO: the slave side is gone
            data = b""
        if data:
            self.on_data(data)
            return
        self.set_reading(False)
        self.closed = True
        os.close(self.fd)
        try:
            _, status = os.waitpid(self.pid, 0)
            code: int | None = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            code = None
        self.on_exit(code)

    def write(self, data: bytes) -> None:
        # Small writes (keys, pastes); a full pty input queue raises BlockingIOError.
        view = memoryview(data)
        while view and not self.closed:
            try:
                view = view[os.write(self.fd, view):]
            except BlockingIOError:
                time.sleep(0.001)
            except OSError:
                return

    def resize(self, cols: int, rows: int) -> None:
        if not self.closed:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def hangup(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass


def spawn_pty(cmd: list[str], cwd: str | None, env: dict, cols: int, rows: int) -> PosixPty:
    return PosixPty(cmd, cwd, env, cols, rows)


# ---- IPC: Unix sockets ------------------------------------------------------------


async def serve(path: Path, cb, limit: int = 1 << 16) -> asyncio.AbstractServer:
    path.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(cb, str(path), limit=limit)
    os.chmod(path, 0o600)
    return server


async def connect(path: Path, limit: int = 1 << 16):
    return await asyncio.open_unix_connection(str(path), limit=limit)


def alive(path: Path) -> bool:
    s = socket.socket(socket.AF_UNIX)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def cleanup(path: Path) -> None:
    path.unlink(missing_ok=True)


def runtime_base() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/pys-{os.getuid()}"
    return Path(base)


def check_path(p: Path) -> None:
    # sockaddr_un.sun_path is 108 bytes on Linux, 104 on macOS, NUL included.
    if len(str(p / "keeper.sock").encode()) > 103:
        raise SystemExit(f"pys: socket dir too long for AF_UNIX (104-byte sun_path): {p}")


# ---- processes ----------------------------------------------------------------


def start_detached(argv: list[str], log: Path) -> None:
    with open(log, "ab") as logf:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                         start_new_session=True, cwd="/")


def on_stop(loop: asyncio.AbstractEventLoop, cb: Callable[[], None]) -> None:
    """SIGTERM/SIGINT stop the keeper; SIGHUP (the terminal went away) is ignored."""
    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, cb)
    loop.add_signal_handler(signal.SIGHUP, lambda: None)


def default_shell() -> str:
    return os.environ.get("SHELL") or "/bin/sh"


def loadavg() -> float | None:
    return os.getloadavg()[0]


_libproc = None


def _macos_cwd(pid: int) -> str | None:
    # proc_pidinfo(PROC_PIDVNODEPATHINFO): struct proc_vnodepathinfo is two
    # vnode_info_path {vnode_info (152 bytes); char path[1024]}; cwd is the first.
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib",
                               use_errno=True)
    buf = ctypes.create_string_buffer(2 * (152 + 1024))
    n = _libproc.proc_pidinfo(pid, 9, ctypes.c_uint64(0), buf, ctypes.sizeof(buf))
    if n <= 0:
        return None
    path = buf.raw[152:152 + 1024].split(b"\0", 1)[0]
    return os.fsdecode(path) or None


def proc_cwd(pid: int) -> str | None:
    try:
        if sys.platform == "darwin":
            return _macos_cwd(pid)
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


# ---- the attached terminal ---------------------------------------------------------


class RawTerm:
    """The client's own terminal: raw keys in, resize events, restore on exit."""

    def __init__(self) -> None:
        self.fd_in, self.fd_out = sys.stdin.fileno(), sys.stdout.fileno()
        self.saved: list | None = None

    def size(self) -> tuple[int, int]:
        s = shutil.get_terminal_size()
        return s.columns, s.lines

    def enter(self) -> None:
        self.saved = termios.tcgetattr(self.fd_in)
        tty.setraw(self.fd_in)

    def start(self, on_keys: Callable[[bytes], None], on_resize: Callable[[], None]) -> None:
        loop = asyncio.get_running_loop()

        def readable() -> None:
            try:
                data = os.read(self.fd_in, 65536)
            except BlockingIOError:
                return
            if data:
                on_keys(data)

        loop.add_reader(self.fd_in, readable)
        loop.add_signal_handler(signal.SIGWINCH, on_resize)

    def write(self, data: bytes) -> None:
        os.write(self.fd_out, data)

    def leave(self) -> None:
        loop = asyncio.get_running_loop()
        loop.remove_reader(self.fd_in)
        loop.remove_signal_handler(signal.SIGWINCH)
        if self.saved is not None:
            termios.tcsetattr(self.fd_in, termios.TCSADRAIN, self.saved)
