"""The brain: everything that changes often. Restartable at any moment.

It holds no pty. It talks to the keeper (window output in, keys and resizes
out), keeps a pyte screen per window, renders the current window plus a status
line to every attached client, and answers one-shot RPCs (`pys ls`, `send`...).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field

import pyte

from . import proto

PREFIX = 0x1D                    # C-]
FRAME = 1 / 60                   # render at most 60 times a second
CLIENT_HIGH_WATER = 1 << 20      # a client this far behind skips frames, then gets a full redraw

# Private modes the outer terminal must mirror, because it produces the input
# (cursor-key mode, bracketed paste, mouse reporting). pyte stores private
# mode N as N << 5.
SYNC_MODES = (1, 1000, 1002, 1003, 1006, 2004)
COLORS = {"black": 0, "red": 1, "green": 2, "brown": 3, "blue": 4,
          "magenta": 5, "cyan": 6, "white": 7}


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), "brain:", *a, file=sys.stderr, flush=True)


def sgr_color(c: str, base: int) -> str:
    if c == "default":
        return str(base + 9)
    if c in COLORS:
        return str(base + COLORS[c])
    if c.startswith("bright") and c[6:] in COLORS:
        return str(base + 60 + COLORS[c[6:]])
    if len(c) == 6:
        try:
            r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
            return f"{base + 8};2;{r};{g};{b}"
        except ValueError:
            pass
    return str(base + 9)


_sgr_cache: dict[tuple, str] = {}


def sgr(ch: pyte.screens.Char) -> str:
    key = (ch.fg, ch.bg, ch.bold, ch.italics, ch.underscore,
           ch.strikethrough, ch.reverse, ch.blink)
    s = _sgr_cache.get(key)
    if s is None:
        parts = ["0", sgr_color(ch.fg, 30), sgr_color(ch.bg, 40)]
        for flag, code in ((ch.bold, "1"), (ch.italics, "3"), (ch.underscore, "4"),
                           (ch.blink, "5"), (ch.reverse, "7"), (ch.strikethrough, "9")):
            if flag:
                parts.append(code)
        s = _sgr_cache[key] = "\x1b[" + ";".join(parts) + "m"
    return s


@dataclass
class Window:
    wid: int
    pid: int
    cmd: list[str]
    screen: pyte.Screen
    stream: pyte.ByteStream

    @classmethod
    def new(cls, wid: int, pid: int, cmd: list[str], cols: int, rows: int) -> "Window":
        screen = pyte.Screen(cols, rows)
        return cls(wid, pid, cmd, screen, pyte.ByteStream(screen))

    @property
    def name(self) -> str:
        return self.screen.title or os.path.basename(self.cmd[0])

    def cwd(self) -> str | None:
        try:
            return os.readlink(f"/proc/{self.pid}/cwd")
        except OSError:
            return None


@dataclass
class Client:
    writer: asyncio.StreamWriter
    modes: set[int] = field(default_factory=set)
    stale: bool = True            # needs a full redraw
    prefix: bool = False


class Brain:
    def __init__(self) -> None:
        self.wins: dict[int, Window] = {}
        self.order: list[int] = []
        self.current: int | None = None
        self.clients: list[Client] = []
        self.cols, self.rows = 80, 24            # outer size; windows get rows - 1
        self.keeper_w: asyncio.StreamWriter | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.tag_n = 0
        self.render_handle: asyncio.TimerHandle | asyncio.Handle | None = None
        self.last_render = 0.0
        self.status_dirty = True
        self.done = asyncio.Event()
        self.exit_code = 0
        self.ready = asyncio.Event()             # replay from the keeper finished

    # ---- keeper link ------------------------------------------------------
    def to_keeper(self, frame: bytes) -> None:
        if self.keeper_w:
            self.keeper_w.write(frame)

    async def keeper_loop(self) -> None:
        r, self.keeper_w = await asyncio.open_unix_connection(
            str(proto.keeper_sock()), limit=1 << 22)
        try:
            while True:
                kind, wid, body = await proto.read_frame(r)
                if kind == proto.DATA:
                    w = self.wins.get(wid)
                    if w:
                        w.stream.feed(body)  # type: ignore[arg-type]
                        if wid == self.current:
                            self.schedule_render()
                        elif w.screen.title != getattr(w, "_seen_title", None):
                            self.status_dirty = True
                else:
                    self.on_keeper_ctl(body)  # type: ignore[arg-type]
        except (asyncio.IncompleteReadError, ConnectionError):
            log("keeper link lost")
            self.quit(1)

    def on_keeper_ctl(self, m: dict) -> None:
        op = m["op"]
        if op == "hello":
            for x in m["windows"]:
                self.add_window(x)
        elif op == "replayed":
            self.ready.set()
            asyncio.get_running_loop().create_task(self.nudge_all())
        elif op == "spawned":
            w = self.add_window(m)
            fut = self.pending.pop(m.get("tag") or "", None)
            if fut and not fut.done():
                fut.set_result(w)
        elif op == "exited":
            self.remove_window(m["wid"])

    def add_window(self, x: dict) -> Window:
        w = Window.new(x["wid"], x["pid"], x["cmd"], x["cols"], x["rows"])
        self.wins[w.wid] = w
        self.order.append(w.wid)
        if self.current is None:
            self.current = w.wid
        self.status_dirty = True
        return w

    def remove_window(self, wid: int) -> None:
        self.wins.pop(wid, None)
        if wid in self.order:
            i = self.order.index(wid)
            self.order.remove(wid)
            if self.current == wid:
                self.current = self.order[min(i, len(self.order) - 1)] if self.order else None
                self.mark_all_stale()
        self.status_dirty = True
        if not self.order:
            log("last window exited; shutting down")
            self.broadcast_ctl({"op": "bye"})
            self.to_keeper(proto.pack_ctl({"op": "shutdown"}))
            self.quit(0)
            return
        self.schedule_render()

    async def nudge_all(self) -> None:
        """After a replay, change each pty's size by one row and back: SIGWINCH makes
        full-screen apps repaint whatever the replayed bytes could not rebuild."""
        for w in self.wins.values():
            s = w.screen
            self.to_keeper(proto.pack_ctl({"op": "resize", "wid": w.wid,
                                           "cols": s.columns, "rows": max(1, s.lines - 1)}))
        await asyncio.sleep(0.05)
        for w in self.wins.values():
            s = w.screen
            self.to_keeper(proto.pack_ctl({"op": "resize", "wid": w.wid,
                                           "cols": s.columns, "rows": s.lines}))

    async def spawn(self, cmd: list[str] | None = None, cwd: str | None = None) -> Window:
        self.tag_n += 1
        tag = f"t{self.tag_n}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[tag] = fut
        cmd = cmd or [os.environ.get("SHELL", "/bin/sh")]
        self.to_keeper(proto.pack_ctl({"op": "spawn", "cmd": cmd, "cwd": cwd, "tag": tag,
                                       "cols": self.cols, "rows": self.rows - 1}))
        return await asyncio.wait_for(fut, 5)

    # ---- size, selection ----------------------------------------------------
    def set_size(self, cols: int, rows: int) -> None:
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        for w in self.wins.values():
            w.screen.resize(rows - 1, cols)
            self.to_keeper(proto.pack_ctl({"op": "resize", "wid": w.wid,
                                           "cols": cols, "rows": rows - 1}))
        self.mark_all_stale()

    def select(self, wid: int) -> None:
        if wid in self.wins and wid != self.current:
            self.current = wid
            self.mark_all_stale()

    def step(self, d: int) -> None:
        if self.current is not None and self.order:
            i = self.order.index(self.current)
            self.select(self.order[(i + d) % len(self.order)])

    def mark_all_stale(self) -> None:
        for c in self.clients:
            c.stale = True
        self.status_dirty = True
        self.schedule_render()

    # ---- rendering ----------------------------------------------------------
    def schedule_render(self) -> None:
        if self.render_handle is not None:
            return
        loop = asyncio.get_running_loop()
        wait = FRAME - (time.monotonic() - self.last_render)
        if wait <= 0:
            self.render_handle = loop.call_soon(self.render)
        else:
            self.render_handle = loop.call_later(wait, self.render)

    def line(self, screen: pyte.Screen, y: int) -> str:
        row = screen.buffer[y]
        out = [f"\x1b[{y + 1};1H"]
        last = None
        for x in range(screen.columns):
            ch = row[x]
            if ch.data == "":                 # right half of a wide character
                continue
            s = sgr(ch)
            if s is not last:
                out.append(s)
                last = s
            out.append(ch.data)
        out.append("\x1b[0m\x1b[K")
        return "".join(out)

    def status_line(self) -> str:
        parts = []
        for wid in self.order:
            w = self.wins[wid]
            w._seen_title = w.screen.title  # type: ignore[attr-defined]
            label = f" {wid} {w.name} "
            parts.append(f"\x1b[0;30;43m{label}\x1b[0;7m" if wid == self.current else label)
        text = "\x1b[0;7m pys │" + "│".join(parts)
        return f"\x1b[{self.rows};1H\x1b[2K{text}\x1b[K\x1b[0m"

    def render(self) -> None:
        self.render_handle = None
        self.last_render = time.monotonic()
        w = self.wins.get(self.current) if self.current is not None else None
        if w is None or not self.clients:
            if w:
                w.screen.dirty.clear()
            return
        s = w.screen
        dirty = sorted(s.dirty)
        s.dirty.clear()
        body_dirty = "".join(self.line(s, y) for y in dirty)
        body_full: str | None = None
        status = self.status_line()
        want = {m for m in SYNC_MODES if (m << 5) in s.mode}
        cx, cy = min(s.cursor.x, s.columns - 1), s.cursor.y
        tail = f"\x1b[{cy + 1};{cx + 1}H" + ("" if s.cursor.hidden else "\x1b[?25h")
        for c in self.clients:
            if c.writer.is_closing():
                continue
            if c.writer.transport.get_write_buffer_size() > CLIENT_HIGH_WATER:
                c.stale = True                # slow client: skip, redraw fully later
                continue
            if c.stale:
                if body_full is None:
                    body_full = "\x1b[0m\x1b[2J" + "".join(self.line(s, y) for y in range(s.lines))
                body = body_full + status
                c.stale = False
            else:
                body = body_dirty + (status if self.status_dirty else "")
            modes = "".join(f"\x1b[?{m}h" for m in want - c.modes)
            modes += "".join(f"\x1b[?{m}l" for m in c.modes - want)
            c.modes = want
            frame = "\x1b[?2026h\x1b[?25l" + modes + body + tail + "\x1b[?2026l"
            c.writer.write(proto.pack(proto.DATA, w.wid, frame.encode()))
        self.status_dirty = False

    def broadcast_ctl(self, m: dict) -> None:
        for c in self.clients:
            if not c.writer.is_closing():
                c.writer.write(proto.pack_ctl(m))

    # ---- clients ------------------------------------------------------------
    async def on_conn(self, r: asyncio.StreamReader, wr: asyncio.StreamWriter) -> None:
        try:
            kind, _, first = await proto.read_frame(r)
        except (asyncio.IncompleteReadError, ConnectionError):
            wr.close()
            return
        await self.ready.wait()
        assert isinstance(first, dict)
        if first.get("op") == "rpc":
            try:
                reply = await self.rpc(first)
            except Exception as e:  # report, don't crash the brain
                reply = {"ok": False, "error": repr(e)}
            wr.write(proto.pack_ctl(reply))
            await wr.drain()
            wr.close()
            if first.get("cmd") == "reload":
                self.reload()
            return
        if first.get("op") == "attach":
            await self.client_loop(r, wr, first)

    async def client_loop(self, r: asyncio.StreamReader, wr: asyncio.StreamWriter,
                          hello: dict) -> None:
        c = Client(wr)
        self.clients.append(c)
        self.set_size(hello["cols"], hello["rows"])
        if not self.wins:
            await self.spawn(cwd=hello.get("cwd"))
        self.mark_all_stale()
        try:
            while True:
                kind, _, body = await proto.read_frame(r)
                if kind == proto.DATA:
                    await self.keys(c, body)  # type: ignore[arg-type]
                elif body.get("op") == "resize":  # type: ignore[union-attr]
                    self.set_size(body["cols"], body["rows"])  # type: ignore[index]
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if c in self.clients:
                self.clients.remove(c)
            wr.close()

    async def keys(self, c: Client, data: bytes) -> None:
        out = bytearray()
        for b in data:
            if c.prefix:
                c.prefix = False
                self.flush_keys(out)
                await self.command(c, b)
            elif b == PREFIX:
                c.prefix = True
            else:
                out.append(b)
        self.flush_keys(out)

    def flush_keys(self, out: bytearray) -> None:
        if out and self.current is not None:
            self.to_keeper(proto.pack(proto.DATA, self.current, bytes(out)))
        out.clear()

    async def command(self, c: Client, b: int) -> None:
        key = chr(b)
        if b == PREFIX:                                   # C-] C-] sends a literal C-]
            self.flush_keys(bytearray([PREFIX]))
        elif key == "d":
            c.writer.write(proto.pack_ctl({"op": "detached"}))
            c.writer.close()
        elif key == "c":
            cur = self.wins.get(self.current) if self.current is not None else None
            w = await self.spawn(cwd=cur.cwd() if cur else None)
            self.select(w.wid)
        elif key in "n ":
            self.step(1)
        elif key == "p":
            self.step(-1)
        elif key.isdigit():
            self.select(int(key))
        elif key == "X" and self.current is not None:
            self.to_keeper(proto.pack_ctl({"op": "kill", "wid": self.current}))
        elif key == "l":
            self.mark_all_stale()
        elif key == "R":
            self.reload()
        elif key == "Q":
            self.broadcast_ctl({"op": "bye"})
            self.to_keeper(proto.pack_ctl({"op": "shutdown"}))
            self.quit(0)

    def reload(self) -> None:
        log("reload")
        self.broadcast_ctl({"op": "reloading"})
        self.quit(proto.RELOAD_EXIT)

    # ---- RPC ----------------------------------------------------------------
    async def rpc(self, m: dict) -> dict:
        cmd = m.get("cmd")
        if cmd == "ls":
            return {"ok": True, "pid": os.getpid(), "current": self.current,
                    "clients": len(self.clients), "size": [self.cols, self.rows],
                    "windows": [{"wid": w.wid, "pid": w.pid, "name": w.name,
                                 "cmd": w.cmd, "cwd": w.cwd()} for w in
                                (self.wins[i] for i in self.order)]}
        if cmd == "send":
            self.to_keeper(proto.pack(proto.DATA, m["wid"], m["text"].encode()))
            return {"ok": True}
        if cmd == "capture":
            w = self.wins[m.get("wid", self.current)]
            return {"ok": True, "lines": w.screen.display,
                    "cursor": [w.screen.cursor.x, w.screen.cursor.y]}
        if cmd == "new":
            w = await self.spawn(m.get("argv"), m.get("cwd"))
            if m.get("select"):
                self.select(w.wid)
            return {"ok": True, "wid": w.wid, "pid": w.pid}
        if cmd == "select":
            self.select(m["wid"])
            return {"ok": True}
        if cmd == "reload":
            return {"ok": True}
        if cmd == "kill":
            self.broadcast_ctl({"op": "bye"})
            self.to_keeper(proto.pack_ctl({"op": "shutdown"}))
            self.quit(0)
            return {"ok": True}
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    # ---- lifecycle ----------------------------------------------------------
    def quit(self, code: int) -> None:
        self.exit_code = code
        self.done.set()

    async def main(self) -> int:
        loop = asyncio.get_running_loop()
        keeper_task = loop.create_task(self.keeper_loop())
        path = proto.brain_sock()
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.on_conn, str(path), limit=1 << 20)
        os.chmod(path, 0o600)
        log("up, pid", os.getpid())
        await self.done.wait()
        server.close()
        for c in self.clients:                      # flush "bye"/"reloading" to clients
            try:
                await asyncio.wait_for(c.writer.drain(), 0.5)
            except (asyncio.TimeoutError, ConnectionError):
                pass
        if self.keeper_w:
            try:
                await asyncio.wait_for(self.keeper_w.drain(), 0.5)
            except (asyncio.TimeoutError, ConnectionError):
                pass
        keeper_task.cancel()
        return self.exit_code


def main() -> None:
    sys.exit(asyncio.run(Brain().main()))
