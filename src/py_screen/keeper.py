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
import os
import sys
import time
from dataclasses import dataclass, field

from . import platform, proto

RING_CAP = 2 * 1024 * 1024       # bytes of recent output kept per window for replay
HIGH_WATER = 4 * 1024 * 1024     # safety net: pause every pty while the brain lags this much
CREDIT = 256 * 1024              # per window: stop reading a pty with this much un-acked output


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), "keeper:", *a, file=sys.stderr, flush=True)


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
        self.brain_starts: list[float] = []

    # ---- ptys ---------------------------------------------------------
    def spawn(self, cmd: list[str], cwd: str | None, cols: int, rows: int) -> Win:
        wid = self.next_wid
        self.next_wid += 1
        env = dict(os.environ, TERM="screen-256color", PYS_WINDOW=str(wid))
        env.pop("PYS_DIR_INTERNAL", None)
        p = platform.spawn_pty(cmd, cwd, env, cols, rows)
        w = Win(wid, p.pid, p, cols, rows, cmd)
        self.wins[wid] = w
        p.start(lambda data: self.on_output(w, data), lambda code: self.reap(w, code))
        self.update_reading(w)
        log("spawned", wid, p.pid, cmd)
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
                x = self.spawn(m["cmd"], m.get("cwd"), m["cols"], m["rows"])
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
        server = await platform.serve(path, self.on_brain)
        loop = asyncio.get_running_loop()
        platform.on_stop(loop, self.stopping.set)
        log("listening", path, "pid", os.getpid())
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
        log("bye")


def main() -> None:
    asyncio.run(Keeper().main())
