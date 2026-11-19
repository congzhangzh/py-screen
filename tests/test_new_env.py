"""`pys new --env K=V`: the value reaches that window's process only, never another window or
the server's own environment, and is kept nowhere (no log, no ls, no layout, no record)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from harness import PYS, Session, Term
from test_mouse import wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")

SECRET = "s3cret-v4lue-not-for-logs"


def screen(sess: Session, wid: int) -> str:
    return sess.pys("capture", str(wid))


def test_env_reaches_that_window_only_and_is_kept_nowhere():
    sess = Session("newenv")
    t = Term(PYS + ["attach"], sess.env, 100, 30)
    try:
        t.wait_for("$ ")
        show = 'echo "[$FOO][$TOKEN][$PYS_WINDOW]"; sleep 60'
        r = json.loads(sess.pys("new", "--env", "FOO=bar baz", "--env", f"TOKEN={SECRET}",
                                "--env", "PYS_WINDOW=99", "--", "sh", "-c", show))
        wid = r["wid"]
        wait(lambda: f"[bar baz][{SECRET}][{wid}]" in screen(sess, wid),   # pys's own name wins
             what=lambda: screen(sess, wid))
        other = json.loads(sess.pys("new", "--", "sh", "-c", show))["wid"]
        wait(lambda: f"[][][{other}]" in screen(sess, other), what=lambda: screen(sess, other))

        # kept nowhere: not in ls, the layout, the server record or the log
        assert SECRET not in sess.pys("ls", "--json")
        files = [p for p in sess.dir.rglob("*") if p.is_file()]
        assert files and not [p for p in files
                              if SECRET.encode() in p.read_bytes()], [str(p) for p in files]
    finally:
        t.close()
        sess.kill()


def test_a_bad_env_is_refused_without_its_value():
    sess = Session("newenvbad")
    t = Term(PYS + ["attach"], sess.env, 100, 30)
    try:
        t.wait_for("$ ")
        for bad in ("NOEQUALS", "=value-only"):
            r = subprocess.run(PYS + ["new", "--env", bad], env=sess.env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=15)
            assert r.returncode and "--env takes K=V" in r.stderr
            assert "value-only" not in r.stderr
        assert len(json.loads(sess.pys("ls", "--json"))["windows"]) == 1   # nothing opened
    finally:
        t.close()
        sess.kill()
