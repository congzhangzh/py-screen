"""Windows: ConPTY (via pywinpty), named pipes, a hidden-console daemon, console VT mode.

Mechanics that differ from POSIX, and why:
- ConPTY has no fd the event loop can watch. Each Pty runs one reader thread that
  blocks in `PTY.read()` and hands bytes to the loop with `call_soon_threadsafe`; a
  `threading.Event` gate is `set_reading`. pywinpty returns `str`, so output is
  re-encoded as UTF-8 (ConPTY emits UTF-8 VT text anyway).
- ConPTY's output pipe does not close when the child exits (it closes with the pseudo
  console), so EOF can't tell us the child is gone. A second thread waits on the
  process handle, then cancels the blocked read and reports the exit code.
- IPC is a named pipe per socket name. `\\\\.\\pipe\\` is one flat namespace, so the
  pipe name carries the user and a hash of the runtime dir (PYS_DIR keeps tests apart).
- A pipe's default DACL lets other local users open it for reading, and the keeper
  replays every window's output to whoever connects. A Unix socket is 0600, so POSIX
  needs nothing more; here every connection first sends a 32-byte key kept in the
  runtime dir (user-private under %LOCALAPPDATA%), and `serve` drops any that doesn't.
- The keeper starts with CREATE_NO_WINDOW: it gets a hidden console of its own, which
  the brain inherits, so no console window pops up and closing the user's terminal
  does not end the session.
"""

from __future__ import annotations

import _winapi
import asyncio
import codecs
import ctypes
import hashlib
import hmac
import os
import secrets
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INFINITE = 0xFFFFFFFF
ERROR_SEM_TIMEOUT = 121

# ---- ptys -------------------------------------------------------------------


class WinPty:
    """Same contract as posix.PosixPty."""

    def __init__(self, cmd: list[str], cwd: str | None, env: dict, cols: int, rows: int):
        from winpty import PTY                       # pywinpty; ConPTY backend = 0

        exe = shutil.which(cmd[0], path=env.get("PATH"))
        if exe is None:
            raise FileNotFoundError(f"command not found: {cmd[0]}")
        self.pty = PTY(cols, rows, backend=0)
        block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0"
        args = subprocess.list2cmdline(cmd[1:])
        self.pty.spawn(exe, cmdline=(" " + args) if args else None,
                       cwd=cwd or os.path.expanduser("~"), env=block)
        self.pid: int = self.pty.pid
        # Opened right away, so the exit code survives even if the child dies at once.
        self.handle = _winapi.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                                          False, self.pid)
        self.gate = threading.Event()
        self.closed = False
        self.exited = threading.Event()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.loop: asyncio.AbstractEventLoop | None = None
        self.on_data: Callable[[bytes], None] = lambda b: None
        self.on_exit: Callable[[int | None], None] = lambda c: None

    def start(self, on_data: Callable[[bytes], None],
              on_exit: Callable[[int | None], None]) -> None:
        self.on_data, self.on_exit = on_data, on_exit
        self.loop = asyncio.get_running_loop()
        self.reader = threading.Thread(target=self._pump, name=f"pty-{self.pid}", daemon=True)
        self.reader.start()
        threading.Thread(target=self._watch, name=f"wait-{self.pid}", daemon=True).start()

    def set_reading(self, on: bool) -> None:
        if on:
            self.gate.set()
        else:
            self.gate.clear()

    def _pump(self) -> None:
        assert self.loop is not None
        while True:
            self.gate.wait()
            pty = self.pty
            if pty is None:
                break
            try:
                text = pty.read(blocking=True)
                eof = not text and (self.exited.is_set() or pty.iseof())
            except Exception:                        # cancelled, or the console closed
                break
            if text:
                self.loop.call_soon_threadsafe(self._deliver, text.encode("utf-8", "replace"))
            elif eof:
                break

    def _deliver(self, data: bytes) -> None:
        if not self.closed:
            self.on_data(data)

    def _watch(self) -> None:
        assert self.loop is not None
        _winapi.WaitForSingleObject(self.handle, INFINITE)
        code: int | None
        try:
            code = _winapi.GetExitCodeProcess(self.handle)
        except OSError:
            code = None
        self.exited.set()
        time.sleep(0.05)                             # let the last output through
        self.gate.set()                              # a paused reader must see the end
        try:
            if self.pty is not None:
                self.pty.cancel_io()
        except Exception:
            pass
        self.reader.join(1.0)
        self.loop.call_soon_threadsafe(self._finish, code)

    def _finish(self, code: int | None) -> None:
        if self.closed:
            return
        self.closed = True
        _winapi.CloseHandle(self.handle)
        self.pty = None                              # drop it: ClosePseudoConsole
        self.on_exit(code)

    def write(self, data: bytes) -> None:
        text = self.decoder.decode(data)             # keeps a split UTF-8 char for later
        if text and not self.closed and self.pty is not None:
            try:
                self.pty.write(text)
            except Exception:
                pass

    def resize(self, cols: int, rows: int) -> None:
        if not self.closed and self.pty is not None:
            self.pty.set_size(cols, rows)

    def hangup(self) -> None:
        # Ends the shell; dropping the pseudo console when it is gone sends
        # CTRL_CLOSE_EVENT to whatever else is still attached (SIGHUP's role).
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass


def spawn_pty(cmd: list[str], cwd: str | None, env: dict, cols: int, rows: int) -> WinPty:
    return WinPty(cmd, cwd, env, cols, rows)


# ---- IPC: named pipes -----------------------------------------------------------


def _pipe(path: Path) -> str:
    user = re.sub(r"[^\w.-]", "_", os.environ.get("USERNAME", "user"))
    h = hashlib.sha1(str(path.parent.resolve()).lower().encode()).hexdigest()[:12]
    return rf"\\.\pipe\pys-{user}-{h}-{path.stem}"


KEY_LEN = 32


def _key(run_dir: Path) -> bytes:
    """The per-session pipe key; whoever comes first (keeper or brain) creates it."""
    p = run_dir / "pipe.key"
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
    except FileExistsError:
        for _ in range(50):                          # the creator may still be writing
            if len(data := p.read_bytes()) == KEY_LEN:
                return data
            time.sleep(0.01)
        raise OSError(f"bad pipe key: {p}")
    key = secrets.token_bytes(KEY_LEN)
    os.write(fd, key)
    os.close(fd)
    return key


class _PipeServer:
    def __init__(self, servers: list) -> None:
        self.servers = servers

    def close(self) -> None:
        for s in self.servers:
            s.close()


async def serve(path: Path, cb, limit: int = 1 << 16) -> _PipeServer:
    loop = asyncio.get_running_loop()
    key = _key(path.parent)

    async def checked(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            got = await asyncio.wait_for(reader.readexactly(KEY_LEN), 5)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            got = b""
        if not hmac.compare_digest(got, key):
            writer.close()
            return
        res = cb(reader, writer)
        if asyncio.iscoroutine(res):
            await res

    def factory() -> asyncio.StreamReaderProtocol:
        return asyncio.StreamReaderProtocol(asyncio.StreamReader(limit=limit), checked)

    # ProactorEventLoop.start_serving_pipe: the first instance uses
    # FILE_FLAG_FIRST_PIPE_INSTANCE, so a second server on the same name fails.
    return _PipeServer(await loop.start_serving_pipe(factory, _pipe(path)))  # type: ignore[attr-defined]


async def connect(path: Path, limit: int = 1 << 16):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit)
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.create_pipe_connection(  # type: ignore[attr-defined]
        lambda: protocol, _pipe(path))
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    writer.write(_key(path.parent))                  # first bytes on the link: the key
    return reader, writer


def alive(path: Path) -> bool:
    try:
        _winapi.WaitNamedPipe(_pipe(path), 1)
        return True
    except OSError as e:
        return getattr(e, "winerror", None) == ERROR_SEM_TIMEOUT   # exists, all busy


def cleanup(path: Path) -> None:
    pass                                             # a pipe goes away with its server


def runtime_base() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())


def check_path(p: Path) -> None:
    pass


# ---- processes ----------------------------------------------------------------


def start_detached(argv: list[str], log: Path) -> None:
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log, "ab") as logf:
        try:                                         # leave the terminal's job if allowed
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                             cwd=os.path.expanduser("~"),
                             creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except OSError:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                             cwd=os.path.expanduser("~"), creationflags=flags)


def on_stop(loop: asyncio.AbstractEventLoop, cb: Callable[[], None]) -> None:
    def handler(*_: object) -> None:
        loop.call_soon_threadsafe(cb)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGBREAK, handler)


def default_shell() -> str:
    return os.environ.get("PYS_SHELL") or os.environ.get("COMSPEC") or "cmd.exe"


def loadavg() -> float | None:
    return None


def proc_cwd(pid: int) -> str | None:
    return None                                      # lives in the child's PEB; not read yet


STILL_ACTIVE = 259


def proc_start(pid: int) -> str | None:
    """Which process `pid` is: its creation time (GetProcessTimes), which changes when the
    PID is reused; None when no such process runs (or it has exited)."""
    try:
        h = _winapi.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except OSError:
        return None
    try:
        if _winapi.GetExitCodeProcess(h) != STILL_ACTIVE:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not k32.GetProcessTimes(wintypes.HANDLE(h), *(ctypes.byref(t) for t in times)):
            return None
        return str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)
    except OSError:
        return None
    finally:
        _winapi.CloseHandle(h)


# ---- the attached terminal ---------------------------------------------------------

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ENABLE_PROCESSED_OUTPUT = 0x1
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x4
DISABLE_NEWLINE_AUTO_RETURN = 0x8
ENABLE_EXTENDED_FLAGS = 0x80                         # with QUICK_EDIT (0x40) cleared
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x200


class RawTerm:
    """The console as a VT terminal: keys arrive as VT sequences (ENABLE_VIRTUAL_TERMINAL_
    INPUT), output is VT (ENABLE_VIRTUAL_TERMINAL_PROCESSING). There is no SIGWINCH, so
    the size is polled. Needs a real console (Windows Terminal, conhost), not mintty."""

    def __init__(self) -> None:
        import msvcrt
        self.hin = msvcrt.get_osfhandle(sys.stdin.fileno())
        self.hout = msvcrt.get_osfhandle(sys.stdout.fileno())
        self.saved: tuple[int, int, int, int] | None = None
        self.poll: asyncio.Task | None = None

    def _mode(self, h: int) -> int:
        m = wintypes.DWORD()
        if not _k32.GetConsoleMode(h, ctypes.byref(m)):
            raise SystemExit("pys: attach needs a Windows console (Windows Terminal or conhost)")
        return m.value

    def size(self) -> tuple[int, int]:
        s = shutil.get_terminal_size()
        return s.columns, s.lines

    def enter(self) -> None:
        mi, mo = self._mode(self.hin), self._mode(self.hout)
        self.saved = (mi, mo, _k32.GetConsoleCP(), _k32.GetConsoleOutputCP())
        _k32.SetConsoleCP(65001)
        _k32.SetConsoleOutputCP(65001)
        _k32.SetConsoleMode(self.hin, ENABLE_VIRTUAL_TERMINAL_INPUT | ENABLE_EXTENDED_FLAGS)
        _k32.SetConsoleMode(self.hout, ENABLE_PROCESSED_OUTPUT
                            | ENABLE_VIRTUAL_TERMINAL_PROCESSING | DISABLE_NEWLINE_AUTO_RETURN)

    def start(self, on_keys: Callable[[bytes], None], on_resize: Callable[[], None]) -> None:
        loop = asyncio.get_running_loop()

        def read_keys() -> None:
            buf = ctypes.create_unicode_buffer(4096)
            n = wintypes.DWORD()
            held = ""                                # a high surrogate waiting for its pair
            while _k32.ReadConsoleW(self.hin, buf, 4096, ctypes.byref(n), None):
                s = held + buf[:n.value]  # type: ignore[operator]  # c_wchar slice is a str
                held = ""
                if s and "\ud800" <= s[-1] <= "\udbff":
                    s, held = s[:-1], s[-1]
                if s:
                    data = s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
                    loop.call_soon_threadsafe(on_keys, data.encode("utf-8"))

        threading.Thread(target=read_keys, name="console-keys", daemon=True).start()

        async def watch_size() -> None:
            last = self.size()
            while True:
                await asyncio.sleep(0.25)
                if (now := self.size()) != last:
                    last = now
                    on_resize()

        self.poll = loop.create_task(watch_size())

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(sys.stdout.fileno(), view):]

    def leave(self) -> None:
        if self.poll:
            self.poll.cancel()
        if self.saved:
            mi, mo, cp, ocp = self.saved
            _k32.SetConsoleMode(self.hin, mi)
            _k32.SetConsoleMode(self.hout, mo)
            _k32.SetConsoleCP(cp)
            _k32.SetConsoleOutputCP(ocp)
