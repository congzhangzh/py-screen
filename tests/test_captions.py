"""CAPTIONS = False: a window that fills the screen has no caption line, and its text takes
that row; a split brings the captions back (they part stacked panes and show the focus)."""

from __future__ import annotations

import sys

import pytest

from harness import PYS, Session, Term
from test_mouse import wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")


def test_no_caption_while_a_window_fills_the_screen(tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text("CAPTIONS = False\n")
    sess = Session("caps")
    sess.env["PYS_CONFIG"] = str(cfg)
    t = Term(PYS + ["attach"], sess.env, 80, 20)
    try:
        t.wait_for("$ ")
        t.send(b"stty size\r")
        t.wait_for("19 80")                               # 20 rows - the status line, no caption
        assert not t.text().splitlines()[-2].startswith(" 0 sh ")
        t.send(b"\x1d-")                                  # prefix -: stacked, with a new window
        wait(lambda: any(l.startswith(" 0 sh ") for l in t.text().splitlines()),
             what=lambda: t.text())                       # a split has its captions back
        t.send(b"\x1dx")                                  # prefix x: close this pane
        wait(lambda: not any(l.startswith(" 0 sh ") for l in t.text().splitlines()),
             what=lambda: t.text())
    finally:
        t.close()
        sess.kill()
