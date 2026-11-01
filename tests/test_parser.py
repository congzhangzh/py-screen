"""The fast parser paths (screen.Screen.draw, screen.Stream.feed) against stock pyte.

Both get the same random byte streams, cut into random reads (UTF-8 characters and
escape sequences split across reads included), and must end every read in the same
state: every cell, the cursor, modes, margins, title, the dirty lines.
"""

from __future__ import annotations

import random

import pyte

from py_screen.screen import Screen, Stream


class RefScreen(Screen):
    """Our Screen with pyte's own code in every place we made faster. What stays ours
    on both sides is behaviour, not speed: the alternate screen and the fixes for
    sequences that make pyte raise."""
    draw = pyte.Screen.draw                      # per character
    index = pyte.Screen.index                    # a Python loop over the rows

    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines)
        self.buffer.default_factory = lambda: pyte.screens.StaticDefaultDict(self.default_char)


def state(s: pyte.Screen) -> tuple:
    cells = {y: dict(line) for y, line in s.buffer.items() if line}
    c = s.cursor
    return (cells, (c.x, c.y, c.attrs, c.hidden), frozenset(s.mode), s.margins,
            s.title, s.icon_name, frozenset(s.dirty), s.charset, s.in_alt,
            s.g0_charset is pyte.charsets.LAT1_MAP, s.g1_charset is pyte.charsets.VT100_MAP)


SGR = [0, 1, 2, 3, 4, 5, 7, 9, 22, 23, 24, 27, 30, 31, 37, 39, 40, 42, 47, 49, 90, 97, 100, 107]
TEXT = ["hello", "x", "a b c", "─│╭╮●✻", "héllo", "漢字", "🙂", "é", "​",
        "~" * 30, "0123456789" * 3, "\x01", "\x7f", "\x00", "ß", "\xad", "̀"]


def token(r: random.Random) -> bytes:
    k = r.random()
    if k < 0.35:
        return r.choice(TEXT).encode()
    if k < 0.5:
        return r.choice([b"\r", b"\n", b"\r\n", b"\x08", b"\t", b"\x07", b"\x0b", b"\x0c",
                         b"\x0e", b"\x0f"])
    if k < 0.65:
        ps = ";".join(str(r.choice(SGR)) for _ in range(r.randint(0, 3)))
        if r.random() < 0.1:
            ps = f"38;5;{r.randint(0, 255)}"
        return f"\x1b[{ps}m".encode()
    if k < 0.85:
        final = r.choice("ABCDEFGHJKLMPSTXZ@`adefr")
        most = 2 if final in "Hfr" else 1          # what pyte's handler takes
        ps = ";".join(str(r.randint(0, 12)) if r.random() < 0.8 else ""
                      for _ in range(r.randint(0, most)))
        return f"\x1b[{ps}{final}".encode()
    if k < 0.93:
        m = r.choice(["?7", "?25", "?1049", "?47", "4", "20", "?6", "?1", "?2004", "?5"])
        return f"\x1b[{m}{r.choice('hl')}".encode()
    return r.choice([b"\x1b]0;title\x07", b"\x1b]2;t2\x1b\\", b"\x1b(0", b"\x1b(B", b"\x1b)0",
                     b"\x1b7", b"\x1b8", b"\x1bD", b"\x1bM", b"\x1bE", b"\x1bc", b"\x1b[>c",
                     b"\x1b[38:5:1m", b"\x1b[1\r2H", b"\x1b[?5;7h", b"\x1b[9999999C",
                     b"\x1b[3\x18m", b"\x1b[$p", b"\x1b%@", b"\x1b%G", b"\x9b2J", b"\x1b#8"])


def run(seed: int) -> None:
    r = random.Random(seed)
    cols, rows = r.choice([(10, 4), (20, 6), (7, 3), (80, 24)])
    a, b = Screen(cols, rows), RefScreen(cols, rows)
    sa, sb = Stream(a), pyte.ByteStream(b)
    data = b"".join(token(r) for _ in range(r.randint(20, 300)))
    i = 0
    while i < len(data):
        n = r.choice([1, 2, 3, 7, 64, 4096])
        chunk, i = data[i:i + n], i + n
        sa.feed(chunk)
        sb.feed(chunk)
        assert state(a) == state(b), f"seed {seed}, after byte {i}: {data[:i]!r}"
        if r.random() < 0.3:
            a.dirty.clear()
            b.dirty.clear()


def test_fast_paths_match_pyte():
    for seed in range(400):
        run(seed)


def test_resize_then_draw_matches_pyte():
    for seed in range(50):
        r = random.Random(1000 + seed)
        a, b = Screen(20, 5), RefScreen(20, 5)
        sa, sb = Stream(a), pyte.ByteStream(b)
        for _ in range(20):
            chunk = b"".join(token(r) for _ in range(10))
            sa.feed(chunk)
            sb.feed(chunk)
            if r.random() < 0.3:
                c, l = r.randint(3, 30), r.randint(2, 8)
                a.resize(l, c)
                b.resize(l, c)
            assert state(a) == state(b), f"seed {seed}"


def test_extra_parameters_are_ignored_and_nothing_after_is_lost():
    """pyte raises on these and drops the rest of the read; terminals ignore the extra
    parameters (an unknown ? makes the sequence unknown). Cut anywhere, same result."""
    data = b"ab\x1b[1;2Bcd\x1b[?5Bef\x1b[2;3;4Hx\x1b[?1mgh"
    want = Screen(10, 3)
    pyte.ByteStream(want).feed(b"ab\x1b[1Bcdef\x1b[2;3Hxgh")
    for cut in range(len(data) + 1):
        s = Screen(10, 3)
        st = Stream(s)
        st.feed(data[:cut])
        st.feed(data[cut:])
        assert s.display == want.display, cut
        assert (s.cursor.x, s.cursor.y) == (want.cursor.x, want.cursor.y), cut
