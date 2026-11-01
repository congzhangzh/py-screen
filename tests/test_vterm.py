"""The vterm engine: py-screen's screen store (_vterm.Term) against libvterm's own screen
layer (_vterm.ScreenLayerTerm), which shares the parser and state and is upstream code.

Both get the same random byte streams, cut into random reads, sometimes resized, and
must end every read the same: every row as rendered (text and colours), the cursor,
the modes, the title. And every row whose rendering changed must be in take_dirty(),
or the brain would leave it stale on the client.
"""

from __future__ import annotations

import os
import random

import pytest

if os.environ.get("PYS_TERM") == "vterm":
    from py_screen import _vterm               # must be there: fail, don't skip
else:
    _vterm = pytest.importorskip("py_screen._vterm")

from test_parser import token as pyte_token   # noqa: E402  (tests/ is on the path)

MODES = (1, 25, 47, 1000, 1002, 1003, 1006, 2004)


def vt_token(r: random.Random) -> bytes:
    """Sequences that exercise the store: margins, insert/delete, scrolls, erases."""
    k = r.random()
    n = r.randint(0, 6)
    if k < 0.25:
        top = r.randint(1, 6)
        return f"\x1b[{top};{top + r.randint(0, 6)}r".encode()          # DECSTBM
    if k < 0.35:
        return r.choice([b"\x1b[?69h", b"\x1b[?69l"]) + \
            f"\x1b[{r.randint(1, 4)};{r.randint(3, 12)}s".encode()       # DECSLRM
    if k < 0.65:
        return f"\x1b[{n}{r.choice('@PLMSTXb')}".encode()                # ICH DCH IL DL SU SD ECH REP
    if k < 0.75:
        return r.choice([b"\x1b[1\"q", b"\x1b[0\"q", b"\x1b[2\"q"])       # DECSCA
    if k < 0.85:
        return f"\x1b[?{r.randint(0, 2)}{r.choice('JK')}".encode()       # DECSED DECSEL
    return r.choice([b"\x1b#8", b"\x1b#6", b"\x1b#5", b"\x1b[?5h", b"\x1b[?5l", b"\x1bD",
                     b"\x1bM", b"\x1b[4h", b"\x1b[4l", b"\x1b[?6h", b"\x1b[?6l", b"\x1b[?7l",
                     b"\x1b[?1047h", b"\x1b[?1047l", b"\x1b[48;5;21m", b"\x1b[38;2;1;2;3m",
                     b"\xe6\xbc\xa2\xe5\xad\x97" * 3])


def stream(r: random.Random, n: int) -> bytes:
    return b"".join(vt_token(r) if r.random() < 0.3 else pyte_token(r) for _ in range(n))


def state(t) -> tuple:
    return ([t.row(y, t.columns) for y in range(t.lines)], t.cursor, t.cursor_hidden,
            t.in_alt, t.title, [t.mode(m) for m in MODES], (t.columns, t.lines))


def check_dirty(t, before: list[str]) -> list[str]:
    after = [t.row(y, t.columns) for y in range(t.lines)]
    dirty = t.take_dirty()
    changed = {y for y in range(t.lines) if y >= len(before) or before[y] != after[y]}
    assert changed <= dirty, f"changed rows {sorted(changed - dirty)} not reported dirty"
    return after


def run(seed: int, resizes: bool) -> None:
    r = random.Random(seed)
    cols, rows = r.choice([(10, 4), (20, 6), (7, 3), (80, 24), (13, 9)])
    a, b = _vterm.Term(cols, rows), _vterm.ScreenLayerTerm(cols, rows)
    data = stream(r, r.randint(20, 300))
    seen = check_dirty(a, [])
    i = 0
    while i < len(data):
        n = r.choice([1, 2, 3, 7, 16, 64, 256])     # many reads: compare often
        chunk, i = data[i:i + n], i + n
        a.feed(chunk)
        b.feed(chunk)
        assert state(a) == state(b), f"seed {seed}, after byte {i}: {data[:i]!r}"
        seen = check_dirty(a, seen)
        if resizes and r.random() < 0.08:
            c, l = r.randint(2, 30), r.randint(1, 12)
            a.resize(l, c)
            b.resize(l, c)
            assert state(a) == state(b), f"seed {seed}, resize to {c}x{l} after byte {i}"
            seen = check_dirty(a, [])


def test_store_matches_libvterm_screen_layer():
    for seed in range(400):
        run(seed, resizes=False)


def test_store_matches_libvterm_screen_layer_with_resizes():
    for seed in range(400):
        run(10_000 + seed, resizes=True)


def test_scroll_moves_rows_not_cells():
    """A flood scrolls by rotating row pointers; the rows still come out in order."""
    t = _vterm.Term(5, 3)
    t.feed(b"".join(f"{i}\r\n".encode() for i in range(1000)) + b"end")
    assert [s.rstrip() for s in t.display] == ["998", "999", "end"]


def test_wide_character_cut_by_a_resize_is_blank():
    """Its right half is gone, so it can't be drawn: a space, never two cells in one."""
    for T in (_vterm.Term, _vterm.ScreenLayerTerm):
        t = T(4, 2)
        t.feed("ab漢".encode())
        t.resize(2, 3)
        assert t.display[0] == "ab ", T.__name__
        assert len(t.row(0, 3).replace("\x1b[0;39;49m", "").replace("\x1b[0m", "")) == 3


def test_modes_and_title():
    t = _vterm.Term(20, 4)
    t.feed(b"\x1b[?1h\x1b[?2004h\x1b[?1006h\x1b[?1002h\x1b]2;my title\x07\x1b[?25l\x1b[?1049h")
    assert [t.mode(m) for m in (1, 2004, 1006, 1000, 1002, 1003)] == [True] * 5 + [False]
    assert (t.title, t.cursor_hidden, t.in_alt) == ("my title", True, True)
    t.feed(b"\x1b[?1049l\x1bc")
    # A full reset ends tracking, cursor keys and paste mode. libvterm keeps the mouse
    # encoding (?1006; vterm_state_reset clears mouse_flags, not mouse_protocol), which
    # is harmless: with tracking off no report is sent.
    assert [t.mode(m) for m in (1, 2004, 1000, 1002, 1003)] == [False] * 5
    assert not t.in_alt
