"""Copy mode: a cursor over one window's scrollback and screen, to search, select and copy.

Prefix [ starts it on the focused pane, like GNU screen and tmux. The keys are vi's:

    h j k l, arrows      move                0 ^ $               start, first word, end
    w b                  next / previous word
    C-u C-d, C-b C-f     half page / page    PgUp PgDn           page
    g G                  oldest row / newest row
    / ?                  search down / up    n N                 again, same / other way
    v, Space             start (or drop) a selection
    y, Enter             copy the selection (or the cursor's row) and leave
    Esc                  drop the selection, or leave     q, prefix   leave

Copy mode shows the window as it was when it started (Frozen), as GNU screen and tmux do.
The app runs on and its output is parsed into the live term, but nothing of it shows
until copy mode ends: an app that redraws in place (Claude Code's fullscreen mode, a
spinner, top) would otherwise change the text under the cursor and the selection.

Rows are addressed as the term addresses them: y >= 0 is the screen, y < 0 the scrollback
(-1 = the newest row there), all as of the start. The screen is a snapshot (one screen of
strings); a scrollback row is read from the live ring, past the rows pushed since (they
were screen rows of the snapshot), so the history costs nothing to freeze. A row the ring
dropped meanwhile (ED 3, a full ring) is gone, and the top moves down to match.

This module knows nothing about panes, clients or frames: the brain feeds it keys and
asks it where the cursor is, what to show and what was copied.
"""

from __future__ import annotations

import re

from wcwidth import wcwidth

from .term import Term

SGR = re.compile(r"\x1b\[[0-9;]*m")
TOKENS = re.compile(r"\x1b\[[0-9;]*[~A-Za-z]|\x1bO[A-Za-z]|\x1b|.", re.S)
WORD = re.compile(r"\w+|[^\w\s]+")

UP, DOWN = ("\x1b[A", "\x1bOA", "k"), ("\x1b[B", "\x1bOB", "j")
LEFT, RIGHT = ("\x1b[D", "\x1bOD", "h", "\x7f", "\x08"), ("\x1b[C", "\x1bOC", "l")
PGUP, PGDN = ("\x1b[5~", "\x02"), ("\x1b[6~", "\x06")
HOME, END = ("\x1b[H", "\x1bOH", "\x1b[1~", "0"), ("\x1b[F", "\x1bOF", "\x1b[4~", "$")

SELECT = "\x1b[0;7m"           # the selection: reverse video
HIT = "\x1b[0;30;43m"          # the last search match: black on yellow


def cells(text: str) -> list[str]:
    """One string per cell: a wide character fills its first cell, "" its second; a
    combining mark joins the cell before it."""
    out: list[str] = []
    for ch in text:
        w = wcwidth(ch)
        if w == 0 and out:
            out[-1] += ch
        elif w == 2:
            out += [ch, ""]
        else:
            out.append(ch)
    return out


CSI = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")


def crop(row: str, n: int) -> str:
    """A row as Term.row gives it (SGR + text), cut or padded to exactly n cells."""
    out: list[str] = []
    w = i = 0
    while i < len(row):
        m = CSI.match(row, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        cw = max(wcwidth(row[i]), 0)
        if w + cw > n:                         # a wide character that doesn't fit
            break
        out.append(row[i])
        w += cw
        i += 1
    out.append("\x1b[0m" + " " * (n - w))
    return "".join(out)


class Frozen:
    """A term's rows as they were when copy mode started; the term itself runs on."""

    def __init__(self, term: Term) -> None:
        self.live = term
        self.columns, self.lines = term.columns, term.lines
        self.in_alt = term.in_alt
        self.pushed = term.pushed
        self.kept = term.history
        self.screen = [term.row(y, term.columns) for y in range(term.lines)]

    @property
    def history(self) -> int:
        """Scrollback rows of the start that the live ring still holds."""
        if self.in_alt:
            return 0
        d = self.live.pushed - self.pushed
        return max(0, min(self.kept, self.live.history - d))

    def row(self, y: int, width: int) -> str:
        """Row y, exactly width cells; blank past the snapshot's edges."""
        if 0 <= y < self.lines:
            return crop(self.screen[y], width)
        if y < 0 and -y <= self.history:
            return crop(self.live.row(y - (self.live.pushed - self.pushed), width), width)
        return "\x1b[0m" + " " * width


class Copy:
    def __init__(self, term: Term, y: int, x: int = 0) -> None:
        self.t = Frozen(term)
        self.y, self.x = y, x
        self.mark: tuple[int, int] | None = None
        self.hit: tuple[int, int, int] | None = None      # (y, x0, x1) of the last match
        self.query = ""
        self.back = False             # the last search went up
        self.prompt: str | None = None   # a search being typed
        self.prompt_back = False
        self.msg = ""
        self.clamp()

    # ---- the rows -------------------------------------------------------------
    @property
    def top(self) -> int:
        return 0 if self.t.in_alt else -self.t.history

    @property
    def bottom(self) -> int:
        return self.t.lines - 1

    def row(self, y: int) -> list[str]:
        c = cells(SGR.sub("", self.t.row(y, self.t.columns)))
        return c[:self.t.columns] + [" "] * (self.t.columns - len(c))

    def length(self, y: int) -> int:
        """Cells up to the last one that isn't blank."""
        r = self.row(y)
        n = len(r)
        while n and r[n - 1] in (" ", ""):
            n -= 1
        return n

    def sync(self) -> None:
        """The view is frozen, so nothing moves; only the top can come down, when the
        live ring drops rows of the start."""
        self.clamp()

    def clamp(self) -> None:
        self.y = min(max(self.y, self.top), self.bottom)
        self.x = min(max(self.x, 0), self.t.columns - 1)
        if self.mark and self.mark[0] < self.top:        # its row left the ring
            self.mark = (self.top, 0)

    # ---- the view -------------------------------------------------------------
    def follow(self, scroll: int, h: int) -> int:
        """The smallest change to a view of h rows starting at row -scroll that shows
        the cursor."""
        if self.y < -scroll:
            scroll = -self.y
        elif self.y >= h - scroll:
            scroll = h - 1 - self.y
        return min(max(scroll, 0), -self.top)

    def span(self, y: int) -> tuple[int, int] | None:
        """The cells [x0, x1) of row y inside the selection."""
        if not self.mark:
            return None
        a, b = sorted((self.mark, (self.y, self.x)))
        if not a[0] <= y <= b[0]:
            return None
        x0 = a[1] if y == a[0] else 0
        x1 = b[1] + 1 if y == b[0] else self.t.columns
        return x0, x1

    def render(self, y: int, width: int) -> str | None:
        """Row y with the selection or the search hit marked, or None: draw it as usual."""
        sel = self.span(y)
        hit = self.hit[1:] if self.hit and self.hit[0] == y else None
        if not sel and not hit:
            return None
        r = self.row(y)[:width]
        out, last = [], None
        for x, c in enumerate(r):
            if c == "":
                continue
            pen = (SELECT if sel and sel[0] <= x < sel[1]
                   else HIT if hit and hit[0] <= x < hit[1] else "\x1b[0m")
            if pen != last:
                out.append(pen)
                last = pen
            out.append(c)
        return "".join(out) + "\x1b[0m"

    def text(self) -> str:
        """The selection, or the cursor's row; trailing blanks cut from every line."""
        if self.mark:
            a, b = sorted((self.mark, (self.y, self.x)))
        else:
            a, b = (self.y, 0), (self.y, self.t.columns - 1)
        lines = []
        for y in range(a[0], b[0] + 1):
            x0, x1 = self.span(y) or (0, self.t.columns) if self.mark else (0, self.t.columns)
            lines.append("".join(self.row(y)[x0:x1]).rstrip())
        return "\n".join(lines)

    # ---- moving ---------------------------------------------------------------
    def word(self, forward: bool) -> None:
        y, x = self.y, self.x
        while True:
            line = "".join(c or "\0" for c in self.row(y))
            starts = [m.start() for m in WORD.finditer(line)]
            if forward:
                nxt = [s for s in starts if s > x]
                if nxt:
                    self.y, self.x = y, nxt[0]
                    return
                if y >= self.bottom:
                    return
                y, x = y + 1, -1
            else:
                prv = [s for s in starts if s < x]
                if prv:
                    self.y, self.x = y, prv[-1]
                    return
                if y <= self.top:
                    return
                y, x = y - 1, self.t.columns

    def search(self, back: bool) -> bool:
        """The next match of the query from the cursor, wrapping around; smartcase."""
        q = self.query
        if not q:
            return False
        fold = q == q.lower()
        if fold:
            q = q.lower()
        n = self.bottom - self.top + 1
        for i in range(n + 1):
            y = self.top + (self.y - self.top + (-i if back else i)) % n
            r = self.row(y)
            text = "".join(r)
            at = [k for k, c in enumerate(r) for _ in c]   # char index -> cell
            if fold:
                text = text.lower()
            found = [m.start() for m in re.finditer(re.escape(q), text)]
            if i == 0:                  # the cursor's row: only past the cursor
                found = [f for f in found if (at[f] < self.x if back else at[f] > self.x)]
            if i == n and not found:
                break
            if found:
                f = found[-1] if back else found[0]
                end = f + len(q) - 1
                self.y, self.x = y, at[f]
                self.hit = (y, at[f], at[end] + 1 if end < len(at) else at[f] + 1)
                self.msg = ""
                return True
        self.msg = f"not found: {self.query}"
        return False

    # ---- keys -------------------------------------------------------------------
    def keys(self, data: bytes, page: int) -> tuple[str, str]:
        """Handle a read of keys. Returns ("", "") to stay, ("quit", "") to leave, or
        ("copy", text) to copy text and leave."""
        self.sync()
        for k in TOKENS.findall(data.decode(errors="ignore")):
            r = self.key(k, page)
            if r[0]:
                return r
        self.clamp()
        return "", ""

    def key(self, k: str, page: int) -> tuple[str, str]:
        if self.prompt is not None:
            if k in ("\r", "\n"):
                if self.prompt:
                    self.query, self.back = self.prompt, self.prompt_back
                self.prompt = None
                self.search(self.back)
            elif k == "\x1b":
                self.prompt = None
            elif k in ("\x7f", "\x08"):
                self.prompt = self.prompt[:-1]
            elif k == "\x15":
                self.prompt = ""
            elif len(k) == 1 and k.isprintable():
                self.prompt += k
            return "", ""
        self.msg = ""
        if k not in ("n", "N"):
            self.hit = None
        if k in UP:
            self.y -= 1
        elif k in DOWN:
            self.y += 1
        elif k in LEFT:
            self.x -= 1
        elif k in RIGHT:
            self.x += 1
        elif k in HOME:
            self.x = 0
        elif k in END:
            self.x = max(0, self.length(self.y) - 1)
        elif k == "^":
            r = self.row(self.y)
            self.x = next((i for i, c in enumerate(r) if c not in (" ", "")), 0)
        elif k == "w":
            self.word(True)
        elif k == "b":
            self.word(False)
        elif k in PGUP:
            self.y -= page
        elif k in PGDN:
            self.y += page
        elif k == "\x15":
            self.y -= max(1, page // 2)
        elif k == "\x04":
            self.y += max(1, page // 2)
        elif k == "g":
            self.y, self.x = self.top, 0
        elif k == "G":
            self.y = self.bottom
        elif k in ("/", "?"):
            self.prompt, self.prompt_back = "", k == "?"
        elif k == "n":
            self.search(self.back)
        elif k == "N":
            self.search(not self.back)
        elif k in ("v", " "):
            self.mark = None if self.mark else (self.y, self.x)
        elif k in ("y", "\r", "\n"):
            return "copy", self.text()
        elif k == "\x1b":
            if self.mark:
                self.mark = None
            else:
                return "quit", ""
        elif k == "q":
            return "quit", ""
        self.clamp()
        return "", ""

    def status(self) -> str:
        if self.prompt is not None:
            return f" search {'up' if self.prompt_back else 'down'}: {self.prompt}▏"
        if self.msg:
            return f" copy: {self.msg}"
        if self.mark:
            return " copy: selecting; y or Enter copies, Esc drops it"
        return " copy: hjkl move, / ? search, v select, y copy, q leave"
