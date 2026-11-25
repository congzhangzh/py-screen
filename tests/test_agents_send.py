"""Sending to one agent, a team (a pys group) or every other agent (2026-11-24): pys-agents to
lists whom a message goes to and the address SendMessage takes, never the sender's own window;
a member that is not running gets a held message when it is back and names its window."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from harness import PYS, Session
from py_screen import mail
from py_screen.agents import listing

LIVE = [
    {"window": 3, "title": "pys-po-g1 #12 busy", "name": "pys-po-g1", "session": "s3",
     "state": "busy", "group": "pys"},
    {"window": 5, "title": "product-a-po-g1 free", "name": "product-a-07", "session": "s5",
     "state": "idle", "group": "product-a"},
    {"window": 6, "title": "by hand", "agent_title": "product-a-rel-g1 free", "name": "product-a-rel-g1", "session": "s6",
     "state": "waiting", "group": "product-a"},
    {"window": None, "title": None, "name": "outside", "session": "s9", "state": "idle",
     "group": None},
]


def wins(rows):
    return [r["window"] for r in rows]


def test_recipients_by_name_group_and_all():
    to = listing.recipients
    # a member name, a session name, a window number, any case; the session name is the address
    rows, missing = to(["PRODUCT-A-PO-G1", "product-a-rel-g1", "3", "gone-po-g1"], None, False,
                       LIVE, me=None)
    assert wins(rows) == [5, 6, 3] and missing == ["gone-po-g1"]
    assert rows[0]["member"] == "product-a-po-g1" and rows[0]["address"] == "product-a-07"
    # a team is a pys group; never the sender's own window, never one outside pys
    assert wins(to([], "product-a", False, LIVE, me=6)[0]) == [5]
    assert wins(to([], None, True, LIVE, me=3)[0]) == [5, 6]
    assert to(["pys-po-g1"], None, False, LIVE, me=3) == ([], ["pys-po-g1"])
    assert wins(to(["product-a-po-g1", "5"], None, False, LIVE, me=None)[0]) == [5]   # once


def test_to_lists_addresses_and_says_who_is_not_running(monkeypatch, capsys):
    monkeypatch.setattr(listing, "agents", lambda: LIVE)
    monkeypatch.setenv("PYS_WINDOW", "3")
    assert listing.main(["to", "product-a-po-g1", "gone-po-g1"]) == 0
    out = capsys.readouterr().out
    assert "product-a-07" in out and "idle" in out and "SendMessage" in out
    assert "gone-po-g1: not running; pys-agents hold gone-po-g1" in out
    assert listing.main(["to", "--group", "product-a", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert [r["address"] for r in d["to"]] == ["product-a-07", "product-a-rel-g1"]
    assert listing.main(["to", "x", "--all"]) == 2                 # one way to name them


def test_hold_and_take(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    mail.hold("Gone-PO-g1", "  the handover is in role-po.md  ", "pys-po-g1")
    mail.hold("gone-po-g1", "second 你好", "product-a-rel-g1")
    assert [m["text"] for m in mail.held("gone-po-g1")] == ["the handover is in role-po.md",
                                                           "second 你好"]
    ms = mail.take("gone-po-g1")
    assert [m["from"] for m in ms] == ["pys-po-g1", "product-a-rel-g1"]
    assert mail.take("gone-po-g1") == [] and mail.held("gone-po-g1") == []
    kept = (tmp_path / "pys-mail" / "gone-po-g1.read.jsonl").read_text(encoding="utf-8")
    assert kept.count("\n") == 2 and "你好" in kept                # what was read is kept
    text = mail.show("gone-po-g1", ms)
    assert "not from the owner" in text and "from pys-po-g1] the handover" in text
    for bad in ("../x", "", "a b", "x" * 65):
        with pytest.raises(ValueError):
            mail.hold(bad, "t", "s")
    with pytest.raises(ValueError):
        mail.hold("x-po-g1", "   ", "s")
    assert mail.take("../x") == []
    mail.hold("x-po-g1", "a\x1b]2;owned\x07b\nc", "s")              # no escape gets through
    assert mail.take("x-po-g1")[0]["text"] == "a]2;ownedb\nc"


@pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")
def test_a_member_gets_what_was_held_when_it_names_its_window():
    sess = Session("send")
    agent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        wid = json.loads(sess.pys("new", "--", "sleep", "60"))["wid"]
        r = subprocess.run([sys.executable, "-m", "py_screen.agents.listing", "hold", "gone-po-g1",
                                       "the build is green"], env=sess.env, capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
        assert r.returncode == 0 and "held for gone-po-g1" in r.stdout, r.stderr
        # a title without an owner is a person's: it takes nothing
        assert sess.pys("title", "--wid", str(wid), "gone-po-g1 x") == ""
        out = sess.pys("title", "--wid", str(wid), "--owner", str(agent.pid), "gone-po-g1 busy")
        assert "1 message held for gone-po-g1" in out and "the build is green" in out
        assert ", from " in out        # who sent it: see test_the_sender_is_named_by_pys
        assert sess.pys("title", "--wid", str(wid), "--owner", str(agent.pid),
                        "gone-po-g1 free") == ""                  # taken once
    finally:
        agent.kill()
        agent.wait()
        sess.kill()


def test_the_sender_is_named_by_pys(monkeypatch):
    """The sender's name comes from its own window's agent title, else its Claude session's
    name, else its window: the sender doesn't type it."""
    monkeypatch.setattr(listing, "windows", lambda: [
        {"wid": 3, "name": "by hand", "agent_title": "pys-po-g1 #12 busy"},
        {"wid": 4, "name": "bash", "agent_title": None}])
    monkeypatch.setattr(listing.claude, "agent_above", lambda pid: 77)
    monkeypatch.setattr(listing.claude, "live_sessions", lambda: {77: {"name": "product-a-07"}})
    monkeypatch.setenv("PYS_WINDOW", "3")
    assert listing.sender() == "pys-po-g1"
    monkeypatch.setenv("PYS_WINDOW", "4")
    assert listing.sender() == "product-a-07"
    monkeypatch.setattr(listing.claude, "live_sessions", lambda: {})
    assert listing.sender() == "window 4"
    monkeypatch.delenv("PYS_WINDOW")
    assert listing.sender() == "a process outside pys"
