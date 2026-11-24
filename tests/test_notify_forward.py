"""A window's notifications (OSC 9, OSC 777;notify) and progress (OSC 9;4) go on to the
terminal you attached from, so a terminal's tab (Ghostty's dot and bar, Windows Terminal's
taskbar) shows what an app in any pys window does; FORWARD_NOTIFY = False keeps them in pys."""
from __future__ import annotations

import json
import re
import sys
import time

import pytest

from harness import PYS, Session, Term
from py_screen import term
from test_mouse import wait


@pytest.mark.parametrize("engine", sorted(term.ENGINES))
def test_both_engines_keep_each_osc_9_and_777_whole(engine):
    t = term.ENGINES[engine](40, 5, 0)
    t.feed(b"\x1b]9;4;1;40\x07a\x1b]777;notify;T;the body\x1b\\b\x1b]9;hel")
    t.feed(b"lo\x07\x1b]9;" + b"x" * 5000 + b"\x07\x1b]2;title\x07")
    assert t.take_oscs() == [(9, "4;1;40"), (777, "notify;T;the body"), (9, "hello")]
    assert t.take_oscs() == []
    assert t.take_alert()                                   # 777 and 9;hello still ring


def test_what_a_payload_means():
    assert term.note(9, "hello") == ("", "hello")
    assert term.note(777, "notify;Build;done; all green") == ("Build", "done; all green")
    assert term.note(777, "notify") == ("", "")
    assert term.note(9, "4;1;40") is None and term.note(777, "other;x") is None
    title, body = term.note(777, "notify;" + "t" * 300 + ";a\x01b")
    assert len(title) == term.NOTE_TITLE_MAX and title.endswith("…") and body == "a b"
    assert term.progress(9, "4;1;40") == (1, 40)
    assert term.progress(9, "4;3") == (3, 0)
    assert term.progress(9, "4;2;250") == (2, 100)
    assert term.progress(9, "4;0;0") == (0, 0)
    assert term.progress(9, "4;7;1") is None and term.progress(9, "hello") is None
    assert term.progress(777, "4;1;40") is None


pytestmark_posix = pytest.mark.skipif(sys.platform == "win32",
                                      reason="the pty harness is POSIX-only")
OSC = re.compile(rb"\x1b\](777;notify;[^\x07]*|9;4;[^\x07]*)\x07")


def sent(t: Term, since: int = 0) -> list[str]:
    """The notifications and progress pys sent this client, in order."""
    with t.lock:
        raw = bytes(t.raw[since:])
    return [m[1].decode("utf-8", "replace") for m in OSC.finditer(raw)]


def emit(sess: Session, wid: int, seq: str) -> None:
    """Window wid's app writes seq (an OSC) to its terminal."""
    sess.pys("send", str(wid), f"printf '{seq}'\r")


def progress_of(sess: Session) -> dict[int, list | None]:
    return {w["wid"]: w["progress"] for w in json.loads(sess.pys("ls", "--json"))["windows"]}


@pytestmark_posix
def test_a_notification_goes_on_unless_its_window_is_in_front():
    sess = Session("notefwd")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for("$ ")
        sess.pys("new")                                     # window 1, in the back
        sess.pys("title", "--wid", "1", "build")
        time.sleep(0.3)
        start = len(t.raw)
        emit(sess, 1, r"\033]777;notify;Tests;all green\007")
        emit(sess, 0, r"\033]9;from the window in front\007")
        wait(lambda: any(s.startswith("777") for s in sent(t, start)), what=lambda: sent(t, start))
        time.sleep(0.5)
        notes = [s for s in sent(t, start) if s.startswith("777")]
        assert notes == ["777;notify;build: Tests;all green"], notes
    finally:
        t.close()
        sess.kill()


@pytestmark_posix
def test_progress_is_one_bar_the_front_window_first_then_the_most_urgent():
    sess = Session("progfwd")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for("$ ")
        sess.pys("new")
        sess.pys("new")                                     # windows 1 and 2, in the back
        sess.pys("select", "0")
        time.sleep(0.3)
        last = lambda: [s for s in sent(t) if s.startswith("9;4")][-1:]
        emit(sess, 1, r"\033]9;4;1;40\007")
        wait(lambda: last() == ["9;4;1;40"], what=lambda: sent(t))
        emit(sess, 2, r"\033]9;4;3\007")                    # busy ranks below running
        wait(lambda: progress_of(sess)[2] == [3, 0], what=lambda: progress_of(sess))
        time.sleep(0.3)
        assert last() == ["9;4;1;40"]
        emit(sess, 2, r"\033]9;4;2;10\007")                 # an error ranks above it
        wait(lambda: last() == ["9;4;2;10"], what=lambda: sent(t))
        emit(sess, 0, r"\033]9;4;1;90\007")                 # the window in front wins
        wait(lambda: last() == ["9;4;1;90"], what=lambda: sent(t))
        emit(sess, 0, r"\033]9;4;0\007")
        wait(lambda: last() == ["9;4;2;10"], what=lambda: sent(t))
        sess.pys("send", "2", "exit\r")                     # its window ends: its bar goes
        wait(lambda: last() == ["9;4;1;40"], what=lambda: sent(t))
        emit(sess, 1, r"\033]9;4;0;0\007")
        wait(lambda: last() == ["9;4;0"], what=lambda: sent(t))
    finally:
        t.close()
        sess.kill()


@pytestmark_posix
def test_forward_notify_false_keeps_them_in_pys(tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text("FORWARD_NOTIFY = False\n", encoding="utf-8")
    sess = Session("nofwd")
    sess.env["PYS_CONFIG"] = str(cfg)
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for("$ ")
        sess.pys("new")
        sess.pys("select", "0")
        time.sleep(0.3)
        emit(sess, 1, r"\033]777;notify;T;b\007\033]9;4;1;50\007")
        wait(lambda: progress_of(sess)[1] == [1, 50], what=lambda: progress_of(sess))
        wait(lambda: json.loads(sess.pys("ls", "--json"))["windows"][1]["attn"] == "ask",
             what=lambda: sess.pys("ls", "--json"))           # pys still marks the window
        time.sleep(0.5)
        assert [s for s in sent(t) if s != "9;4;0"] == [], sent(t)
    finally:
        t.close()
        sess.kill()
