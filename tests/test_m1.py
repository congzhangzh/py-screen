"""M1 features: alternate screen, splits, window list, rename, widgets, layout across reload."""

from __future__ import annotations

import json
import time

import pyte

from harness import PYS, Session, Term
from py_screen.screen import Screen

C_RB = "\x1d"


def test_alt_screen_restores_the_shell_screen():
    s = Screen(20, 5)
    st = pyte.ByteStream(s)
    st.feed(b"shell-text\r\n$ ")
    st.feed(b"\x1b[?1049h\x1b[2Jvim-frame")
    assert "vim-frame" in "\n".join(s.display)
    st.feed(b"\x1b[?1049l")
    text = "\n".join(s.display)
    assert "shell-text" in text and "vim-frame" not in text
    assert (s.cursor.x, s.cursor.y) == (2, 1)


def panes(sess: Session) -> list[dict]:
    return json.loads(sess.pys("ls", "--json"))["panes"]


def wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met")


def test_splits_list_rename_widgets_reload(tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text('WIDGETS = [cmd(["echo", "W1DGET"], every=1), clock("%H:%M")]\n'
                   'BIND = {"P": ("tops", ["sh"])}\n')
    sess = Session("m1")
    sess.env["PYS_CONFIG"] = str(cfg)
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for(" pys ")
        t.wait_for("W1DGET")                              # a command widget in the status line

        t.send(C_RB + "|")                                # split + new window on the right
        wait(lambda: len(panes(sess)) == 2)
        right = next(p for p in panes(sess) if p["focus"])
        assert right["x"] > 0 and right["wid"] == 1
        t.wait_for("│")                                   # the separator
        t.send(C_RB + "h")                                # focus moves left
        wait(lambda: next(p for p in panes(sess) if p["focus"])["x"] == 0)

        t.send(C_RB + ",")                                # rename the left window
        t.wait_for("title for window 0")
        t.send("editor\r")
        t.wait_for(" 0 editor ")

        t.send(C_RB + "P")                                # a BIND entry from the config
        t.wait_for(" 2 tops ")

        t.send(C_RB + "w")                                # window list
        t.wait_for("Num  Title")
        t.send("\x1b")
        t.wait_until(lambda s: "Num  Title" not in s)

        before = panes(sess)
        sess.pys("reload")                                # the layout survives a new brain
        wait(lambda: sess.pys("ls", check=False) != "" and panes(sess) == before, 10)
        t.wait_for(" 0- editor ", timeout=5)              # and so do the title and "last"

        t.send(C_RB + "x")                                # close the focused pane
        wait(lambda: len(panes(sess)) == 1)
        t.send(C_RB + "-")                                # horizontal split
        wait(lambda: len(panes(sess)) == 2)
        ps = panes(sess)
        assert ps[0]["x"] == ps[1]["x"] == 0 and ps[1]["y"] > 0
    finally:
        t.close()
        sess.kill()


def test_alt_tab_and_help():
    sess = Session("m1k")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for(" pys ")
        sess.pys("new")
        sess.pys("new")
        cur = lambda: json.loads(sess.pys("ls", "--json"))["current"]
        assert cur() == 0
        t.send(b"\x1b\t")                                # Alt+Tab
        wait(lambda: cur() == 1)
        t.send(b"\x1b\t")
        wait(lambda: cur() == 2)
        t.send(b"\x1b\x1b[Z")                            # Alt+Shift+Tab
        wait(lambda: cur() == 1)
        t.send(C_RB + "?")
        t.wait_for("keys: prefix C-], then")
        t.wait_for("Alt-Tab / Alt-Shift-Tab")
        t.send("q")
        t.wait_until(lambda s: "py-screen keys" not in s)
    finally:
        t.close()
        sess.kill()


def test_help_lists_the_windows_and_selects_one():
    sess = Session("m1h")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for(" pys ")
        sess.pys("new")
        sess.pys("new")
        cur = lambda: json.loads(sess.pys("ls", "--json"))["current"]
        t.send(C_RB + "?")
        t.wait_for("windows (a digit or a click selects one)")
        t.wait_until(lambda s: all(f"   {i}" in s for i in "012") and "  0*" in s)
        t.send("2")                                      # a digit selects and closes
        wait(lambda: cur() == 2)
        t.wait_until(lambda s: "py-screen help" not in s)
        t.send(C_RB + "?")
        t.wait_for("py-screen help")
        t.send("\x1b[<0;6;5M\x1b[<0;6;5m")               # click the row of window 1
        wait(lambda: cur() == 1)
        t.wait_until(lambda s: "py-screen help" not in s)
    finally:
        t.close()
        sess.kill()


def test_help_pages_when_it_does_not_fit():
    sess = Session("m1p")
    t = Term(PYS + ["attach"], sess.env, 80, 24)
    try:
        t.wait_for(" pys ")
        t.send(C_RB + "?")
        t.wait_for("page 1/2 · Space: more")
        t.send(" ")
        t.wait_for("page 2/2")
        t.wait_until(lambda s: "without the prefix" in s and " mouse" in s)
        t.send(" ")                                      # wraps to the first page
        t.wait_for("page 1/2")
        t.send("q")
        t.wait_until(lambda s: "py-screen help" not in s)
    finally:
        t.close()
        sess.kill()


def test_help_lines_fit_80_columns():
    from py_screen import brain
    for k, v in brain.HELP + brain.NO_PREFIX + brain.MOUSE_HELP:
        assert len(f"   {k:<23} {v}") < 80, k
