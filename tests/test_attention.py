"""A window that wants you: its app's bell or notification, or `pys attn` (a Claude hook),
marks its tab; switching to it clears the mark; the window in front of you is never marked."""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from py_screen import term
from py_screen.agents import attn
from py_screen.brain import Window
from py_screen.screen import notifies

# (bytes the app writes, whether they ask for attention)
CASES = [
    (b"hi\x07", True),                            # a bell
    (b"plain text", False),
    (b"\x1b]0;title\x07", False),                 # the BEL that ends an OSC is no bell
    (b"\x1b]2;title\x1b\\", False),
    (b"\x1b]9;build done\x07", True),             # iTerm2's notification
    (b"\x1b]9;4;1;50\x07", False),                # ConEmu's progress bar
    (b"\x1b]9;9;/home/u\x1b\\", False),           # ConEmu's folder, at every prompt
    (b"\x1b]777;notify;Claude;waits\x07", True),  # urxvt, VTE, Ghostty
    (b"\x1b]777;preexec\x07", False),
    (b"\x1b]52;c;aGk=\x07", False),               # a copy, not a call
]


@pytest.mark.parametrize("engine", sorted(term.ENGINES))
@pytest.mark.parametrize("data,alert", CASES)
def test_engines_see_bells_and_notifications(engine, data, alert) -> None:
    t = term.ENGINES[engine](40, 5, 10)
    t.feed(data)
    assert t.take_alert() is alert
    assert t.take_alert() is False                # taken once


@pytest.mark.parametrize("engine", sorted(term.ENGINES))
@pytest.mark.parametrize("data,alert", [c for c in CASES if c[0].startswith(b"\x1b]")])
def test_a_notification_cut_at_every_byte(engine, data, alert) -> None:
    for cut in range(1, len(data)):
        t = term.ENGINES[engine](40, 5, 10)
        t.feed(data[:cut])
        t.feed(data[cut:])
        assert t.take_alert() is alert, cut


def test_notifies() -> None:
    assert notifies(9, "done") and notifies(9, "4 builds done")
    assert not notifies(9, "") and not notifies(9, "4") and not notifies(9, "12;x")
    assert notifies(777, "notify") and notifies(777, "notify;a;b")
    assert not notifies(777, "notifyx") and not notifies(52, "c;aGk=")


def test_a_replayed_bell_rings_no_more() -> None:
    """After a brain restart the keeper replays old output: its bells were seen before."""
    w = Window.new(0, 1, ["/bin/sh"], 40, 5)
    w.pending += b"old\x07"
    w.noack = len(w.pending)
    w.parse(len(w.pending))
    assert not w.alert
    w.pending += b"new\x07"
    w.parse(len(w.pending))
    assert w.alert


def test_the_claude_hook_events() -> None:
    assert attn.level_of({"hook_event_name": "Notification", "message": "Claude needs your "
                          "permission to use Bash", "notification_type": "permission_prompt"}) \
        == ("ask", "Claude needs your permission to use Bash")
    assert attn.level_of({"hook_event_name": "Notification",
                          "notification_type": "idle_prompt"}) is None
    assert attn.level_of({"hook_event_name": "Stop"}) == ("done", "turn ended")
    assert attn.level_of({"hook_event_name": "UserPromptSubmit"}) == (None, "")
    assert attn.level_of({"hook_event_name": "SessionEnd"}) == (None, "")
    assert attn.level_of({"hook_event_name": "PreToolUse"}) is None


@pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")
def test_marks_on_the_status_line() -> None:
    from harness import PYS, Session, Term
    sess = Session("attn", shell="/bin/sh")
    t = Term(PYS + ["attach"], sess.env)
    try:
        t.wait_for("$ ")
        first = json.loads(sess.pys("ls", "--json"))["current"]
        other = json.loads(sess.pys("new"))["wid"]

        def mark(wid):
            return next(w for w in json.loads(sess.pys("ls", "--json"))["windows"]
                        if w["wid"] == wid)["attn"]

        # pys attn on a window behind: marked, and its tab says so
        sess.pys("attn", "ask", "--wid", str(other), "--why", "a test")
        assert mark(other) == "ask"
        assert sess.pys("attn", "--wid", str(other)).strip() == "ask: a test"
        t.wait_for(f" {other}! ")
        # the window in front of you is never marked
        r = subprocess.run(PYS + ["attn", "done", "--wid", str(first)], env=sess.env,
                           capture_output=True, text=True, timeout=15)
        assert r.returncode == 0 and "looking at" in r.stderr and mark(first) is None
        # a bell in a window behind marks it by itself
        sess.pys("attn", "--clear", "--wid", str(other))
        assert mark(other) is None
        sess.pys("send", "--enter", str(other), "printf '\\a'")
        deadline = time.monotonic() + 10
        while mark(other) != "ask" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert mark(other) == "ask"
        # the Claude hook, as Claude runs it in that window
        env = dict(sess.env, PYS_WINDOW=str(other), PYS_SESSION=str(sess.dir))
        hook = [sys.executable, "-m", "py_screen.agents.attn"]
        for event, want in (("UserPromptSubmit", None), ("Stop", "done")):
            subprocess.run(hook, env=env, input=json.dumps({"hook_event_name": event}),
                           text=True, timeout=15, check=True)
            assert mark(other) == want
        # prefix ! goes to it, and that clears it
        t.send(b"\x1d!")
        deadline = time.monotonic() + 10
        while json.loads(sess.pys("ls", "--json"))["current"] != other \
                and time.monotonic() < deadline:
            time.sleep(0.05)
        assert json.loads(sess.pys("ls", "--json"))["current"] == other
        assert mark(other) is None
    finally:
        t.close()
        sess.kill()


def test_the_hook_outside_pys_does_nothing() -> None:
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("PYS_WINDOW", "PYS_SESSION")}
    r = subprocess.run([sys.executable, "-m", "py_screen.agents.attn"], env=env,
                       input='{"hook_event_name": "Stop"}', capture_output=True, text=True,
                       timeout=15)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_setup_adds_the_hooks_once(tmp_path) -> None:
    from test_setup import setup
    claude = tmp_path / "claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    setup(tmp_path, "--yes")
    s = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    for event in attn.EVENTS:
        cmds = [h["command"] for g in s["hooks"][event] for h in g["hooks"]]
        assert sum(attn.MARK in c for c in cmds) == 1, event
    assert [h["command"] for h in s["hooks"]["Stop"][0]["hooks"]] == ["echo mine"]   # kept
    assert "runs this install already" in setup(tmp_path, "--yes")
