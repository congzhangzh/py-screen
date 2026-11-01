"""The client's terminal never scrolls by itself: every frame fits its bottom row.

A status widget wider than the terminal (a long PR list) used to run past the
bottom-right corner, so the terminal wrapped and scrolled a line on every status redraw.
"""

from __future__ import annotations

import sys
import time

import pyte

from harness import PYS, Session, Term


class Counting(pyte.Screen):
    """An outer terminal that counts its own scrolls (a line feed or wrap on the last row)."""

    def __init__(self, cols: int, rows: int) -> None:
        super().__init__(cols, rows)
        self.scrolls = 0

    def index(self) -> None:
        if self.cursor.y == self.lines - 1:
            self.scrolls += 1
        super().index()


def attach(sess: Session, cols: int, rows: int) -> tuple[Term, Counting]:
    t = Term(PYS + ["attach"], sess.env, cols, rows)
    scr = Counting(cols, rows)
    with t.lock:
        t.screen, t.stream = scr, pyte.ByteStream(scr)
    return t, scr


def test_long_widget_is_cut_and_never_scrolls(tmp_path):
    long = "PR" + "".join(f" #{16000 + i} ✓ fix-thing-{i}" for i in range(40))
    widget = tmp_path / "prs.py"
    widget.write_text(f"print({long!r})\n")
    cfg = tmp_path / "config.py"
    cfg.write_text(f"WIDGETS = [cmd([{sys.executable!r}, {str(widget)!r}], every=1),\n"
                   f"           cmd(['echo', 'CLK'], every=0.2)]\n")
    sess = Session("bottom")
    sess.env["PYS_CONFIG"] = str(cfg)
    t, scr = attach(sess, 120, 30)
    try:
        t.wait_for("CLK")
        t.wait_for("…")                               # the PR list is cut, marked
        time.sleep(1.5)                               # several status redraws
        status = scr.display[-1]
        assert status.startswith(" pys "), status
        assert status.rstrip().endswith("CLK"), status   # the last widget stays whole
        assert "#16039" in status                     # the PR list's tail is what's kept
        assert not any("fix-thing" in row for row in scr.display[:-1]), t.text()
        assert scr.scrolls == 0
        assert b"\x1b[?7l" in bytes(t.raw)            # no autowrap while attached
    finally:
        t.close()
        sess.kill()
