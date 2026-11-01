"""pyte.Screen and pyte.ByteStream plus what the brain needs and pyte lacks.

The alternate screen: full-screen apps (vim, less, Claude) switch to it with private
mode 1049 (or 47 / 1047) and switch back on exit, expecting the shell's screen to
come back as it was. pyte ignores those modes, so vim's last frame used to stay.

Speed: every byte of every window is parsed (the screen model is lossless), so the
parser's speed is the flood speed. pyte spends its time in two places: draw() makes
one namedtuple per character, and the stream sends every control character and
every byte of an escape sequence through a generator, one at a time. Screen.draw and
Stream.feed below take the common cases at C speed (slices, dict.update, one regex
per sequence) and leave everything else to pyte. They must behave exactly as pyte
does, cell for cell: tests/test_parser.py feeds random streams to both and compares.
"""

from __future__ import annotations

import copy
import inspect
import re

import pyte
from pyte import charsets as cs
from pyte import modes as mo
from wcwidth import wcwidth

ALT_MODES = {47, 1047, 1049}


def _narrow_class() -> str:
    """A regex class of the characters that take exactly one cell by wcwidth, the
    function pyte's draw() uses: printable ASCII, Latin-1, box drawing, blocks,
    arrows, most symbols. Built from wcwidth itself, so the two never disagree."""
    runs, start, prev = [], None, None
    for cp in range(0x20, 0x3000):
        ok = wcwidth(chr(cp)) == 1
        if ok and start is None:
            start = cp
        elif not ok and start is not None:
            runs.append((start, prev))
            start = None
        prev = cp
    if start is not None:
        runs.append((start, prev))
    esc = lambda cp: re.escape(chr(cp))
    return "".join(esc(a) if a == b else f"{esc(a)}-{esc(b)}" for a, b in runs)


_NARROW = _narrow_class()
# Narrow runs, and the rest. A run that isn't narrow goes to pyte's own draw().
_SEGMENTS = re.compile(f"([{_NARROW}]+)|[^{_NARROW}]+")
_ALL_NARROW = re.compile(f"[{_NARROW}]*\\Z")
# A complete CSI sequence in its plain form: ESC [, an optional leading ?, decimal
# parameters, one final byte. Anything else (":" sub-parameters, ">", controls inside,
# a sequence cut by the end of a read) goes through pyte's parser, byte by byte.
_CSI = re.compile(r"\x1b\[(\?)?([0-9;]*)([@-~])")
_CSI_PART = re.compile(r"\x1b(\[\??[0-9;]*)?\Z")      # the start of one, up to the end
# Control characters that act the same in every state of the screen. SI and SO are
# not here: pyte ignores them in UTF-8 mode only.
_BASIC = "\x07\x08\t\n\x0b\x0c\r"


class _Cells(dict):
    """ch → the Char for ch in one set of attributes; filled on first use."""

    def __init__(self, attrs: pyte.screens.Char) -> None:
        super().__init__()
        self.attrs = attrs

    def __missing__(self, ch: str) -> pyte.screens.Char:
        c = self[ch] = self.attrs._replace(data=ch)
        return c


class Screen(pyte.Screen):
    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines)
        self._saved: tuple[dict, pyte.screens.Cursor] | None = None
        self._cells: dict[pyte.screens.Char, _Cells] = {}
        Char = pyte.screens.Char
        self._blanks = {r: Char(data=" ", fg="default", bg="default", reverse=r)
                        for r in (False, True)}
        self.buffer.default_factory = self._new_line

    def _new_line(self) -> pyte.screens.StaticDefaultDict:
        """pyte's factory builds default_char and a generic alias for every new line;
        the same blank line, made once per reverse-video state."""
        return pyte.screens.StaticDefaultDict(self._blanks[mo.DECSCNM in self.mode])

    def index(self) -> None:
        """pyte's index(), with the rows moved up by one C-level update instead of a
        Python loop. The reads run ahead of the writes, as in pyte's loop."""
        m = self.margins
        top, bottom = (m.top, m.bottom) if m else (0, self.lines - 1)
        if self.cursor.y != bottom:
            return self.cursor_down()
        self.dirty.update(range(self.lines))
        buf = self.buffer
        buf.update(zip(range(top, bottom), map(buf.__getitem__, range(top + 1, bottom + 1))))
        buf.pop(bottom, None)

    def draw(self, data: str) -> None:
        """pyte's draw(), with runs of one-cell characters written a line slice at a time."""
        if (self.g1_charset if self.charset else self.g0_charset) is not cs.LAT1_MAP \
                or mo.IRM in self.mode:
            return super().draw(data)
        if _ALL_NARROW.match(data):              # the common case: one run
            if data:
                return self._draw_narrow(data)
            self.dirty.add(self.cursor.y)
            return None
        for m in _SEGMENTS.finditer(data):
            if m.group(1) is None:
                # pyte stops drawing at an unprintable character and drops the rest
                # of the run, so the rest goes to pyte as one piece.
                return super().draw(data[m.start():])
            self._draw_narrow(m.group(1))
        if not data:
            self.dirty.add(self.cursor.y)

    def _draw_narrow(self, text: str) -> None:
        cur, cols = self.cursor, self.columns
        attrs = cur.attrs
        cells = self._cells.get(attrs)
        if cells is None:
            if len(self._cells) > 256:
                self._cells.clear()
            cells = self._cells[attrs] = _Cells(attrs)
        get = cells.__getitem__
        i, n = 0, len(text)
        if cur.x > cols:                      # can't happen; if it does, pyte decides
            return super().draw(text)
        while i < n:
            if cur.x == cols:
                if mo.DECAWM in self.mode:
                    self.dirty.add(cur.y)
                    self.carriage_return()
                    self.linefeed()
                else:                         # no wrap: each one overwrites the last cell
                    self.buffer[cur.y][cols - 1] = get(text[-1])
                    cur.x = cols
                    break
            take = min(n - i, cols - cur.x)
            x = cur.x
            self.buffer[cur.y].update(zip(range(x, x + take), map(get, text[i:i + take])))
            cur.x = x + take
            i += take
        self.dirty.add(cur.y)

    @property
    def in_alt(self) -> bool:
        return self._saved is not None

    def set_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and ALT_MODES & set(modes) and self._saved is None:
            lines = {y: dict(line) for y, line in self.buffer.items()}
            self._saved = (lines, copy.copy(self.cursor))
            self.buffer.clear()
            self.dirty.update(range(self.lines))
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and ALT_MODES & set(modes) and self._saved is not None:
            lines, cursor = self._saved
            self._saved = None
            self.buffer.clear()
            for y, line in lines.items():
                if y < self.lines:
                    self.buffer[y].update({x: c for x, c in line.items() if x < self.columns})
            self.cursor = cursor
            self.cursor.x = min(self.cursor.x, self.columns - 1)
            self.cursor.y = min(self.cursor.y, self.lines - 1)
            self.dirty.update(range(self.lines))
        super().reset_mode(*modes, **kwargs)

    # pyte raises UnboundLocalError on a value it doesn't know (ESC [ 3 K, ESC [ 5 J),
    # which stops the read. Terminals ignore those values; so does this screen.
    def erase_in_line(self, how: int = 0, private: bool = False) -> None:
        if how in (0, 1, 2):
            super().erase_in_line(how, private)

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:
        if how in (0, 1, 2, 3):
            super().erase_in_display(how, *args, **kwargs)

    # Origin mode with no scroll margins set means the whole screen is the region;
    # pyte asserts margins exist there instead (VPA and the cursor report).
    def cursor_to_line(self, line: int | None = None) -> None:
        if mo.DECOM in self.mode and self.margins is None:
            self.cursor.y = (line or 1) - 1
            self.ensure_vbounds()
        else:
            super().cursor_to_line(line)

    def report_device_status(self, mode: int) -> None:
        if mode == 6 and mo.DECOM in self.mode and self.margins is None:
            self.write_process_input(f"\x1b[{self.cursor.y + 1};{self.cursor.x + 1}R")
        else:
            super().report_device_status(mode)

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        super().resize(lines, columns)
        self.dirty.update(range(self.lines))


def _arity(fn: object) -> tuple[int | None, bool]:
    """How many positional parameters a handler takes (None = any) and whether it
    takes private=True."""
    sig = inspect.signature(fn)  # type: ignore[arg-type]
    ps = sig.parameters.values()
    kinds = {p.kind for p in ps}
    n = None if inspect.Parameter.VAR_POSITIONAL in kinds else sum(
        p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name != "private"
        for p in ps)
    private = inspect.Parameter.VAR_KEYWORD in kinds or "private" in sig.parameters
    return n, private


class Stream(pyte.ByteStream):
    """pyte.ByteStream, with plain text, the common control characters and complete
    CSI sequences dispatched straight from the top state of pyte's parser.

    It also never stops on a sequence it can't apply. pyte raises out of feed() on a
    CSI with more parameters than its handler takes (ESC [ 1 ; 2 B) or a ? its handler
    doesn't take (ESC [ ? 5 B), and the rest of that read is lost. Terminals ignore the
    extra parameters (DEC STD 070), and so does this stream; a ? where none is defined
    makes the sequence unknown, which is ignored. A CSI cut by the end of a read is
    kept until the next read, so it takes the same path whole. The few forms only
    pyte's parser takes (">", ":", controls inside) still go through it; if one of
    those raises, the parser is back in its ground state (pyte resets it) and the
    bytes after it are still parsed.
    """

    def attach(self, screen: pyte.Screen) -> None:
        super().attach(screen)
        self._csi = {k: (getattr(screen, v), *_arity(getattr(screen, v)))
                     for k, v in self.csi.items()}
        self._basic_fn = {ch: getattr(screen, self.basic[ch]) for ch in _BASIC}
        self._tail = ""
        self.errors = 0                             # sequences pyte's parser raised on

    def feed(self, data: bytes) -> None:  # type: ignore[override]
        if self.listener is None:
            raise RuntimeError("Listener is not set")
        text = self.utf8_decoder.decode(data) if self.use_utf8 else "".join(map(chr, data))
        if self._tail:
            text, self._tail = self._tail + text, ""
        draw, debug = self.listener.draw, self.listener.debug
        match_text, match_csi, match_part = self._text_pattern.match, _CSI.match, _CSI_PART.match
        basic, csi = self._basic_fn, self._csi
        send = self._send_to_parser
        plain = self._taking_plain_text
        i, n = 0, len(text)
        while i < n:
            if plain:
                m = match_text(text, i)
                if m:
                    i = m.end()
                    draw(text[m.start():i])
                    continue
                ch = text[i]
                fn = basic.get(ch)
                if fn is not None:
                    fn()
                    i += 1
                    continue
                if ch == "\x1b":
                    m = match_csi(text, i)
                    if m:
                        i = m.end()
                        private, params, final = m.groups()
                        args = [min(int(p or 0), 9999) for p in params.split(";")]
                        f, most, takes_private = csi.get(final, (debug, None, True))
                        if most is not None:
                            del args[most:]
                        if not private:
                            f(*args)
                        elif takes_private:
                            f(*args, private=True)
                        else:
                            debug(*args, private=True)
                        continue
                    if n - i < 64 and match_part(text, i):   # cut by the end of the read
                        self._tail = text[i:]
                        break
            try:
                plain = send(text[i])
            except Exception:                        # pyte reset its parser: ground state
                self.errors += 1
                plain = True
            i += 1
        self._taking_plain_text = plain
