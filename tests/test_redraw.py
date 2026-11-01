"""Redraw: a window's screen stays exact, hidden or shown, slow or flooding.

Apps like Claude Code draw by diff: they write only the cells that changed, often
with relative cursor moves, and never rewrite a cell they think is already right.
If the brain loses any of their output, the wrong cells stay wrong.
"""

from __future__ import annotations

import json
import sys
import time

from harness import PYS, Session, Term

# A diff renderer: a grid of dots, then many one-cell updates with relative moves
# only. It ignores SIGWINCH, so nothing it lost is ever repainted.
DIFF_APP = r"""
import sys, time
R, C = 8, 32
steps, chunk, pause, out = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
w = sys.stdout.buffer
w.write(b"\x1b[2J\x1b[H" + b"\r\n".join([b"." * C] * R) + b"\x1b[H")
grid = [["."] * C for _ in range(R)]
cy = 0
buf = bytearray()
for i in range(steps):
    y, x, ch = (i * 7) % R, (i * 13) % C, chr(97 + i % 26)
    if y > cy:
        buf += b"\x1b[%dB" % (y - cy)
    elif y < cy:
        buf += b"\x1b[%dA" % (cy - y)
    buf += b"\r" + (b"\x1b[%dC" % x if x else b"") + ch.encode()
    grid[y][x], cy = ch, y
    if len(buf) >= chunk:
        w.write(buf); w.flush(); buf.clear(); time.sleep(pause)
w.write(bytes(buf) + b"\x1b[%dB\rDONE" % (R - cy)); w.flush()
open(out, "w").write("\n".join("".join(r) for r in grid))
time.sleep(60)
"""



def wait(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met")


def capture(sess: Session, wid: int) -> list[str]:
    return sess.pys("capture", str(wid)).splitlines()


def test_hidden_diff_renderer_stays_exact(tmp_path):
    app, expected = tmp_path / "diff.py", tmp_path / "expected"
    app.write_text(DIFF_APP)
    sess = Session("redraw")
    t = Term(PYS + ["attach"], sess.env, 100, 30)
    try:
        t.wait_for(" pys ")
        # ~200 KB of updates into a hidden window: more than the 128 KB that a hidden
        # window used to keep (the rest was dropped, leaving wrong cells).
        wid = json.loads(sess.pys("new", sys.executable, str(app), "17000", "8192",
                                  "0.04", str(expected)))["wid"]
        wait(expected.exists, 30)
        time.sleep(0.5)
        sess.pys("select", str(wid))
        t.wait_for("DONE")
        want = expected.read_text().splitlines()
        got = [line[:32] for line in capture(sess, wid)[:8]]
        assert got == want
    finally:
        t.close()
        sess.kill()


def test_visible_diff_renderer_at_full_speed_stays_exact(tmp_path):
    """~3 MB as fast as the app can write: far more than a frame can parse."""
    app, expected = tmp_path / "diff.py", tmp_path / "expected"
    app.write_text(DIFF_APP)
    sess = Session("redrawv")
    t = Term(PYS + ["attach"], sess.env, 100, 30)
    try:
        t.wait_for(" pys ")
        wid = json.loads(sess.pys("new", "--select", sys.executable, str(app), "250000",
                                  "65536", "0", str(expected)))["wid"]
        t.wait_for("DONE", timeout=60)
        want = expected.read_text().splitlines()
        assert [line[:32] for line in capture(sess, wid)[:8]] == want
        t.wait_until(lambda s: s.splitlines()[:8] and
                     [line[:32] for line in s.splitlines()[:8]] == want)   # and on screen
    finally:
        t.close()
        sess.kill()
