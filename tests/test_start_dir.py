"""A window opened from one whose folder can't be entered (gone, or handed to another user, as
a container's chown does) starts in the home folder and says so; it used to end at once with
exit 127 and nothing said why (2026-11-22). A window that ends at once with an error shows
its last line in the status line."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

import pytest

from harness import PYS, Session, Term
from test_mouse import wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")


def windows(sess: Session) -> dict[int, dict]:
    return {w["wid"]: w for w in json.loads(sess.pys("ls", "--json"))["windows"]}


def screen(sess: Session, wid: int) -> str:
    return "\n".join(json.loads(sess.pys("capture", "--json", str(wid)))["lines"])


@pytest.mark.parametrize("how", ["no access", "gone"])
def test_a_window_from_a_folder_it_cannot_enter_starts_at_home(how, tmp_path):
    if how == "no access" and os.geteuid() == 0:
        pytest.skip("root enters every folder")
    d = tmp_path / "locked"
    d.mkdir()
    sess = Session("startdir")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for("$ ")
        t.send(f"cd {d}\r")
        wait(lambda: windows(sess)[0].get("cwd") == str(d), what=lambda: windows(sess))
        if how == "no access":
            d.chmod(0)
        else:
            shutil.rmtree(d)
        t.send(b"\x1dc")                                        # prefix c, from window 0
        wait(lambda: len(windows(sess)) == 2, what=lambda: windows(sess))
        time.sleep(0.5)
        assert len(windows(sess)) == 2, "the new window ended"
        new = max(windows(sess))
        wait(lambda: "pys: cannot enter" in screen(sess, new), what=lambda: screen(sess, new))
        text = "".join(screen(sess, new).split())            # the line wraps at 120
        assert f"cannotenter{d}" in text and f"startedin{os.path.expanduser('~')}" in text
        assert windows(sess)[new]["cwd"] == os.path.expanduser("~")
    finally:
        if d.exists():
            d.chmod(0o700)
        t.close()
        sess.kill()


def test_a_window_that_ends_at_once_says_why():
    sess = Session("startfail")
    t = Term(PYS + ["attach"], sess.env, 140, 30)
    try:
        t.wait_for("$ ")
        sess.pys("new", "--select", "/no/such/program", check=False)
        t.wait_for("ended at once", timeout=5)
        line = next(l for l in t.text().splitlines() if "ended at once" in l)
        assert "cannot start /no/such/program" in line and "(exit 127)" in line, line
        log = (sess.dir / "server.log").read_text(encoding="utf-8", errors="replace")
        assert "ended at once: pys: cannot start /no/such/program" in log
    finally:
        t.close()
        sess.kill()
