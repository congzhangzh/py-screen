"""Mouse: per-pane routing, click to focus, drag a border, wheel, app coordinates."""

from __future__ import annotations

import json
import re
import sys
import time

from harness import PYS, Session, Term
from py_screen import mouse
from py_screen.layout import Layout

C_RB = "\x1d"

# A tiny app that asks for the mouse and logs what it gets, one repr per read.
APP = r"""
import os, sys, termios, tty
mode, out = sys.argv[1], sys.argv[2]
saved = termios.tcgetattr(0)
tty.setraw(0)
os.write(1, b"\x1b[?1000h" + (b"\x1b[?1006h" if mode == "sgr" else b"") + b"READY-" + mode.encode())
with open(out, "a") as f:
    while True:
        d = os.read(0, 1024)
        if not d or d == b"q":
            break
        f.write(repr(d) + "\n"); f.flush()
os.write(1, b"\x1b[?1006l\x1b[?1000l")
termios.tcsetattr(0, termios.TCSADRAIN, saved)
"""


def sgr(b: int, x: int, y: int, release: bool = False) -> bytes:
    """An SGR report as the outer terminal sends it, 0-based screen cell in."""
    return f"\x1b[<{b};{x + 1};{y + 1}{'m' if release else 'M'}".encode()


def click(t: Term, x: int, y: int) -> None:
    t.send(sgr(0, x, y) + sgr(0, x, y, True))


def test_parse_and_encode():
    parts = mouse.split(b"ab" + sgr(0, 4, 2) + b"c" + b"\x1b[M" + bytes([32 + 3, 33 + 9, 33 + 1]))
    assert parts[0] == b"ab" and parts[2] == b"c"
    ev, rel = parts[1], parts[3]
    assert (ev.b, ev.x, ev.y, ev.press) == (0, 4, 2, True)
    assert rel.release and (rel.x, rel.y) == (9, 1)
    assert mouse.encode(ev, 1, 0, True) == b"\x1b[<0;2;1M"
    assert mouse.encode(mouse.Event(0, 0, 0, True), 300, 0, False) == b"\x1b[M#\xff!"
    drag, move = mouse.Event(32, 0, 0, False), mouse.Event(35, 0, 0, False)
    assert mouse.accepts(1002, drag) and not mouse.accepts(1002, move)
    assert mouse.accepts(1003, move) and not mouse.accepts(0, ev)


def test_borders():
    lay = Layout()
    lay.split(True, None)                           # left | right
    lay.split(False, None)                          # right: top over bottom
    lay.place(81, 24)
    left, top, bottom = lay.panes()
    assert lay.boundary_at(left.w, 5)[1] == 0        # the separator column
    sp, i = lay.boundary_at(top.x + 3, top.y + top.h - 1)   # the top pane's caption
    assert not sp.vertical and i == 0
    assert lay.boundary_at(top.x + 3, bottom.y + bottom.h - 1) is None  # bottom: no neighbour
    lay.drag(*lay.boundary_at(left.w, 5), 20)
    lay.place(81, 24)
    assert left.w == 20 and top.x == 21


def panes(sess: Session) -> list[dict]:
    return json.loads(sess.pys("ls", "--json"))["panes"]


def wait(pred, timeout=5.0, what=lambda: ""):
    """Wait for pred(); on a timeout, say what was seen (what() is called then)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"condition not met after {timeout}s: {what()}")


def text(path) -> str:
    return path.read_text() if path.exists() else "<no file>"


def test_click_drag_wheel_and_app_coordinates(tmp_path):
    app = tmp_path / "app.py"
    app.write_text(APP)
    log = tmp_path / "log"
    sess = Session("mouse")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for(" pys ")
        t.wait_until(lambda s: "\x1b[?1002h" in t.raw.decode(errors="replace"))  # MOUSE default on
        t.send(C_RB + "|")
        wait(lambda: len(panes(sess)) == 2, what=lambda: panes(sess))
        left, right = sorted(panes(sess), key=lambda p: p["x"])
        assert right["focus"]

        click(t, 3, 3)                                # click the left pane: it gets the focus
        wait(lambda: sorted(panes(sess), key=lambda p: p["x"])[0]["focus"], what=lambda: panes(sess))

        sep = left["w"]                               # drag the separator 10 columns left
        t.send(sgr(0, sep, 5) + sgr(32, sep - 5, 5) + sgr(32, sep - 10, 5) + sgr(0, sep - 10, 5, True))
        wait(lambda: sorted(panes(sess), key=lambda p: p["x"])[0]["w"] == left["w"] - 10, what=lambda: panes(sess))
        left, right = sorted(panes(sess), key=lambda p: p["x"])

        # The right pane runs an app that wants the mouse in SGR form; clicking it
        # focuses it and the app sees its own coordinates (1-based, pane-local).
        sess.pys("send", str(right["wid"]), f"{sys.executable} {app} sgr {log}", "--enter")
        t.wait_for("READY-sgr")                       # the app has asked for the mouse
        click(t, right["x"] + 4, 2)
        wait(lambda: "<0;5;3m" in text(log), what=lambda: text(log))
        assert "\\x1b[<0;5;3M" in log.read_text()
        assert next(p for p in panes(sess) if p["focus"])["wid"] == right["wid"]

        click(t, 3, 3)                                # back to the left pane...
        wait(lambda: sorted(panes(sess), key=lambda p: p["x"])[0]["focus"], what=lambda: panes(sess))
        n = log.read_text().count("\n")
        t.send(sgr(65, right["x"] + 1, 4))            # ...the wheel goes to the pane under it
        wait(lambda: text(log).count("\n") > n, what=lambda: text(log))
        assert "<65;2;5M" in log.read_text()
        assert sorted(panes(sess), key=lambda p: p["x"])[0]["focus"]   # focus didn't move

        # X10 form for an app that didn't ask for SGR
        sess.pys("send", str(right["wid"]), "q")
        log2 = tmp_path / "log2"
        sess.pys("send", str(right["wid"]), f"{sys.executable} {app} x10 {log2}", "--enter")
        t.wait_for("READY-x10")                       # not a fixed sleep: a slow start
        click(t, right["x"] + 4, 2)                   # would miss the click (the flake)
        wait(lambda: text(log2).count("\n") >= 1, what=lambda: text(log2) + "\n" + t.text())
        assert "\\x1b[M %#" in log2.read_text()      # button 0, x 5, y 3, each + 32

        # The status line picks a window
        sess.pys("new")
        t.wait_for(" 2 sh")
        row = t.text().splitlines()[-1]
        click(t, row.index(" 2 ") + 1, 29)
        wait(lambda: json.loads(sess.pys("ls", "--json"))["current"] == 2, what=lambda: t.text())
    finally:
        t.close()
        sess.kill()


def tracking(raw: bytes) -> int:
    """The mouse tracking an xterm-like terminal is left in after reading raw. 1000 /
    1002 / 1003 are one setting there: a set picks it, a reset of any of them clears it."""
    lvl = 0
    for m in re.finditer(rb"\x1b\[\?([\d;]+)([hl])", raw):
        for n in map(int, m[1].split(b";")):
            if n in mouse.TRACK:
                lvl = n if m[2] == b"h" else 0
    return lvl


def test_outer_tracking_follows_the_window():
    """Switching to a window whose app takes every motion (Claude Code: 1003), opening
    help (1000) and coming back must leave the outer terminal tracking, not off: a phone
    terminal with tracking off turns a touch drag into arrow keys."""
    sess = Session("mouse-modes")
    t = Term(PYS + ["attach"], sess.env, 100, 24)
    try:
        t.wait_for(" pys ")
        t.wait_until(lambda s: tracking(bytes(t.raw)) == 1002)       # MOUSE default on
        sess.pys("new", "--select", "--", "sh", "-c", r"printf '\033[?1003hANYMOTION'; sleep 60")
        t.wait_for("ANYMOTION")
        wait(lambda: tracking(bytes(t.raw)) == 1003, what=lambda: bytes(t.raw)[-300:])
        t.send(C_RB + "?")                                           # help: clicks only
        wait(lambda: tracking(bytes(t.raw)) == 1000, what=lambda: bytes(t.raw)[-300:])
        t.send("x")                                                  # any other key closes it
        wait(lambda: tracking(bytes(t.raw)) == 1003, what=lambda: bytes(t.raw)[-300:])
        sess.pys("select", "0")                                      # the plain shell again
        wait(lambda: tracking(bytes(t.raw)) == 1002, what=lambda: bytes(t.raw)[-300:])
    finally:
        t.close()
        sess.kill()
