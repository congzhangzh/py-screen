"""Two clients on one session: each has its own view (its current window, its layout), and a
guest (`pys attach --guest`, a phone or a web terminal) never resizes a window a normal
client shows; a window only a guest showed goes back to its home size when it leaves."""

from __future__ import annotations

import json
import sys

import pytest

from harness import PYS, Session, Term
from test_mouse import wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")


def ls(sess: Session) -> dict:
    return json.loads(sess.pys("ls", "--json"))


def size(sess: Session, wid: int) -> list[int]:
    return next(w["size"] for w in ls(sess)["windows"] if w["wid"] == wid)


def views(sess: Session) -> list[dict]:
    return ls(sess)["client_views"]


def test_each_client_has_its_own_current_window():
    sess = Session("views")
    a = Term(PYS + ["attach"], sess.env, 120, 30)
    b = None
    try:
        a.wait_for("$ ")
        sess.pys("new")
        sess.pys("select", "0")
        wait(lambda: ls(sess)["current"] == 0)
        b = Term(PYS + ["attach"], sess.env, 100, 26)
        wait(lambda: len(views(sess)) == 2, what=lambda: views(sess))
        assert [v["current"] for v in views(sess)] == [0, 0]       # it starts from the lead's
        b.send(b"\x1dn")                                            # B: the next window
        wait(lambda: [v["current"] for v in views(sess)] == [0, 1], what=lambda: views(sess))
        a.send(b"\x1dn")                                            # A moves on its own
        wait(lambda: [v["current"] for v in views(sess)] == [1, 1], what=lambda: views(sess))
        a.send(b"\x1dn")
        wait(lambda: [v["current"] for v in views(sess)] == [0, 1], what=lambda: views(sess))
        # each status line marks its own client's window
        wait(lambda: "0* " in a.text().splitlines()[-1], what=lambda: a.text())
        wait(lambda: "1* " in b.text().splitlines()[-1], what=lambda: b.text())
    finally:
        if b:
            b.close()
        a.close()
        sess.kill()


def test_a_guest_never_shrinks_the_desktop():
    sess = Session("guest")
    a = Term(PYS + ["attach"], sess.env, 120, 30)
    g = None
    try:
        a.wait_for("$ ")
        sess.pys("new")
        sess.pys("select", "0")
        wait(lambda: size(sess, 0) == [120, 28], what=lambda: ls(sess))   # caption, status
        home1 = size(sess, 1)
        g = Term(PYS + ["attach", "--guest"], sess.env, 80, 24)
        wait(lambda: len(views(sess)) == 2 and views(sess)[1]["guest"], what=lambda: views(sess))
        g.wait_for("$ ")                                   # it sees window 0, cropped
        assert size(sess, 0) == [120, 28]                  # the desktop's size, kept
        a.send(b"x")                                       # the desktop types: still its size
        wait(lambda: size(sess, 0) == [120, 28], what=lambda: ls(sess))

        g.send(b"\x1dn")                                   # the guest alone shows window 1:
        wait(lambda: size(sess, 1) == [80, 22], what=lambda: ls(sess))   # its size
        assert ls(sess)["current"] == 0 and size(sess, 0) == [120, 28]   # the desktop's own
        g.close()
        g = None
        wait(lambda: size(sess, 1) == home1, what=lambda: ls(sess))      # back home, hidden
    finally:
        if g:
            g.close()
        a.close()
        sess.kill()


def test_a_guest_sees_the_rows_around_the_cursor():
    """The desktop's window is taller than the guest's pane: the guest shows its bottom,
    where the prompt is, not its top."""
    sess = Session("guestrows")
    a = Term(PYS + ["attach"], sess.env, 100, 40)
    g = None
    try:
        a.wait_for("$ ")
        sess.pys("send", "--enter", "0", "seq 1 30; echo END-OF-IT")
        wait(lambda: "END-OF-IT" in [r.strip() for r in a.text().splitlines()],
             what=lambda: a.text())
        g = Term(PYS + ["attach", "--guest"], sess.env, 80, 20)
        wait(lambda: "END-OF-IT" in [r.strip() for r in g.text().splitlines()],
             what=lambda: g.text())
        rows = g.text().splitlines()
        assert not any(r.strip() == "1" for r in rows[:3])           # the top rows are cut
        g.send(b"echo hi-from-the-guest\r")
        wait(lambda: "hi-from-the-guest" in a.text(), what=lambda: a.text())
        assert size(sess, 0) == [100, 38]
    finally:
        if g:
            g.close()
        a.close()
        sess.kill()
