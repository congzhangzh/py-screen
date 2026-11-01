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


def ls(sess: Session) -> dict:
    import json
    return json.loads(sess.pys("ls", "--json"))


def wait(pred, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met")


def status_on_bottom_only(scr: Counting) -> bool:
    rows = scr.display
    return rows[-1].startswith(" pys ") and not any(r.startswith(" pys ") for r in rows[:-1])


def test_two_clients_each_get_a_view_that_fits(tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text("WIDGETS = [cmd(['echo', 'CLK'], every=0.2)]\n")
    sess = Session("views")
    sess.env["PYS_CONFIG"] = str(cfg)
    small, s_scr = attach(sess, 100, 24)                # like a laptop over ssh
    big = None
    try:
        small.wait_for("CLK")
        big, b_scr = attach(sess, 140, 34)              # the desk attaches: it's the latest
        big.wait_for("CLK")
        wait(lambda: ls(sess)["size"] == [140, 34])
        big.send("for i in $(seq 1 40); do echo tick-$i; done\r")
        big.wait_for("tick-40")
        time.sleep(0.8)                                 # a few status redraws
        # the small terminal sees a cropped view: its own status row, nothing scrolls
        small.wait_until(lambda _: status_on_bottom_only(s_scr) and s_scr.display[-1].rstrip()
                         .endswith("CLK"))
        assert s_scr.scrolls == 0 and b_scr.scrolls == 0
        assert all(len(r) == 100 for r in s_scr.display)

        small.send("echo typed-on-small\r")             # typing makes the small one the latest
        wait(lambda: ls(sess)["size"] == [100, 24])
        small.wait_for("typed-on-small")
        big.wait_until(lambda _: status_on_bottom_only(b_scr))   # the big one: status at its bottom
        assert b_scr.display[-1].rstrip().endswith("CLK")

        small.close()                                    # the latest leaves: back to the big one
        small = None
        wait(lambda: ls(sess)["size"] == [140, 34])
        big.wait_until(lambda _: status_on_bottom_only(b_scr))
        assert b_scr.scrolls == 0
    finally:
        for t in (small, big):
            if t is not None:
                t.close()
        sess.kill()
