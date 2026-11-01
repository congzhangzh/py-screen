"""The keeper: tiny and stable. Owns every pty and child, pumps their output.

It is the only reader of each pty master, so its ring buffer is always complete.
It forwards output to the brain (when one is connected), writes the brain's
input to the ptys, and starts the brain again whenever it exits. A brain crash
or reload therefore never closes a pty, so no shell gets SIGHUP.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import struct
import sys
import termios
import time
import pty
from dataclasses import dataclass, field

from . import proto

RING_CAP = 2 * 1024 * 1024       # bytes of recent output kept per window for replay
HIGH_WATER = 4 * 1024 * 1024     # pause reading ptys while the brain lags this much


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), "keeper:", *a, file=sys.stderr, flush=True)


@dataclass
class Win:
    wid: int
    pid: int
    fd: int
    cols: int
    rows: int
    cmd: list[str]
    ring: bytearray = field(default_factory=bytearray)


class Keeper:
    def __init__(self) -> None:
        self.wins: dict[int, Win] = {}
        self.next_wid = 0
        self.brain_w: asyncio.StreamWriter | None = None
        self.brain_proc: asyncio.subprocess.Process | None = None
        self.paused = False
        self.stopping = asyncio.Event()
        self.brain_starts: list[float] = []

    # ---- ptys ---------------------------------------------------------
    def spawn(self, cmd: list[str], cwd: str | None, cols: int, rows: int) -> Win:
        wid = self.next_wid
        self.next_wid += 1
        env = dict(os.environ, TERM="screen-256color", PYS_WINDOW=str(wid))
        env.pop("PYS_DIR_INTERNAL", None)
        pid, fd = pty.fork()                       # child: setsid + pty slave as ctty
        if pid == 0:
            try:
                if cwd:
                    os.chdir(cwd)
                os.execvpe(cmd[0], cmd, env)
            finally:
                os._exit(127)
        os.set_blocking(fd, False)
        w = Win(wid, pid, fd, cols, rows, cmd)
        self.set_size(w, cols, rows)
        self.wins[wid] = w
        if not self.paused:
            asyncio.get_running_loop().add_reader(fd, self.on_readable, w)
        log("spawned", wid, pid, cmd)
        return w

    def set_size(self, w: Win, cols: int, rows: int) -> None:
        w.cols, w.rows = cols, rows
        fcntl.ioctl(w.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def on_readable(self, w: Win) -> None:
        try:
            data = os.read(w.fd, 65536)
        except BlockingIOError:
            return
        except OSError:                            # EIO: the slave side is gone
            data = b""
        if not data:
            self.reap(w)
            return
        w.ring += data
        if len(w.ring) > RING_CAP:
            del w.ring[: len(w.ring) - RING_CAP]
        self.to_brain(proto.pack(proto.DATA, w.wid, data))

    def reap(self, w: Win) -> None:
        loop = asyncio.get_running_loop()
        loop.remove_reader(w.fd)
        os.close(w.fd)
        try:
            _, status = os.waitpid(w.pid, 0)
            code = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            code = None
        del self.wins[w.wid]
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
        loop = asyncio.get_running_loop()
        for w in self.wins.values():
            loop.remove_reader(w.fd)

    def resume(self) -> None:
        if not self.paused:
            return
        self.paused = False
        loop = asyncio.get_running_loop()
        for w in self.wins.values():
            loop.add_reader(w.fd, self.on_readable, w)

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
        self.resume()
        hello = [{"wid": x.wid, "pid": x.pid, "cols": x.cols, "rows": x.rows, "cmd": x.cmd}
                 for x in self.wins.values()]
        w.write(proto.pack_ctl({"op": "hello", "windows": hello, "next_wid": self.next_wid}))
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
                        self.write_pty(x, body)  # type: ignore[arg-type]
                else:
                    self.on_ctl(body)  # type: ignore[arg-type]
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if self.brain_w is w:
                self.brain_w = None
                self.resume()                       # keep children unblocked without a brain
            log("brain link closed")

    def write_pty(self, x: Win, data: bytes) -> None:
        # Small writes (keys, pastes); a full pty input queue would raise BlockingIOError.
        view = memoryview(data)
        while view:
            try:
                n = os.write(x.fd, view)
                view = view[n:]
            except BlockingIOError:
                time.sleep(0.001)
            except OSError:
                return

    def on_ctl(self, m: dict) -> None:
        op = m.get("op")
        if op == "spawn":
            x = self.spawn(m["cmd"], m.get("cwd"), m["cols"], m["rows"])
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
                os.killpg(x.pid, signal.SIGHUP)
        elif op == "shutdown":
            log("shutdown requested")
            self.stopping.set()

    # ---- brain process --------------------------------------------------
    async def run_brain(self) -> None:
        while not self.stopping.is_set():
            self.brain_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "py_screen", "_brain")
            code = await self.brain_proc.wait()
            if self.stopping.is_set():
                break
            if code == proto.RELOAD_EXIT:           # asked for: never throttled
                log("brain reload")
                continue
            now = time.monotonic()                  # a crash: throttle a crash loop
            self.brain_starts = [t for t in self.brain_starts if now - t < 10] + [now]
            log("brain exited", code, "- starting it again")
            if len(self.brain_starts) >= 5:
                log("brain crashed 5 times in 10 s; waiting 5 s")
                await asyncio.sleep(5)

    async def main(self) -> None:
        path = proto.keeper_sock()
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.on_brain, str(path))
        os.chmod(path, 0o600)
        loop = asyncio.get_running_loop()
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, self.stopping.set)
        loop.add_signal_handler(signal.SIGHUP, lambda: None)
        log("listening", path, "pid", os.getpid())
        brain_task = loop.create_task(self.run_brain())
        await self.stopping.wait()
        for x in list(self.wins.values()):
            try:
                os.killpg(x.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
        if self.brain_proc and self.brain_proc.returncode is None:
            self.brain_proc.terminate()
        brain_task.cancel()
        server.close()
        path.unlink(missing_ok=True)
        proto.brain_sock().unlink(missing_ok=True)
        log("bye")


def main() -> None:
    asyncio.run(Keeper().main())
