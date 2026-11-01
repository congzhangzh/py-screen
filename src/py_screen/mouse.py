"""Mouse reports: parse what the outer terminal sends, re-encode it for one pane's app.

The brain asks the outer terminal for SGR reports (mode 1006): `ESC [ < b ; x ; y M`
for a press or motion, `... m` for a release, 1-based cell coordinates of the whole
screen. Each report is routed to the pane under the pointer and rewritten in that
pane's coordinates and in the encoding its app asked for: SGR when the app set 1006,
else the old X10 form `ESC [ M` plus three bytes (each value + 32, so at most 223).

Button code bits (the same in both encodings): 0-2 the button (3 = none, also the X10
release), 4 Shift, 8 Alt, 16 Ctrl, 32 motion, 64 wheel (64 up, 65 down).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

TRACK = (1000, 1002, 1003)   # press/release, + motion with a button held, + any motion
SGR = 1006
_RE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[M([\x20-\xff]{3})", re.S)


@dataclass(frozen=True)
class Event:
    b: int           # button code with its modifier bits
    x: int           # 0-based column on the outer screen
    y: int           # 0-based row
    release: bool

    @property
    def motion(self) -> bool:
        return bool(self.b & 32)

    @property
    def wheel(self) -> bool:
        return bool(self.b & 64)

    @property
    def button(self) -> int:
        return self.b & 3

    @property
    def press(self) -> bool:
        return not (self.release or self.motion or self.wheel)


def has_report(data: bytes) -> bool:
    return b"\x1b[<" in data or b"\x1b[M" in data


def split(data: bytes) -> list[bytes | Event]:
    """Cut one read into key bytes and mouse events, in order."""
    out: list[bytes | Event] = []
    pos = 0
    for m in _RE.finditer(data):
        if m.start() > pos:
            out.append(data[pos:m.start()])
        if m.group(1) is not None:
            out.append(Event(int(m[1]), int(m[2]) - 1, int(m[3]) - 1, m[4] == b"m"))
        else:
            cb, cx, cy = m[5]
            b = cb - 32
            out.append(Event(b, cx - 33, cy - 33, (b & 3) == 3 and not b & 96))
        pos = m.end()
    if pos < len(data):
        out.append(data[pos:])
    return out


def level(mode: Callable[[int], bool]) -> int:
    """The app's tracking mode, from mode(n) = private mode n is on; 0 = no mouse."""
    for m in reversed(TRACK):
        if mode(m):
            return m
    return 0


def accepts(lvl: int, ev: Event) -> bool:
    if not lvl:
        return False
    if ev.motion:
        return lvl == 1003 if ev.button == 3 else lvl >= 1002
    return True


def encode(ev: Event, x: int, y: int, sgr: bool) -> bytes:
    """The event at pane-local 0-based (x, y), as the app expects it."""
    if sgr:
        return f"\x1b[<{ev.b};{x + 1};{y + 1}{'m' if ev.release else 'M'}".encode()
    b = (ev.b | 3) if ev.release else ev.b
    return bytes((0x1B, 0x5B, 0x4D, 32 + b, 32 + min(x + 1, 223), 32 + min(y + 1, 223)))
