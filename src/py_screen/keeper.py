"""The keeper: tiny and stable. Owns every pty and child, pumps their output.

It is the only reader of each pty master, so its ring buffer is always complete.
It forwards output to the brain (when one is connected), writes the brain's
input to the ptys, and starts the brain again whenever it exits. A brain crash
or reload therefore never closes a pty, so no shell gets SIGHUP.

Ptys, the brain link and signals come from the platform layer, so this file is the
same on Linux, macOS and Windows.
"""

from __future__ import annotations

import asyncio
import errno
import os
import stat
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field

from . import platform, proto

RING_CAP = 2 * 1024 * 1024       # bytes of recent output kept per window for replay
HIGH_WATER = 4 * 1024 * 1024     # safety net: pause every pty while the brain lags this much
CREDIT = 256 * 1024              # per window: stop reading a pty with this much un-acked output


LOG_CAP = 20 << 20          # server.log past this, at a brain restart: cut to its last LOG_KEEP
LOG_KEEP = 1 << 20


def crash_wait(crashes: int) -> float:
    """How long to wait before starting the brain again after its crashes-th crash in a
    row: nothing for the first four, then 5 s, doubling, 60 s at most. Never giving up: the
    keeper holds the user's shells, a brain is only their view."""
    return 0.0 if crashes < 5 else min(60.0, 5.0 * 2 ** (crashes - 5))


def cap_log(path: Path | None = None) -> None:
    """A crash loop must not fill the disk (a tmpfs run dir is RAM): when stderr, the server
    log, is a file past LOG_CAP, keep its last LOG_KEEP. stderr is write-only (opened to
    append), so the tail is read through the log's path, when that is still the same file;
    one already unlinked (its dir removed) is cut to nothing. The writers append (O_APPEND),
    so they go on at the new end."""
    import stat
    try:
        fd = sys.stderr.fileno()
        st = os.fstat(fd)
    except (OSError, ValueError, AttributeError):
        return
    if not stat.S_ISREG(st.st_mode) or st.st_size <= LOG_CAP:
        return
    tail = b""
    try:
        with open(path or proto.runtime_dir() / "server.log", "rb") as f:
            if os.fstat(f.fileno()).st_ino == st.st_ino:
                f.seek(max(0, st.st_size - LOG_KEEP))
                tail = f.read(LOG_KEEP)
    except OSError:
        pass
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)    # Windows: no O_APPEND, writes go at the file pointer
        os.write(fd, b"[server.log cut: it passed %d MB in a crash loop]\n" % (LOG_CAP >> 20) + tail)
    except OSError:
        pass


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), "keeper:", *a, file=sys.stderr, flush=True)


def cannot_enter(d: str) -> str | None:
    """Why this user can't start a program in folder d (None: it can)."""
    try:
        if not stat.S_ISDIR(os.stat(d).st_mode):
            return "not a folder"
    except OSError as e:
        return e.strerror or str(e)
    return None if os.access(d, os.X_OK) else os.strerror(errno.EACCES)


def start_dir(cwd: str | None) -> tuple[str | None, str | None]:
    """Where a new window starts: the folder asked for (the window it was opened from)
    when this user can enter it, else the home folder, else the root, as tmux does; and
    the line the new window shows to say so. A folder that can't be entered (gone, or
    handed to another user, as a container's chown does) must not end the window at its
    start: the shell's chdir failed, it exited 127 and nothing said why."""
    if not cwd or (why := cannot_enter(cwd)) is None:
        return cwd, None
    home = os.path.expanduser("~")
    to = home if cannot_enter(home) is None else os.path.abspath(os.sep)
    return to, f"pys: cannot enter {cwd} ({why}); started in {to}\r\n"


@dataclass
class Win:
    wid: int
    pid: int
    pty: object                  # platform Pty (posix.PosixPty / windows.WinPty)
    cols: int
    rows: int
    cmd: list[str]
    ring: bytearray = field(default_factory=bytearray)
    inflight: int = 0            # bytes sent to the brain and not yet acked
    blocked: bool = False        # reader removed because inflight > CREDIT


class Keeper:
    def __init__(self) -> None:
        self.wins: dict[int, Win] = {}
        self.next_wid = 0
        self.brain_w: asyncio.StreamWriter | None = None
        self.brain_proc: asyncio.subprocess.Process | None = None
        self.paused = False
        self.stopping = asyncio.Event()
        self.crashes = 0                         # brain crashes in a row (after_crash)
        self.sock_id: tuple[int, int] | None = None   # the keeper.sock this keeper bound
        self.started = time.time()               # names the brain's server record

    # ---- ptys ---------------------------------------------------------
    def spawn(self, cmd: list[str], cwd: str | None, cols: int, rows: int,
              extra: dict | None = None) -> Win:
        wid = self.next_wid
        self.next_wid += 1
        # PYS_SESSION names this server (its runtime dir), so a `pys` typed in the window
        # can tell it would attach to the session it runs in (see __init__.nested_in).
        # extra (`pys new --env`) is this window's alone: a copy, never the keeper's own
        # environment, never logged, never kept; PYS_WINDOW and PYS_SESSION are pys's.
        env = dict(os.environ, TERM="screen-256color")
        env.update(extra or {})
        env.update(PYS_WINDOW=str(wid), PYS_SESSION=os.path.realpath(proto.runtime_dir()))
        env.pop("PYS_DIR_INTERNAL", None)
        cwd, note = start_dir(cwd)
        p = platform.spawn_pty(cmd, cwd, env, cols, rows)
        w = Win(wid, p.pid, p, cols, rows, cmd)
        self.wins[wid] = w
        p.start(lambda data: self.on_output(w, data), lambda code: self.reap(w, code))
        self.update_reading(w)
        log("spawned", wid, p.pid, cmd)
        if note:                                    # after the brain hears "spawned"
            log("window", wid, note.strip())
            asyncio.get_running_loop().call_soon(self.on_output, w, note.encode("utf-8"))
        return w

    def update_reading(self, w: Win) -> None:
        """Read a pty unless the brain lags (paused) or the window used its credit."""
        w.pty.set_reading(not self.paused and not w.blocked)  # type: ignore[attr-defined]

    def set_size(self, w: Win, cols: int, rows: int) -> None:
        w.cols, w.rows = cols, rows
        w.pty.resize(cols, rows)  # type: ignore[attr-defined]

    def on_output(self, w: Win, data: bytes) -> None:
        w.ring += data
        if len(w.ring) > RING_CAP:
            del w.ring[: len(w.ring) - RING_CAP]
        if self.brain_w is None:
            return
        self.to_brain(proto.pack(proto.DATA, w.wid, data))
        # Per-window credit: one flooding window can't fill the shared stream, so the
        # echo of another window never queues behind megabytes (head-of-line blocking).
        w.inflight += len(data)
        if w.inflight > CREDIT and not w.blocked and w.wid in self.wins:
            w.blocked = True
            self.update_reading(w)

    def ack(self, wid: int, n: int) -> None:
        w = self.wins.get(wid)
        if not w:
            return
        w.inflight = max(0, w.inflight - n)
        if w.blocked and w.inflight <= CREDIT // 2:
            w.blocked = False
            self.update_reading(w)

    def unblock_all(self) -> None:
        for w in self.wins.values():
            w.inflight = 0
            if w.blocked:
                w.blocked = False
                self.update_reading(w)

    def reap(self, w: Win, code: int | None) -> None:
        self.wins.pop(w.wid, None)
        log("exited", w.wid, code)
        self.to_brain(proto.pack_ctl({"op": "exited", "wid": w.wid, "code": code}))

    # ---- brain link ---------------------------------------------------
    def to_brain(self, frame: bytes) -> None:
        bw = self.brain_w
        if bw is None or bw.is_closing():
            return
        bw.write(frame)
        if not self.paused and bw.transport.get_write_buffer_size() > HIGH_WATER:
            self.pause()
            asyncio.get_running_loop().create_task(self.resume_after_drain(bw))

    def pause(self) -> None:
        self.paused = True
        for w in self.wins.values():
            self.update_reading(w)

    def resume(self) -> None:
        if not self.paused:
            return
        self.paused = False
        for w in self.wins.values():
            self.update_reading(w)

    async def resume_after_drain(self, bw: asyncio.StreamWriter) -> None:
        try:
            await bw.drain()
        except (ConnectionError, OSError):
            pass
        self.resume()

    async def on_brain(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if self.brain_w and not self.brain_w.is_closing():
            self.brain_w.close()
        self.brain_w = w
        self.unblock_all()                          # a new brain starts with full credit
        self.resume()
        hello = [{"wid": x.wid, "pid": x.pid, "cols": x.cols, "rows": x.rows, "cmd": x.cmd}
                 for x in self.wins.values()]
        w.write(proto.pack_ctl({"op": "hello", "windows": hello, "next_wid": self.next_wid,
                                "pid": os.getpid(), "started": self.started}))
        for x in self.wins.values():                # replay: the brain rebuilds its screens
            if x.ring:
                w.write(proto.pack(proto.DATA, x.wid, bytes(x.ring)))
        w.write(proto.pack_ctl({"op": "replayed"}))
        log("brain connected, replayed", len(self.wins), "windows")
        try:
            while True:
                kind, wid, body = await proto.read_frame(r)
                if kind == proto.DATA:
                    x = self.wins.get(wid)
                    if x:
                        x.pty.write(body)  # type: ignore[attr-defined]
                else:
                    self.on_ctl(body)  # type: ignore[arg-type]
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if self.brain_w is w:
                self.brain_w = None
                self.unblock_all()                  # keep children unblocked without a brain
                self.resume()
            log("brain link closed")

    def on_ctl(self, m: dict) -> None:
        op = m.get("op")
        if op == "ack":
            self.ack(m["wid"], m["n"])
        elif op == "spawn":
            try:
                x = self.spawn(m["cmd"], m.get("cwd"), m["cols"], m["rows"], m.get("env"))
            except OSError as e:                    # Windows: CreateProcess fails here
                log("spawn failed", m["cmd"], e)    # (POSIX: the child exits 127)
                self.to_brain(proto.pack_ctl({"op": "spawn_failed", "tag": m.get("tag"),
                                              "error": str(e)}))
                return
            self.to_brain(proto.pack_ctl({"op": "spawned", "wid": x.wid, "pid": x.pid,
                                          "cols": x.cols, "rows": x.rows, "cmd": x.cmd,
                                          "tag": m.get("tag")}))
        elif op == "resize":
            x = self.wins.get(m["wid"])
            if x and (x.cols, x.rows) != (m["cols"], m["rows"]):
                self.set_size(x, m["cols"], m["rows"])
        elif op == "kill":
            x = self.wins.get(m["wid"])
            if x:
                x.pty.hangup()  # type: ignore[attr-defined]
        elif op == "shutdown":
            log("shutdown requested")
            self.stopping.set()

    # ---- brain process --------------------------------------------------
    def after_crash(self, ran: float) -> float:
        """A brain that ran `ran` seconds since its start crashed: the wait before the next
        one. One that ran 30 s ends a crash loop (the count starts again); the backoff's
        own sleep never counts as running."""
        self.crashes = 1 if ran >= 30 else self.crashes + 1
        return crash_wait(self.crashes)

    def sock_is_mine(self) -> bool:
        """keeper.sock is still the socket this keeper bound (POSIX: same inode). Its dir
        removed (a test cleaned up, logind at logout) or a newer keeper's socket there: no
        brain of ours can reach us, and one that started would take the newer keeper's
        place (proto.runtime_dir makes the dir again). Windows' pipes have no file here."""
        if self.sock_id is None:
            return True
        try:
            st = os.stat(proto.keeper_sock_path())
        except OSError:
            return False
        return (st.st_dev, st.st_ino) == self.sock_id

    async def run_brain(self) -> None:
        while not self.stopping.is_set():
            if not self.sock_is_mine():
                log("keeper.sock is gone or another keeper's: no more brains; the shells keep "
                    "running until they end or this keeper is stopped")
                return
            started = time.monotonic()
            self.brain_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "py_screen", "_brain", env=proto.utf8_env())
            code = await self.brain_proc.wait()
            if self.stopping.is_set():
                break
            if code == proto.RELOAD_EXIT:           # asked for: never throttled
                log("brain reload")
                continue
            cap_log()                               # a crash: keep the log, back off
            wait = self.after_crash(time.monotonic() - started)
            log("brain exited", code, "- starting it again" + (f" in {wait:.0f} s" if wait else ""))
            if wait:
                await asyncio.sleep(wait)

    async def main(self) -> None:
        path = proto.keeper_sock()
        server = await platform.serve(path, self.on_brain)
        if os.name == "posix":
            st = os.stat(path)
            self.sock_id = (st.st_dev, st.st_ino)
        loop = asyncio.get_running_loop()
        platform.on_stop(loop, self.stopping.set)
        log("listening", path, "pid", os.getpid())
        pidfile = proto.runtime_dir() / "keeper.pid"   # who to stop when nothing else answers
        pidfile.write_text(f"{os.getpid()}\n", encoding="utf-8")
        brain_task = loop.create_task(self.run_brain())
        await self.stopping.wait()
        for x in list(self.wins.values()):
            x.pty.hangup()  # type: ignore[attr-defined]
        if self.brain_proc and self.brain_proc.returncode is None:
            self.brain_proc.terminate()
        brain_task.cancel()
        server.close()
        platform.cleanup(path)
        platform.cleanup(proto.brain_sock())
        try:
            if pidfile.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pidfile.unlink()
        except OSError:
            pass
        log("bye")


def main() -> None:
    asyncio.run(Keeper().main())
