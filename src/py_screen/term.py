"""The screen model the brain talks to: one Term per window, fed every byte it writes.

The brain needs little from a screen: feed it bytes, resize it, ask which rows changed,
get a row as text with its colours (ready to send to a client), and read the cursor,
the private modes, the title and whether the alternate screen is on. Everything about
cells, pens and parser state stays behind this interface, so the engine can change:

- "pyte": screen.Screen + screen.Stream, pure Python, always there.
- "vterm": libvterm, through the _vterm C extension, when it was built (it links the
  system's libvterm; a machine without one gets "pyte").

PYS_TERM=pyte or PYS_TERM=vterm picks one; otherwise the fastest one available.
"""

from __future__ import annotations

import os
from typing import Protocol

import pyte

from .screen import Screen, Stream


class Term(Protocol):
    engine: str
    columns: int
    lines: int
    title: str
    in_alt: bool
    cursor: tuple[int, int]
    cursor_hidden: bool

    def feed(self, data: bytes) -> None: ...
    def resize(self, lines: int, columns: int) -> None: ...
    def take_dirty(self) -> set[int]: ...
    def row(self, y: int, width: int) -> str: ...
    def mode(self, n: int) -> bool: ...
    @property
    def display(self) -> list[str]: ...


# ---- pyte ---------------------------------------------------------------------
COLORS = {"black": 0, "red": 1, "green": 2, "brown": 3, "blue": 4,
          "magenta": 5, "cyan": 6, "white": 7}


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


class PyteTerm:
    engine = "pyte"

    def __init__(self, columns: int, lines: int) -> None:
        self.screen = Screen(columns, lines)
        self.stream = Stream(self.screen)

    def feed(self, data: bytes) -> None:
        self.stream.feed(data)

    def resize(self, lines: int, columns: int) -> None:
        self.screen.resize(lines, columns)

    @property
    def columns(self) -> int:
        return self.screen.columns

    @property
    def lines(self) -> int:
        return self.screen.lines

    def take_dirty(self) -> set[int]:
        d, self.screen.dirty = self.screen.dirty, set()
        return d

    def row(self, y: int, width: int) -> str:
        """Row y, cut to width cells, as SGR + text; ends with the attributes reset."""
        line = self.screen.buffer[y]
        out, last = [], None
        for x in range(min(self.screen.columns, width)):
            ch = line[x]
            if ch.data == "":                 # right half of a wide character
                continue
            s = sgr(ch)
            if s is not last:
                out.append(s)
                last = s
            out.append(ch.data)
        out.append("\x1b[0m")
        return "".join(out)

    def mode(self, n: int) -> bool:
        """Private mode n (DECSET n) is on. pyte keeps private mode N as N << 5."""
        return (n << 5) in self.screen.mode

    @property
    def title(self) -> str:
        return self.screen.title

    @property
    def in_alt(self) -> bool:
        return self.screen.in_alt

    @property
    def cursor(self) -> tuple[int, int]:
        return self.screen.cursor.x, self.screen.cursor.y

    @property
    def cursor_hidden(self) -> bool:
        return self.screen.cursor.hidden

    @property
    def display(self) -> list[str]:
        return self.screen.display


# ---- choosing ---------------------------------------------------------------
ENGINES: dict[str, type] = {"pyte": PyteTerm}
try:
    from .vterm import VTermTerm
except ImportError:                      # not built here: the Python engine only
    pass
else:
    ENGINES["vterm"] = VTermTerm


def engine() -> str:
    want = os.environ.get("PYS_TERM")
    if want:
        if want not in ENGINES:
            raise SystemExit(f"PYS_TERM={want}: not available here (have: {', '.join(ENGINES)})")
        return want
    return "vterm" if "vterm" in ENGINES else "pyte"


def new(columns: int, lines: int) -> Term:
    return ENGINES[engine()](columns, lines)
