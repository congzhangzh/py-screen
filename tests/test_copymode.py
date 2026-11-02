"""Copy mode: the cursor, search, selection and copy over the scrollback (copymode.Copy),
then end to end: prefix [ in an attached client, search, select, copy (OSC 52 to the
client's terminal), prefix ] pastes; and `pys ls` reports each window's modes."""

from __future__ import annotations

import base64
import json

import pytest

from harness import PYS, Session, Term
from py_screen import term
from py_screen.copymode import Copy, cells

C_RB = "\x1d"


@pytest.fixture(params=sorted(term.ENGINES))
def numbers(request):
    """A 20x5 screen after `seq 1 30`: 1..26 in the scrollback, 27..30 + an empty row."""
    t = term.ENGINES[request.param](20, 5, 100)
    t.feed(b"".join(f"{i}\r\n".encode() for i in range(1, 31)))
    return t


def test_cells_wide_and_combining():
    assert cells("a漢é") == ["a", "漢", "", "é"]


def test_move_select_copy(numbers):
    c = Copy(numbers, 4)
    assert (c.top, c.bottom) == (-26, 4)
    c.keys(b"kkk", 4)                         # up to "28"
    c.keys(b"v", 4)
    c.keys(b"j$", 4)                          # to the end of "29"
    assert c.keys(b"y", 4) == ("copy", "28\n29")


def test_copy_without_selection_takes_the_row(numbers):
    c = Copy(numbers, 1)                      # the row "28"
    assert c.keys(b"\r", 4) == ("copy", "28")


def test_search_up_and_down_with_wrap(numbers):
    c = Copy(numbers, 4)
    c.keys(b"?1\r", 4)                        # up: the nearest row with a "1"
    assert (c.y, c.x, c.hit) == (-26 + 20, 1, (-6, 1, 2))   # "21": 1 is its second cell
    c.keys(b"n", 4)                           # again, up: "19"
    assert "".join(c.row(c.y)).strip() == "19"
    c.keys(b"N", 4)                           # the other way: back to "21"
    assert "".join(c.row(c.y)).strip() == "21"
    c.keys(b"g/30\r", 4)                      # down from the oldest row
    assert "".join(c.row(c.y)).strip() == "30" and c.y == 3
    c.keys(b"/nothing\r", 4)
    assert c.msg == "not found: nothing" and c.y == 3


def test_view_follows_the_cursor(numbers):
    c = Copy(numbers, 4)
    assert c.follow(0, 5) == 0
    c.keys(b"g", 5)
    assert c.follow(0, 5) == 26               # the oldest row is the view's top row
    c.keys(b"G", 5)
    assert c.follow(26, 5) == 0


def test_rows_pushed_meanwhile_keep_the_cursor_on_its_text(numbers):
    c = Copy(numbers, 0)                       # on "27"
    c.keys(b"v", 4)
    numbers.feed(b"31\r\n32\r\n")              # two rows scroll off the top
    c.keys(b"j$", 4)                           # sync, then down to the end of "28"
    assert c.keys(b"y", 4) == ("copy", "27\n28")


def test_escape_drops_the_selection_then_leaves(numbers):
    c = Copy(numbers, 2)
    c.keys(b"v", 4)
    assert c.keys(b"\x1b", 4) == ("", "") and c.mark is None
    assert c.keys(b"\x1b", 4) == ("quit", "")


def test_render_marks_the_selection(numbers):
    c = Copy(numbers, 0)
    assert c.render(0, 20) is None
    c.keys(b"vl", 4)
    assert c.render(0, 20).startswith("\x1b[0;7m27\x1b[0m")


def ls(sess: Session) -> dict:
    return json.loads(sess.pys("ls", "--json"))


def test_copy_mode_end_to_end_and_ls_modes():
    sess = Session("copy")
    t = Term(PYS + ["attach"], sess.env, 80, 24)
    try:
        t.wait_for(" pys ")
        sess.pys("send", "0", "seq 1 300", "--enter")
        t.wait_for("300")
        t.wait_until(lambda s: s.splitlines()[21].startswith("$"))
        w = ls(sess)["windows"][0]
        assert (w["alt"], w["mouse"], w["copy"], w["scroll"]) == (False, 0, False, 0)
        assert w["history"] > 250

        t.send(C_RB + "[")                                   # copy mode
        t.wait_until(lambda s: "[copy 0/" in s.splitlines()[22])
        assert ls(sess)["windows"][0]["copy"]
        t.send("?123\r")                                     # search up: the view follows
        t.wait_until(lambda s: any(r.rstrip() == "123" for r in s.splitlines()[:22]))
        assert ls(sess)["windows"][0]["scroll"] > 0
        t.send("vj$y")                                       # select 123..124, copy
        want = base64.b64encode(b"123\n124").decode()
        t.wait_until(lambda s: f"\x1b]52;c;{want}\x07" in t.raw.decode(errors="replace"))
        t.wait_until(lambda s: "[copy" not in s.splitlines()[22])
        w = ls(sess)["windows"][0]
        assert (w["copy"], w["scroll"]) == (False, 0)        # back to live

        t.send(C_RB + "]")                                   # paste: the shell runs 123; 124
        t.wait_for("123: not found")                         # is typed, not run (no newline
        t.send("\x15")                                      # after it), as a terminal pastes
    finally:
        t.close()
        sess.kill()
