"""Scrollback: rows that leave the top of the primary screen are kept, and the wheel,
a touch drag (it arrives as wheel reports) or Shift-PgUp scroll a pane back through
them. Typing goes back to live. The engines are checked one by one here; the vterm
store is also fuzzed against libvterm's own scrollback in test_vterm.py."""

from __future__ import annotations

import json
import re

import pytest

from harness import PYS, Session, Term
from py_screen import term

C_RB = "\x1d"
SGR = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(params=sorted(term.ENGINES))
def make(request):
    cls = term.ENGINES[request.param]
    return lambda cols, rows, sb=100: cls(cols, rows, sb)


def history(t) -> list[str]:
    return [SGR.sub("", t.row(y, t.columns)).rstrip() for y in range(-t.history, 0)]


def test_scrolled_rows_are_kept_newest_last(make):
    t = make(10, 3)
    t.feed(b"".join(f"{i}\r\n".encode() for i in range(10)))
    assert history(t) == [str(i) for i in range(8)]
    assert (t.history, t.pushed) == (8, 8)
    assert [s.rstrip() for s in t.display] == ["8", "9", ""]


def test_the_ring_keeps_the_newest_rows(make):
    t = make(10, 3, 5)
    t.feed(b"".join(f"{i}\r\n".encode() for i in range(100)))
    assert history(t) == [str(i) for i in range(93, 98)]
    assert (t.history, t.pushed) == (5, 98)


def test_no_scrollback_when_zero(make):
    t = make(10, 3, 0)
    t.feed(b"a\r\nb\r\nc\r\nd\r\n")
    assert (t.history, t.pushed) == (0, 0)


def test_colours_survive(make):
    t = make(10, 2)
    t.feed(b"\x1b[31mred\x1b[0m\r\n\r\n\r\n")
    assert "\x1b[0;31;49mred" in t.row(-2, 10)


def test_alternate_screen_pushes_nothing(make):
    t = make(10, 3)
    t.feed(b"\x1b[?1049h" + b"x\r\n" * 10)
    assert t.history == 0
    t.feed(b"\x1b[?1049l")
    t.feed(b"y\r\n" * 4)
    assert t.history == 2


def test_ed3_clears_the_scrollback(make):
    t = make(10, 3)
    t.feed(b"a\r\n" * 10)
    assert t.history
    t.feed(b"\x1b[3J")
    assert t.history == 0
    assert t.pushed == 8                      # the count never goes back


def test_a_shrink_keeps_the_rows_it_drops(make):
    t = make(10, 4)
    t.feed(b"1\r\n2\r\n3\r\n4")               # the cursor on the last row
    t.resize(2, 10)
    assert history(t) == ["1", "2"]
    assert [s.rstrip() for s in t.display] == ["3", "4"]


def test_full_reset_leaves_the_alternate_screen(make):
    """RIS (ESC c, what `reset` sends) inside a full-screen app: the shell's screen
    comes back (cleared), and scrolling there fills the scrollback again. libvterm
    cleared the flag but kept showing the alternate buffer (deps/README.md, change 5)."""
    t = make(10, 3)
    t.feed(b"\x1b[?1049hvim\x1bc")
    assert not t.in_alt
    assert [s.rstrip() for s in t.display] == ["", "", ""]
    t.feed(b"a\r\nb\r\nc\r\nd")
    assert history(t) == ["a"]


def rows_of(text: str) -> list[str]:
    return [r.rstrip() for r in text.splitlines()]


def lines(sess: Session, wid: int, hist: bool = False) -> dict:
    return json.loads(sess.pys("capture", str(wid), "--json", *(["--history"] if hist else [])))


def test_wheel_and_shift_pgup_scroll_a_pane_and_a_key_goes_live():
    sess = Session("scroll")
    t = Term(PYS + ["attach"], sess.env, 80, 24)     # one pane: 22 text rows
    try:
        t.wait_for(" pys ")
        t.wait_until(lambda s: "\x1b[?1002h" in t.raw.decode(errors="replace"))
        sess.pys("send", "0", "seq 1 300", "--enter")
        t.wait_for("300")
        t.wait_until(lambda s: rows_of(s)[21].startswith("$"))   # the prompt is back
        live = rows_of(t.text())[:22]
        assert live[20] == "300"
        cap = lines(sess, 0, hist=True)
        assert cap["history"][-1].rstrip() == f"{299 - 20}"         # the row above the screen's top

        t.send(b"\x1b[<64;10;5M")                         # wheel up over the pane: 3 rows
        t.wait_until(lambda s: rows_of(s)[0] == f"{280 - 3}")
        rows = rows_of(t.text())
        assert rows[3] == live[0]                          # the screen moved down by 3
        assert "[3/" in rows[22]                           # the caption says where the view is
        assert lines(sess, 0)["scroll"] == 3

        sess.pys("send", "0", "echo more", "--enter")     # output doesn't move the view:
        t.wait_until(lambda s: lines(sess, 0)["scroll"] > 3)
        assert rows_of(t.text())[0] == f"{280 - 3}"    # still the same rows on top

        t.send(b"\x1b[5;2~")                               # Shift-PgUp: half a pane more
        t.wait_until(lambda s: rows_of(s)[0] == f"{280 - 3 - 11}")

        t.send(b"\x1b[<65;10;5M")                         # wheel down: 3 rows forward
        t.wait_until(lambda s: rows_of(s)[0] == f"{280 - 3 - 11 + 3}")

        t.send("x")                                        # a key: back to live, the key typed
        t.wait_until(lambda s: rows_of(s)[21].startswith("$ x"))
        assert "[" not in rows_of(t.text())[22].split("sh")[0]
        assert lines(sess, 0)["scroll"] == 0
    finally:
        t.close()
        sess.kill()
