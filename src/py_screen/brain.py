"""The brain: everything that changes often. Restartable at any moment.

It holds no pty. It talks to the keeper (window output in, keys and resizes
out), keeps a screen per window, lays windows out in panes, renders them plus
captions and a status line to every attached client, handles keys, and answers
one-shot RPCs (`pys ls`, `send`...). Its layout is saved on every change, so a
reload keeps the splits.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

import pyte
from wcwidth import wcswidth

from . import config as cfgmod
from . import mouse, proto
from .layout import Layout, Pane, Split
from .screen import Screen

FRAME = 1 / 60                   # render at most 60 times a second
CLIENT_HIGH_WATER = 1 << 20      # a client this far behind skips frames, then gets a full redraw
# The screen model is lossless: every byte a window writes is parsed, in order,
# hidden or shown, attached or not. Apps that draw by diff (Claude Code) write only
# the cells they think changed and never rewrite one they think is right, so a
# single lost byte would leave wrong cells for good; a lost mode switch (alternate
# screen, cursor keys, bracketed paste) would break input too.
# Parsing (pyte, ~0.75 MB/s of scrolling text) is the slow part, so the brain acks
# output to the keeper only once it is parsed: a window with CREDIT un-acked bytes is
# not read until the parser catches up, and a flooding app simply waits on its
# write, as under GNU screen. Parsing runs in steps of at most STEP_TIME, shown
# windows first, then the loop goes back to keys and frames, so typing stays quick.
SLICE = 2 * 1024                 # bytes per window per turn (~3 ms of scrolling text)
STEP_TIME = 0.002                # seconds of parsing before the loop gets control back:
                                 # a key's echo crosses ~4 loop turns, each may start with a step
KEY_GRACE = 0.1                  # after a key, hidden windows wait this long: nothing but
                                 # shown windows stands between the key and its echo

# Private modes the outer terminal must mirror, because it produces the input
# (cursor-key mode, bracketed paste). pyte stores private mode N as N << 5.
# The mouse is not mirrored: the brain asks for SGR reports itself and routes
# each one to the pane under the pointer (see mouse.py and Brain.on_mouse).
SYNC_MODES = (1, 2004)
WHEEL_LINES = 3                  # arrow keys per wheel notch for a full-screen app without mouse
COLORS = {"black": 0, "red": 1, "green": 2, "brown": 3, "blue": 4,
          "magenta": 5, "cyan": 6, "white": 7}
WIDGET_COLORS = (33, 36, 32, 37)

UP = (b"\x1b[A", b"\x1bOA", b"k")
DOWN = (b"\x1b[B", b"\x1bOB", b"j")
F2 = (b"\x1bOQ", b"\x1b[12~")
ALT_TAB = (b"\x1b\t",)                         # Alt+Tab: ESC prefix + Tab
ALT_SHIFT_TAB = (b"\x1b\x1b[Z",)               # Alt+Shift+Tab: ESC prefix + back-tab (CSI Z)

HELP = [
    ("c, C-c", "new window in the focused window's directory"),
    ("| / -", "split side by side / stacked, with a new window"),
    ("\\ / _", "empty split side by side / stacked"),
    ("h j k l", "move the focus between panes"),
    ("r, then h l j k =", "resize mode (any other key ends it)"),
    ("=", "equalise panes"),
    ("x / X", "close the pane / kill the window"),
    ("n / p, 0-9", "next / previous window, select by number"),
    ("Space, prefix twice", "toggle to the last window"),
    ("w  \"  F2", "window list (j k, arrows, digits, Enter, Esc)"),
    (",", "rename the window"),
    ("a", "send a literal prefix"),
    ("?", "this help"),
    ("d / C-l", "detach / redraw"),
    ("R / Q", "reload the brain (shells stay) / quit everything"),
]
NO_PREFIX = [
    ("Alt-0 .. Alt-9", "select a window"),
    ("Alt-Tab / Alt-Shift-Tab", "next / previous window, if the desktop passes them"),
    ("F2", "window list"),
]
MOUSE_HELP = [
    ("click", "focus a pane; mouse apps get the click too"),
    ("click the status line", "select that window"),
    ("drag a border", "resize: a │ separator or a caption line"),
    ("wheel", "scrolls the pane under it; arrows for vim, less..."),
    ("Shift+drag", "select text in your terminal (most terminals)"),
]


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), "brain:", *a, file=sys.stderr, flush=True)


def width(s: str) -> int:
    w = wcswidth(s)
    return len(s) if w < 0 else w


def fit(s: str, n: int) -> str:
    """Cut or pad s to exactly n terminal cells."""
    out, w = [], 0
    for ch in s:
        cw = max(0, wcswidth(ch))
        if w + cw > n:
            break
        out.append(ch)
        w += cw
    return "".join(out) + " " * (n - w)


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
    screen: Screen
    stream: pyte.ByteStream
    title: str | None = None     # set by C-a , or a BIND entry; wins over the app's title
    pending: bytearray = field(default_factory=bytearray)   # raw output not parsed yet
    noack: int = 0               # bytes at the front of pending that the keeper replayed:
                                 # it doesn't count them as in flight, so they get no ack

    @classmethod
    def new(cls, wid: int, pid: int, cmd: list[str], cols: int, rows: int) -> "Window":
        screen = Screen(cols, rows)
        return cls(wid, pid, cmd, screen, pyte.ByteStream(screen))

    @property
    def name(self) -> str:
        return self.title or self.screen.title or os.path.basename(self.cmd[0])

    def cwd(self) -> str | None:
        try:
            return os.readlink(f"/proc/{self.pid}/cwd")
        except OSError:
            return None

    def parse(self, n: int) -> int:
        """Parse up to n pending bytes (pyte keeps its parser state between pieces, so
        a cut inside an escape sequence is fine). Returns how many to ack."""
        data = bytes(self.pending[:n])
        del self.pending[:n]
        self.stream.feed(data)
        replayed = min(self.noack, len(data))
        self.noack -= replayed
        return len(data) - replayed


@dataclass
class Client:
    writer: asyncio.StreamWriter
    prefix: int = 0x01
    modes: set[int] = field(default_factory=set)
    stale: bool = True            # needs a full redraw
    armed: bool = False           # the prefix was typed
    mode: str | None = None       # None | "list" | "rename" | "resize"
    list_idx: int = 0
    buf: str = ""
    grab: Pane | None = None      # the pane that got a press: its drag and release follow
    drag: tuple[Split, int] | None = None   # a border being dragged
    help_hits: dict[tuple[int, int], int] = field(default_factory=dict)  # (row, help column) → wid
    help_cw: int = 80             # the help's column width, to map a click to its column
    help_page: int = 0


class Brain:
    def __init__(self) -> None:
        self.wins: dict[int, Window] = {}
        self.order: list[int] = []
        self.layout = Layout()
        self.last: int | None = None             # for C-a Space / C-a C-a
        self.clients: list[Client] = []
        self.cols, self.rows = 80, 24            # outer size; panes share rows - 1
        self.keeper_w: asyncio.StreamWriter | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.tag_n = 0
        self.render_handle: asyncio.TimerHandle | asyncio.Handle | None = None
        self.last_render = 0.0
        self.status_dirty = True
        self.done = asyncio.Event()
        self.exit_code = 0
        self.ready = asyncio.Event()             # replay from the keeper finished
        self.cfg = cfgmod.load_config()
        self.state_path = proto.runtime_dir() / "layout.json"
        self.captions: dict[int, str] = {}
        self.parse_handle: asyncio.Handle | asyncio.TimerHandle | None = None
        self.last_key = 0.0                      # for KEY_GRACE
        self.parse_next = 0                      # round robin start, so no window starves

    # ---- small helpers ------------------------------------------------------
    @property
    def current(self) -> int | None:
        return self.layout.focus.wid

    def visible(self) -> set[int]:
        return {p.wid for p in self.layout.panes() if p.wid is not None}

    def focused_window(self) -> Window | None:
        return self.wins.get(self.current) if self.current is not None else None

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
                    assert isinstance(body, bytes)
                    w = self.wins.get(wid)
                    if w:
                        w.pending += body
                        if not self.ready.is_set():           # the keeper's replay
                            w.noack += len(body)
                        self.schedule_parse()
                    else:                                     # a window we don't know yet
                        self.to_keeper(proto.pack_ctl({"op": "ack", "wid": wid, "n": len(body)}))
                else:
                    self.on_keeper_ctl(body)  # type: ignore[arg-type]
                # No yield per frame: storing a frame is cheap, and the frames already
                # buffered are bounded (a window gets CREDIT un-acked bytes, and acks come
                # only from parse_step, which runs between reads). Reading them all at once
                # gets a quiet window's echo past a flooding window's frames.
        except (asyncio.IncompleteReadError, ConnectionError):
            log("keeper link lost")
            self.quit(1)

    def on_keeper_ctl(self, m: dict) -> None:
        op = m["op"]
        if op == "hello":
            for x in m["windows"]:
                self.add_window(x)
        elif op == "replayed":
            self.load_state()
            self.ready.set()
            # A new brain knows only the keeper's last 2 MB of each window; if the ring
            # wrapped, what came before is gone. The nudge (a SIGWINCH) asks full-screen
            # apps to repaint. Open: keep the screens across a planned reload instead.
            asyncio.get_running_loop().create_task(self.nudge(list(self.wins.values())))
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
        if self.layout.focus.wid is None and len(self.layout.panes()) == 1:
            self.layout.focus.wid = w.wid
        self.status_dirty = True
        return w

    def remove_window(self, wid: int) -> None:
        self.wins.pop(wid, None)
        if wid in self.order:
            self.order.remove(wid)
        if self.last == wid:
            self.last = None
        for p in self.layout.panes():
            if p.wid == wid:
                p.wid = self.hidden_candidate()
        if not self.order:
            log("last window exited; shutting down")
            self.broadcast_ctl({"op": "bye"})
            self.to_keeper(proto.pack_ctl({"op": "shutdown"}))
            self.state_path.unlink(missing_ok=True)
            self.quit(0)
            return
        self.relayout()

    def hidden_candidate(self) -> int | None:
        vis = self.visible()
        if self.last is not None and self.last in self.wins and self.last not in vis:
            return self.last
        return next((w for w in reversed(self.order) if w not in vis), None)

    def schedule_parse(self) -> None:
        if isinstance(self.parse_handle, asyncio.TimerHandle):   # waiting out KEY_GRACE:
            self.parse_handle.cancel()                           # new output may be shown
            self.parse_handle = None
        if self.parse_handle is None:
            self.parse_handle = asyncio.get_running_loop().call_soon(self.parse_step)

    def parse_step(self) -> None:
        """Parse pending output for up to STEP_TIME: shown windows first, then the
        others round robin, SLICE bytes each per turn; ack what was parsed."""
        self.parse_handle = None
        todo = [w for w in self.wins.values() if w.pending]
        if not todo:
            return
        shown = self.visible() if self.clients else set()
        hidden = [w for w in todo if w.wid not in shown]
        typing = time.monotonic() - self.last_key
        if typing < KEY_GRACE and hidden:          # keys first: hidden output waits
            hidden = []
            if not any(w.wid in shown for w in todo):
                self.parse_handle = asyncio.get_running_loop().call_later(
                    KEY_GRACE - typing, self.parse_step)
                return
        k = self.parse_next % len(hidden) if hidden else 0
        self.parse_next += 1
        todo = [w for w in todo if w.wid in shown] + hidden[k:] + hidden[:k]
        deadline = time.monotonic() + STEP_TIME
        acks: dict[int, int] = {}
        parsed_shown = parsed_hidden = False
        while todo and time.monotonic() < deadline:
            for w in todo:
                acks[w.wid] = acks.get(w.wid, 0) + w.parse(SLICE)
                if w.wid in shown:
                    parsed_shown = True
                else:
                    parsed_hidden = True
                if time.monotonic() >= deadline:
                    break
            todo = [w for w in todo if w.pending]
        for wid, n in acks.items():
            if n and wid in self.wins:
                self.to_keeper(proto.pack_ctl({"op": "ack", "wid": wid, "n": n}))
        if parsed_hidden:
            self.status_dirty = True              # a hidden window's title may have changed
        if self.clients and (parsed_shown or parsed_hidden):
            self.schedule_render()
        if any(w.pending for w in self.wins.values()):
            self.schedule_parse()

    def parse_all(self, w: Window) -> None:
        """Parse everything w has pending now (a capture wants the exact screen)."""
        n = w.parse(len(w.pending))
        if n:
            self.to_keeper(proto.pack_ctl({"op": "ack", "wid": w.wid, "n": n}))

    async def nudge(self, wins: list[Window]) -> None:
        """Change each pty's size by one row and back: SIGWINCH makes full-screen apps
        repaint what replayed or dropped bytes could not rebuild."""
        for w in wins:
            s = w.screen
            self.to_keeper(proto.pack_ctl({"op": "resize", "wid": w.wid,
                                           "cols": s.columns, "rows": max(1, s.lines - 1)}))
        await asyncio.sleep(0.05)
        for w in wins:
            s = w.screen
            self.to_keeper(proto.pack_ctl({"op": "resize", "wid": w.wid,
                                           "cols": s.columns, "rows": s.lines}))

    async def spawn(self, cmd: list[str] | None = None, cwd: str | None = None,
                    size: tuple[int, int] | None = None) -> Window:
        self.tag_n += 1
        tag = f"t{self.tag_n}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[tag] = fut
        cmd = cmd or [os.environ.get("SHELL", "/bin/sh")]
        f = self.layout.focus
        cols, rows = size or ((f.w, f.text_h) if f.w else (self.cols, self.rows - 2))
        self.to_keeper(proto.pack_ctl({"op": "spawn", "cmd": cmd, "cwd": cwd, "tag": tag,
                                       "cols": cols, "rows": rows}))
        return await asyncio.wait_for(fut, 5)

    # ---- layout, selection ---------------------------------------------------
    def set_size(self, cols: int, rows: int) -> None:
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self.relayout()

    def relayout(self) -> None:
        """Place panes, give each shown window its pane's size, redraw, save."""
        self.layout.place(self.cols, self.rows - 1)
        for p in self.layout.panes():
            w = self.wins.get(p.wid) if p.wid is not None else None
            if w and (w.screen.columns, w.screen.lines) != (p.w, p.text_h):
                w.screen.resize(p.text_h, p.w)
                self.to_keeper(proto.pack_ctl({"op": "resize", "wid": w.wid,
                                               "cols": p.w, "rows": p.text_h}))
        self.mark_all_stale()
        self.save_state()

    def select(self, wid: int) -> None:
        """Show wid in the focused pane, or move the focus to the pane already showing it."""
        if wid not in self.wins or wid == self.current:
            return
        if self.current is not None:
            self.last = self.current
        pane = self.layout.pane_of(wid)
        if pane:
            self.layout.focus = pane
        else:
            self.layout.focus.wid = wid
        self.relayout()

    def step(self, d: int) -> None:
        if self.order:
            i = self.order.index(self.current) if self.current in self.order else -1
            self.select(self.order[(i + d) % len(self.order)])

    def mark_all_stale(self) -> None:
        for c in self.clients:
            c.stale = True
        self.status_dirty = True
        self.schedule_render()

    # ---- state across reloads ------------------------------------------------
    def save_state(self) -> None:
        def enc(n):
            if isinstance(n, Pane):
                return {"pane": n.wid, "focus": n is self.layout.focus}
            return {"vertical": n.vertical, "weights": n.weights,
                    "children": [enc(c) for c in n.children]}
        state = {"layout": enc(self.layout.root), "last": self.last,
                 "titles": {w.wid: w.title for w in self.wins.values() if w.title},
                 "order": self.order}
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            tmp.replace(self.state_path)
        except OSError as e:
            log("save_state:", e)

    def load_state(self) -> None:
        try:
            state = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return
        focus: list[Pane] = []

        def dec(d):
            if "pane" in d:
                p = Pane(d["pane"] if d["pane"] in self.wins else None)
                if d.get("focus"):
                    focus.append(p)
                return p
            return Split(d["vertical"], [dec(c) for c in d["children"]], list(d["weights"]))
        try:
            self.layout.root = dec(state["layout"])
        except (KeyError, TypeError):
            return
        panes = self.layout.panes()
        self.layout.focus = focus[0] if focus else panes[0]
        seen: set[int] = set()
        for p in panes:                           # a window shows in one pane at most
            if p.wid in seen:
                p.wid = None
            elif p.wid is not None:
                seen.add(p.wid)
        if self.layout.focus.wid is None and self.order:
            self.layout.focus.wid = self.hidden_candidate()
        self.last = state.get("last") if state.get("last") in self.wins else None
        for k, t in state.get("titles", {}).items():
            if int(k) in self.wins:
                self.wins[int(k)].title = t
        order = [w for w in state.get("order", []) if w in self.wins]
        self.order = order + [w for w in self.order if w not in order]

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

    def line(self, screen: Screen, y: int, p: Pane) -> str:
        row = screen.buffer[y]
        out = [f"\x1b[{p.y + y + 1};{p.x + 1}H"]
        last = None
        for x in range(min(screen.columns, p.w)):
            ch = row[x]
            if ch.data == "":                 # right half of a wide character
                continue
            s = sgr(ch)
            if s is not last:
                out.append(s)
                last = s
            out.append(ch.data)
        out.append("\x1b[0m")
        return "".join(out)

    def caption(self, p: Pane) -> str:
        w = self.wins.get(p.wid) if p.wid is not None else None
        label = f" {p.wid} {w.name} " if w else " (empty) "
        rest = f" {w.cwd() or ''} " if w else ""
        focused = p is self.layout.focus
        a, b = ("\x1b[0;30;43m", "\x1b[0;33;40m") if focused else ("\x1b[0;37;100m", "\x1b[0;90;40m")
        lw = min(width(label), p.w)
        return (f"\x1b[{p.y + p.h};{p.x + 1}H{a}{fit(label, lw)}{b}{fit(rest, p.w - lw)}\x1b[0m")

    def pane_full(self, p: Pane) -> str:
        w = self.wins.get(p.wid) if p.wid is not None else None
        out = []
        if w:
            s = w.screen
            out.extend(self.line(s, y, p) for y in range(min(s.lines, p.text_h)))
        else:
            out.extend(f"\x1b[{p.y + y + 1};{p.x + 1}H\x1b[0m{' ' * p.w}" for y in range(p.text_h))
        out.append(self.caption(p))
        return "".join(out)

    def separators(self) -> str:
        return "".join(f"\x1b[{y + i + 1};{x + 1}H\x1b[0;90m│\x1b[0m"
                       for x, y, h in self.layout.separators for i in range(h))

    def status_line(self, c: Client | None = None) -> str:
        row = f"\x1b[{self.rows};1H"
        if c and c.mode == "rename":
            return row + "\x1b[0;30;46m" + fit(f" title for window {self.current}: {c.buf}▏",
                                               self.cols) + "\x1b[0m"
        if c and c.mode == "resize":
            return row + "\x1b[0;30;46m" + fit(" resize: h/l width, j/k height, = equal, "
                                               "other key ends", self.cols) + "\x1b[0m"
        right_parts, right_w = self.status_right()
        left, left_w = "\x1b[0;30;46m pys \x1b[0m", 5
        used = 0
        for wid, label, x in self.status_tabs(right_w):
            left += (f"\x1b[0;30;43m{label}\x1b[0m" if wid == self.current else label)
            used = x + width(label) - left_w
        gap = " " * max(0, self.cols - left_w - used - right_w)
        return row + left + gap + "".join(right_parts) + "\x1b[0m"

    def status_right(self) -> tuple[list[str], int]:
        right_parts, right_w = [], 0
        texts = [wd.text for wd in self.cfg.widgets if wd.text]
        if self.cfg.error:
            texts.insert(0, self.cfg.error)
        for i, t in enumerate(texts):
            seg = f" {t} "
            right_parts.append(f"\x1b[0;{WIDGET_COLORS[i % len(WIDGET_COLORS)]};40m{seg}")
            right_w += width(seg)
        return right_parts, right_w

    def status_tabs(self, right_w: int) -> list[tuple[int, str, int]]:
        """The window labels that fit in the status line: (wid, label, start column)."""
        x, budget, out = 5, max(0, self.cols - 5 - right_w), []
        for wid in self.order:
            w = self.wins[wid]
            flag = "*" if wid == self.current else ("-" if wid == self.last else "")
            label = f" {wid}{flag} {w.name} "
            if x - 5 + width(label) > budget:
                break
            out.append((wid, label, x))
            x += width(label)
        return out

    def window_list(self, c: Client) -> str:
        area = self.rows - 1
        rows = [fit(" Num  Title" + " " * 20 + "Dir", self.cols)]
        for i, wid in enumerate(self.order):
            w = self.wins[wid]
            rows.append((i, fit(f" {wid:>3}  {fit(w.name, 24)} {w.cwd() or ''}", self.cols)))
        out = [f"\x1b[1;1H\x1b[0;30;46m{rows[0]}\x1b[0m"]
        for y, (i, text) in enumerate(rows[1:area], start=1):
            attr = "\x1b[0;30;43m" if i == c.list_idx else "\x1b[0m"
            out.append(f"\x1b[{y + 1};1H{attr}{text}\x1b[0m")
        for y in range(len(rows), area):
            out.append(f"\x1b[{y + 1};1H\x1b[0m{' ' * self.cols}")
        return "".join(out)

    def help_sections(self, c: Client) -> list[list[str]]:
        """The help as sections: the windows first (what you'd switch to), then the keys."""
        home = os.path.expanduser("~")
        wins = [" windows (a digit or a click selects one)"]
        for wid in self.order:
            w = self.wins[wid]
            flag = "*" if wid == self.current else ("-" if wid == self.last else " ")
            cwd = w.cwd() or ""
            if home != "/" and (cwd == home or cwd.startswith(home + "/")):
                cwd = "~" + cwd[len(home):]
            wins.append(f"   {wid:>2}{flag} {fit(w.name, 20)} {cwd}")
        pfx = "C-" + chr(c.prefix + 0x60) if c.prefix < 0x1B else "C-" + chr(c.prefix + 0x40)
        out = [wins, [f" keys: prefix {pfx}, then"] + [f"   {k:<23} {v}" for k, v in HELP]]
        if self.cfg.bind:
            out.append([" from your config (prefix, then)"]
                       + [f"   {k:<23} {t}: {' '.join(a)}" for k, (t, a) in self.cfg.bind.items()])
        out.append([" without the prefix"] + [f"   {k:<23} {v}" for k, v in NO_PREFIX])
        if self.cfg.mouse:
            out.append([" mouse"] + [f"   {k:<23} {v}" for k, v in MOUSE_HELP])
        return out

    def help_columns(self, c: Client) -> tuple[list[list[tuple[str, int | None]]], int, int]:
        """Flow the help sections into columns of the help's height. A section that runs
        past the bottom continues at the top of the next column under its heading, marked
        "(more)"; a heading never sits alone at the bottom. Returns the
        columns, how many fit side by side (only as many as fit without cutting a key's
        text) and the height. Rows carry the wid of a window row, for digits and clicks."""
        h = max(1, self.rows - 1 - 3)            # between title + blank and the footer
        secs = self.help_sections(c)
        need = max(width(t) for sec in secs[1:] for t in sec) + 2
        per_page = max(1, min(3, self.cols // need))
        cols: list[list[tuple[str, int | None]]] = [[]]
        for i, sec in enumerate(secs):
            wids: list[int | None] = [None] + list(self.order) if i == 0 else [None] * len(sec)
            rows = list(zip(sec, wids))
            if cols[-1] and len(cols[-1]) + 3 > h:
                cols.append([])                  # no room for the heading and a line
            elif cols[-1]:
                cols[-1].append(("", None))
            cols[-1].append(rows[0])
            for r in rows[1:]:
                if len(cols[-1]) >= h:
                    cols.append([(rows[0][0] + " (more)", None)])
                cols[-1].append(r)
        return cols, per_page, h

    def help_pages(self, c: Client) -> int:
        cols, per_page, _ = self.help_columns(c)
        return -(-len(cols) // per_page)

    def help_screen(self, c: Client) -> str:
        cols, per_page, h = self.help_columns(c)
        pages = -(-len(cols) // per_page)
        c.help_page %= pages
        shown = cols[c.help_page * per_page:(c.help_page + 1) * per_page]
        cw = self.cols // per_page
        area = self.rows - 1
        c.help_hits, c.help_cw = {}, cw
        lines = [" py-screen help", ""]
        for k in range(h):
            parts = []
            for j, col in enumerate(shown):
                text, wid = col[k] if k < len(col) else ("", None)
                if wid is not None:
                    c.help_hits[(k + 2, j)] = wid
                parts.append(fit(text, cw))
            lines.append("".join(parts))
        foot = " a digit selects a window · other keys close"
        if pages > 1:
            foot = f" page {c.help_page + 1}/{pages} · Space: more ·" + foot
        lines.append(foot)
        out = []
        for y in range(area):
            attr = "\x1b[0;30;46m" if y == 0 else "\x1b[0m"
            text = lines[y] if y < len(lines) else ""
            out.append(f"\x1b[{y + 1};1H{attr}{fit(text, self.cols)}\x1b[0m")
        return "".join(out)

    def render(self) -> None:
        self.render_handle = None
        self.last_render = time.monotonic()
        if not self.clients:
            return                                # parse later, when someone looks
        panes = [p for p in self.layout.panes() if p.wid in self.wins]
        dirty_parts = []
        for p in panes:
            w = self.wins[p.wid]  # type: ignore[index]
            s = w.screen
            dirty = sorted(y for y in s.dirty if y < p.text_h)
            s.dirty.clear()
            dirty_parts.extend(self.line(s, y, p) for y in dirty)
        captions = {id(p): self.caption(p) for p in self.layout.panes()}
        dirty_parts.extend(cap for k, cap in captions.items() if self.captions.get(k) != cap)
        self.captions = captions                  # a title or cwd change redraws its caption
        body_dirty = "".join(dirty_parts)
        body_full: str | None = None
        fw = self.focused_window()
        f = self.layout.focus
        want: set[int] = set()
        tail = ""
        lvl = max(mouse.level(fw.screen.mode) if fw else 0, 1002 if self.cfg.mouse else 0)
        mouse_want = {lvl, mouse.SGR} if lvl else set()
        overlay_mouse = {1000, mouse.SGR} if self.cfg.mouse else set()
        if fw:
            s = fw.screen
            want = {m for m in SYNC_MODES if (m << 5) in s.mode}
            cx, cy = min(s.cursor.x, s.columns - 1), min(s.cursor.y, f.text_h - 1)
            tail = f"\x1b[{f.y + cy + 1};{f.x + cx + 1}H" + ("" if s.cursor.hidden else "\x1b[?25h")
        for c in self.clients:
            if c.writer.is_closing():
                continue
            if c.writer.transport.get_write_buffer_size() > CLIENT_HIGH_WATER:
                c.stale = True                # slow client: skip, redraw fully later
                continue
            if c.stale:
                if body_full is None:
                    body_full = ("\x1b[0m\x1b[2J" + "".join(self.pane_full(p) for p in self.layout.panes())
                                 + self.separators())
                body = body_full + self.status_line(c)
                c.stale = False
            else:
                body = body_dirty + (self.status_line(c) if self.status_dirty else "")
            if c.mode == "list":
                body += self.window_list(c)
            elif c.mode == "help":
                body += self.help_screen(c)
            overlay = c.mode in ("list", "help")
            cur = "" if overlay else tail
            mw = overlay_mouse if overlay else want | mouse_want
            modes_s = "".join(f"\x1b[?{m}h" for m in mw - c.modes)
            modes_s += "".join(f"\x1b[?{m}l" for m in c.modes - mw)
            c.modes = mw
            frame = "\x1b[?2026h\x1b[?25l" + modes_s + body + cur + "\x1b[?2026l"
            c.writer.write(proto.pack(proto.DATA, 0, frame.encode()))
        self.status_dirty = False

    def broadcast_ctl(self, m: dict) -> None:
        for c in self.clients:
            if not c.writer.is_closing():
                c.writer.write(proto.pack_ctl(m))

    # ---- widgets --------------------------------------------------------------
    async def widget_loop(self, wd: cfgmod.Widget) -> None:
        while True:
            try:
                fw = self.focused_window()
                text = await wd.update({"cwd": fw.cwd() if fw else None})
            except Exception as e:  # a widget never takes the brain down
                text = f"!{type(e).__name__}"
            if text != wd.text:
                wd.text = text
                self.status_dirty = True
                if self.clients:
                    self.schedule_render()
            await asyncio.sleep(wd.every)

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

    def client_prefix(self, hello: dict) -> int:
        name = hello.get("prefix") or self.cfg.prefix
        if not name:
            name = "C-]" if hello.get("in_screen") else "C-a"
        try:
            return cfgmod.prefix_byte(name)
        except (IndexError, ValueError):
            return 0x01

    async def client_loop(self, r: asyncio.StreamReader, wr: asyncio.StreamWriter,
                          hello: dict) -> None:
        c = Client(wr, prefix=self.client_prefix(hello))
        self.clients.append(c)
        self.set_size(hello["cols"], hello["rows"])
        self.layout.place(self.cols, self.rows - 1)
        if not self.wins:
            w = await self.spawn(cwd=hello.get("cwd"))
            self.layout.focus.wid = w.wid
        self.relayout()
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

    def send_keys(self, data: bytes) -> None:
        if data and self.current is not None:
            self.to_keeper(proto.pack(proto.DATA, self.current, data))

    async def keys(self, c: Client, data: bytes) -> None:
        self.last_key = time.monotonic()
        if mouse.has_report(data):
            for part in mouse.split(data):
                if isinstance(part, bytes):
                    await self.key_bytes(c, part)
                else:
                    self.on_mouse(c, part)
            return
        await self.key_bytes(c, data)

    # ---- mouse --------------------------------------------------------------
    def to_pane(self, p: Pane, ev: mouse.Event) -> bool:
        """Give ev to p's app in p's coordinates, if the app takes that kind of event."""
        w = self.wins.get(p.wid) if p.wid is not None else None
        if not w or not mouse.accepts(mouse.level(w.screen.mode), ev):
            return False
        x = min(max(ev.x - p.x, 0), p.w - 1)
        y = min(max(ev.y - p.y, 0), p.text_h - 1)
        sgr = (mouse.SGR << 5) in w.screen.mode
        self.to_keeper(proto.pack(proto.DATA, w.wid, mouse.encode(ev, x, y, sgr)))
        return True

    def focus_pane(self, p: Pane) -> None:
        if p is self.layout.focus:
            return
        if p.wid is not None:
            self.select(p.wid)                    # also keeps "last" right
        else:
            self.layout.focus = p
            self.relayout()

    def wheel_keys(self, p: Pane, ev: mouse.Event) -> None:
        """A full-screen app that doesn't take the mouse (less, man, vim without
        mouse=a) scrolls with arrow keys, as terminals do for the alternate screen."""
        w = self.wins.get(p.wid) if p.wid is not None else None
        if not w or not w.screen.in_alt or ev.button > 1:
            return
        app_keys = (1 << 5) in w.screen.mode       # DECCKM: the app wants ESC O A
        key = (b"\x1bO" if app_keys else b"\x1b[") + (b"A" if ev.button == 0 else b"B")
        self.to_keeper(proto.pack(proto.DATA, w.wid, key * WHEEL_LINES))

    def on_mouse(self, c: Client, ev: mouse.Event) -> None:
        if c.mode == "help":
            if ev.wheel:
                if not ev.release and self.help_pages(c) > 1:
                    c.help_page += 1 if ev.button == 1 else -1
                    c.stale = True
                    self.schedule_render()
            elif ev.press:
                wid = c.help_hits.get((ev.y, ev.x // max(1, c.help_cw)))
                c.mode, c.stale = None, True
                if wid is not None:
                    self.select(wid)
                self.schedule_render()
            return
        if c.mode == "list":
            if ev.wheel:
                d = -1 if ev.button == 0 else 1
                c.list_idx = min(max(0, c.list_idx + d), len(self.order) - 1)
            elif ev.press and 1 <= ev.y <= len(self.order):
                c.mode = None
                self.select(self.order[ev.y - 1])
            c.stale = True
            self.schedule_render()
            return
        if c.mode:                                # rename / resize: keys only
            return
        if c.drag:                                # a border follows the pointer
            split, i = c.drag
            if ev.release:
                c.drag = None
                self.save_state()
            elif ev.motion:
                self.layout.drag(split, i, ev.x if split.vertical else ev.y)
                self.relayout()
            return
        if c.grab:                                # drag and release go where the press went
            if ev.release or (ev.motion and ev.button != 3):
                self.to_pane(c.grab, ev)
                if ev.release:
                    c.grab = None
                return
            c.grab = None
        p = self.layout.pane_at(ev.x, ev.y) if ev.y < self.rows - 1 else None
        in_text = p is not None and ev.y < p.y + p.text_h
        if ev.wheel:
            if in_text and not self.to_pane(p, ev):
                self.wheel_keys(p, ev)
            return
        if ev.motion:                             # no button held: focused app only
            if in_text and p is self.layout.focus:
                self.to_pane(p, ev)
            return
        if not ev.press:
            return
        if ev.y == self.rows - 1:                 # the status line: pick a window
            for wid, label, x in self.status_tabs(self.status_right()[1]):
                if x <= ev.x < x + width(label):
                    self.select(wid)
            return
        if p is not None:
            self.focus_pane(p)
            if in_text:
                if self.to_pane(p, ev):
                    c.grab = p
                return
        if ev.button == 0 and (b := self.layout.boundary_at(ev.x, ev.y)):
            c.drag = b

    async def key_bytes(self, c: Client, data: bytes) -> None:
        if c.mode:
            await self.mode_keys(c, data)
            return
        # One keypress arrives as one read: ESC+digit is Alt-digit, ESC O Q is F2.
        if not c.armed and len(data) == 2 and data[0] == 0x1B and 0x30 <= data[1] <= 0x39:
            self.select(data[1] - 0x30)
            return
        if not c.armed and data in F2:
            self.open_list(c)
            return
        if not c.armed and data in ALT_TAB:
            self.step(1)
            return
        if not c.armed and data in ALT_SHIFT_TAB:
            self.step(-1)
            return
        out = bytearray()
        for b in data:
            if c.armed:
                c.armed = False
                self.send_keys(bytes(out))
                out.clear()
                await self.command(c, b)
                if c.mode:                       # the rest of this read belongs to the mode
                    break
            elif b == c.prefix:
                c.armed = True
            else:
                out.append(b)
        self.send_keys(bytes(out))

    async def new_window_here(self) -> Window:
        fw = self.focused_window()
        return await self.spawn(cwd=fw.cwd() if fw else None)

    async def split(self, vertical: bool, with_window: bool) -> None:
        fw = self.focused_window()
        cwd = fw.cwd() if fw else None
        self.layout.split(vertical, None)
        self.layout.place(self.cols, self.rows - 1)
        if with_window:
            w = await self.spawn(cwd=cwd)
            self.layout.focus.wid = w.wid
        self.relayout()

    async def command(self, c: Client, b: int) -> None:
        key = chr(b)
        if key in self.cfg.bind:
            title, argv = self.cfg.bind[key]
            fw = self.focused_window()
            w = await self.spawn(argv, cwd=fw.cwd() if fw else None)
            w.title = title
            self.select(w.wid)
        elif b == c.prefix or key == " ":
            if self.last is not None:
                self.select(self.last)
        elif key == "a":
            self.send_keys(bytes([c.prefix]))
        elif key in ("c", "\x03"):
            w = await self.new_window_here()
            self.select(w.wid)
        elif key == "|":
            await self.split(True, True)
        elif key == "-":
            await self.split(False, True)
        elif key == "\\":
            await self.split(True, False)
        elif key == "_":
            await self.split(False, False)
        elif key in "hjkl":
            self.layout.move_focus(key)
            self.relayout()
        elif key == "x":
            if self.layout.remove(self.layout.focus):
                self.relayout()
        elif key == "X" and self.current is not None:
            self.to_keeper(proto.pack_ctl({"op": "kill", "wid": self.current}))
        elif key == "r":
            c.mode = "resize"
            self.mark_all_stale()
        elif key == "=":
            self.layout.equalize()
            self.relayout()
        elif key in ('w', '"'):
            self.open_list(c)
        elif key == "?":
            c.mode, c.help_page = "help", 0
            self.schedule_render()
        elif key == ",":
            c.mode, c.buf = "rename", ""
            self.status_dirty = True
            self.schedule_render()
        elif key == "n":
            self.step(1)
        elif key == "p":
            self.step(-1)
        elif key.isdigit():
            self.select(int(key))
        elif key == "d":
            c.writer.write(proto.pack_ctl({"op": "detached"}))
            c.writer.close()
        elif key == "\x0c":
            self.mark_all_stale()
        elif key == "R":
            self.reload()
        elif key == "Q":
            self.broadcast_ctl({"op": "bye"})
            self.to_keeper(proto.pack_ctl({"op": "shutdown"}))
            self.state_path.unlink(missing_ok=True)
            self.quit(0)

    def open_list(self, c: Client) -> None:
        c.mode = "list"
        c.list_idx = self.order.index(self.current) if self.current in self.order else 0
        self.schedule_render()

    async def mode_keys(self, c: Client, data: bytes) -> None:
        if c.mode == "help":                     # Space pages; a digit selects; others close
            c.stale = True
            if data in (b" ", *DOWN) and self.help_pages(c) > 1:
                c.help_page += 1
            elif data in UP and self.help_pages(c) > 1:
                c.help_page -= 1
            else:
                c.mode = None
                if len(data) == 1 and chr(data[0]).isdigit():
                    self.select(data[0] - 0x30)
        elif c.mode == "list":
            if data in UP:
                c.list_idx = max(0, c.list_idx - 1)
            elif data in DOWN:
                c.list_idx = min(len(self.order) - 1, c.list_idx + 1)
            elif data in (b"\r", b"\n") and self.order:
                c.mode = None
                self.select(self.order[c.list_idx])
            elif len(data) == 1 and chr(data[0]).isdigit():
                c.mode = None
                self.select(data[0] - 0x30)
            elif data in (b"\x1b", b"q", bytes([c.prefix])):
                c.mode = None
            c.stale = True
        elif c.mode == "rename":
            if data == b"\x1b":                   # a lone Esc cancels; Esc sequences don't
                c.mode = None
            for ch in data.decode(errors="ignore"):  # a paste or fast typing: one read
                if c.mode != "rename":
                    break
                if ch in "\r\n":
                    fw = self.focused_window()
                    if fw:
                        fw.title = c.buf or None
                    c.mode = None
                    self.save_state()
                elif ch in "\x7f\x08":
                    c.buf = c.buf[:-1]
                elif ch == "\x15":
                    c.buf = ""
                elif ch.isprintable():
                    c.buf += ch
            self.status_dirty = True
        elif c.mode == "resize":
            key = data.decode(errors="ignore")
            if key in ("h", "l"):
                self.layout.resize(True, -5 if key == "h" else 5)
                self.relayout()
            elif key in ("j", "k"):
                self.layout.resize(False, 3 if key == "j" else -3)
                self.relayout()
            elif key == "=":
                self.layout.equalize()
                self.relayout()
            else:
                c.mode = None
            self.status_dirty = True
        self.schedule_render()

    def reload(self) -> None:
        log("reload")
        self.save_state()
        self.broadcast_ctl({"op": "reloading"})
        self.quit(proto.RELOAD_EXIT)

    # ---- RPC ----------------------------------------------------------------
    async def rpc(self, m: dict) -> dict:
        cmd = m.get("cmd")
        if cmd == "ls":
            return {"ok": True, "pid": os.getpid(), "current": self.current,
                    "clients": len(self.clients), "size": [self.cols, self.rows],
                    "windows": [{"wid": w.wid, "pid": w.pid, "name": w.name,
                                 "cmd": w.cmd, "cwd": w.cwd(),
                                 "pending": len(w.pending)} for w in
                                (self.wins[i] for i in self.order)],
                    "panes": [{"wid": p.wid, "x": p.x, "y": p.y, "w": p.w, "h": p.h,
                               "focus": p is self.layout.focus} for p in self.layout.panes()]}
        if cmd == "send":
            self.to_keeper(proto.pack(proto.DATA, m["wid"], m["text"].encode()))
            return {"ok": True}
        if cmd == "capture":
            w = self.wins[m.get("wid", self.current)]
            self.parse_all(w)
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
            self.state_path.unlink(missing_ok=True)
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
        widget_tasks = [loop.create_task(self.widget_loop(wd)) for wd in self.cfg.widgets]
        log("up, pid", os.getpid())
        await self.done.wait()
        server.close()
        for t in widget_tasks:
            t.cancel()
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
