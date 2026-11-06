"""Copy mode: the cursor, search, selection and copy over the scrollback (copymode.Copy),
then end to end: prefix [ in an attached client, search, select, copy (OSC 52 to the
client's terminal), prefix ] pastes; and `pys ls` reports each window's modes."""

from __future__ import annotations

import base64
import json
import time

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
        t.wait_until(lambda s: "123: not found" in s         # is typed, not run (no newline
                     or "123: command not found" in s)       # after it), as a terminal pastes;
        t.send("\x15")                                      # dash, or bash as macOS's sh
    finally:
        t.close()
        sess.kill()


# ---- copy mode is frozen: the app runs on, its output shows when copy mode ends ----

def test_alternate_screen_redrawn_in_place_stays_frozen():
    for eng in sorted(term.ENGINES):
        t = term.ENGINES[eng](20, 5, 100)
        t.feed(b"\x1b[?1049h\x1b[Hhello\r\nworld")
        c = Copy(t, 0)
        t.feed(b"\x1b[H\x1b[2JREDRAWN")              # the app repaints, as Claude Code does
        assert "".join(c.row(0)).startswith("hello"), eng
        assert "REDRAWN" not in "".join(c.t.row(y, 20) for y in range(5)), eng
        assert c.keys(b"vj$y", 4) == ("copy", "hello\nworld"), eng
        assert "REDRAWN" in t.row(0, 20), eng        # the live term ran on


def test_primary_screen_freezes_while_rows_scroll_on(numbers):
    c = Copy(numbers, 0)                              # on "27"
    numbers.feed(b"".join(f"{i}\r\n".encode() for i in range(31, 41)))  # 10 rows pushed
    assert c.t.history == 26                          # the start's 26 rows, still in the ring
    assert "".join(c.row(0)).startswith("27") and "".join(c.row(-1)).startswith("26")
    c.keys(b"g", 4)
    assert c.y == -26 and c.row(-26)[:1] == ["1"]
    c.keys(b"G", 4)
    assert c.y == 4                                   # the start's last row, not the live one


def test_rows_the_ring_drops_leave_the_frozen_history():
    for eng in sorted(term.ENGINES):
        t = term.ENGINES[eng](20, 5, 26)              # the ring is full after seq 1 30
        t.feed(b"".join(f"{i}\r\n".encode() for i in range(1, 31)))
        c = Copy(t, 0)
        c.keys(b"g", 4)                               # on "1"
        t.feed(b"31\r\n32\r\n")                       # the ring drops "1" and "2"
        c.keys(b"", 4)
        assert c.t.history == 24 and c.y == -24, eng
        assert c.row(-24)[:1] == ["3"], eng


def test_crop_keeps_colour_and_never_splits_a_wide_character():
    from py_screen.copymode import crop
    assert crop("\x1b[31mab\x1b[0m", 4) == "\x1b[31mab\x1b[0m\x1b[0m  "
    assert crop("a中b", 2) == "a\x1b[0m "            # 中 takes 2 cells: it doesn't fit


def test_copy_mode_freezes_an_app_that_redraws_end_to_end():
    sess = Session("copy-freeze")
    t = Term(PYS + ["attach"], sess.env, 80, 24)

    def tick() -> int:
        row = t.text().splitlines()[0]
        return int(row.split()[1]) if row.startswith("TICK ") else -1

    try:
        t.wait_for(" pys ")
        loop = r"""printf '\033[?1049h'; i=0; while :; do i=$((i+1)); printf '\033[HTICK %d ' $i; sleep 0.05; done"""
        sess.pys("send", "0", loop, "--enter")
        t.wait_until(lambda s: tick() >= 3)
        t.send(C_RB + "[")                                   # copy mode
        t.wait_until(lambda s: "[copy" in s.splitlines()[22])
        n = tick()
        time.sleep(0.6)                                      # ~12 more ticks go by
        assert tick() == n                                   # ...but none shows
        assert ls(sess)["windows"][0]["copy"]
        t.send("q")                                          # leave: live again
        t.wait_until(lambda s: "[copy" not in s.splitlines()[22] and tick() > n + 5)
    finally:
        t.close()
        sess.kill()


# ---- OSC 52: an app in a pane copies ------------------------------------------------
def osc52(text: bytes, end: bytes = b"\x07", sel: bytes = b"c") -> bytes:
    return b"\x1b]52;" + sel + b";" + base64.b64encode(text) + end


@pytest.fixture(params=sorted(term.ENGINES))
def blank(request):
    return term.ENGINES[request.param](40, 5, 0)


def taken(t) -> bytes | None:
    raw = t.take_osc52()
    return raw and term.osc52(raw)


@pytest.mark.parametrize("end", [b"\x07", b"\x1b\\"])
def test_osc52_whole_and_cut_at_every_byte(blank, end):
    seq = b"ab" + osc52("复制 me!".encode(), end) + b"cd"
    for cut in range(1, len(seq)):
        blank.feed(b"\x1b[2J\x1b[H")
        blank.feed(seq[:cut])
        blank.feed(seq[cut:])
        assert taken(blank) == "复制 me!".encode(), f"cut at {cut}"
        assert blank.display[0].rstrip() == "abcd", f"cut at {cut}"   # nothing leaks on screen
        assert blank.take_osc52() is None                             # taken once


def test_osc52_one_byte_at_a_time_padding_included(blank):
    for i in (1, 2, 3):                      # ==, =, no padding: the end of the base64 varies
        for b in osc52(b"x" * i):
            blank.feed(bytes([b]))
        assert taken(blank) == b"x" * i


def test_osc52_the_last_of_a_read_wins(blank):
    blank.feed(osc52(b"old") + osc52(b"new"))
    assert taken(blank) == b"new"


@pytest.mark.parametrize("seq", [
    b"\x1b]52;c;?\x07",                      # a query: the clipboard is never read for an app
    b"\x1b]52;c;\x07",                       # a clear
    b"\x1b]52;c;@@@@\x07",                   # not base64
    b"\x1b]52;\x07",                         # no selection field
])
def test_osc52_that_copies_nothing(blank, seq):
    blank.feed(seq + b"after")
    assert taken(blank) is None
    assert blank.display[0].rstrip() == "after"


def test_osc52_too_big_is_dropped_whole(blank):
    blank.feed(osc52(b"small") + b"\x1b]52;c;")
    assert taken(blank) == b"small"
    for _ in range(40):                      # a partial copy would be worse than none
        blank.feed(b"QUJD" * 16384)
    blank.feed(b"\x07after")
    assert blank.take_osc52() is None
    assert blank.display[0].rstrip() == "after"
    blank.feed(osc52(b"next"))
    assert taken(blank) == b"next"


def test_app_copy_reaches_the_client_and_prefix_paste(sess_short):
    """The whole way: an app prints OSC 52 in a window, the client's terminal gets it,
    prefix ] pastes it, and a reload (which replays the keeper's ring) sends nothing."""
    sess = sess_short
    t = Term(PYS + ["attach"], sess.env, cols=80, rows=24)
    try:
        t.wait_for("$")
        want = base64.b64encode("from the app 复制".encode()).decode()
        sess.pys("send", "0", f"printf '\\033]52;c;{want}\\033\\\\'", "--enter")
        t.wait_until(lambda s: f"\x1b]52;c;{want}\x07" in t.raw.decode(errors="replace"))

        sess.pys("send", "0", "cat", "--enter")
        time.sleep(0.2)
        t.send(C_RB + "]")                                   # prefix ] types it into cat
        t.wait_for("from the app 复制")
        t.send("\x04")

        osc = f"\x1b]52;c;{want}\x07"                       # not the bare base64: the shell's
        assert t.raw.decode(errors="replace").count(osc) == 1 # echo of the printf has that
        sess.pys("reload")
        t.wait_for("$")
        time.sleep(1)
        assert t.raw.decode(errors="replace").count(osc) == 1  # old copies stay old
    finally:
        t.close()


def test_a_hidden_window_copies_too(sess_short):
    sess = sess_short
    t = Term(PYS + ["attach"], sess.env, cols=80, rows=24)
    try:
        t.wait_for("$")
        sess.pys("new", "--", "/bin/sh", "-c",
                 "sleep 0.5; printf '\\033]52;c;aGlkZGVu\\007'; sleep 30")
        t.send(C_RB + "0")                                   # back to window 0
        t.wait_until(lambda s: "\x1b]52;c;aGlkZGVu\x07" in t.raw.decode(errors="replace"))
    finally:
        t.close()


@pytest.fixture
def sess_short():
    s = Session("osc52")
    yield s
    s.kill()
