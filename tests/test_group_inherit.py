"""A window opened from another joins its product group (prefix c, `pys new` typed in it);
`pys new --group` says which ("" none); the SessionStart hook tells an agent whose window is in
no group to join its product's."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from harness import PYS, Session, Term
from test_mouse import wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")


def groups(sess: Session) -> dict[int, str | None]:
    return {w["wid"]: w["group"] for w in json.loads(sess.pys("ls", "--json"))["windows"]}


def inside(sess: Session, wid: int, *args: str) -> dict:
    """`pys …` as typed in window wid of this session."""
    env = dict(sess.env, PYS_WINDOW=str(wid), PYS_SESSION=str(sess.dir))
    r = subprocess.run(PYS + list(args), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=15, check=True)
    return json.loads(r.stdout)


def test_a_new_window_takes_the_group_of_the_one_it_came_from():
    sess = Session("grpinh")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for("$ ")
        sess.pys("group", "--wid", "0", "product-a")
        a = inside(sess, 0, "new")["wid"]                       # typed in window 0
        b = inside(sess, 0, "new", "--group", "product-b")["wid"]
        c = inside(sess, 0, "new", "--group", "")["wid"]        # explicitly none
        d = json.loads(sess.pys("new"))["wid"]                  # from outside any window
        g = groups(sess)
        assert (g[a], g[b], g[c], g[d]) == ("product-a", "product-b", None, None), g
        t.send(b"\x1dc")                                        # prefix c, from window 0
        wait(lambda: len(groups(sess)) == 6, what=lambda: groups(sess))
        new = max(groups(sess))
        assert groups(sess)[new] == "product-a"
    finally:
        t.close()
        sess.kill()


def test_the_hook_nudges_a_window_in_no_group():
    sess = Session("grpnudge")
    t = Term(PYS + ["attach"], sess.env, 120, 30)
    try:
        t.wait_for("$ ")
        sess.pys("new")
        sess.pys("group", "--wid", "1", "product-a")

        def hook(wid: int) -> str:
            env = dict(sess.env, PYS_WINDOW=str(wid), PYS_SESSION=str(sess.dir))
            env.pop("PYS_DIR")                                  # found from PYS_SESSION
            r = subprocess.run([sys.executable, "-m", "py_screen.agents.instructions"], env=env,
                               input=json.dumps({"hook_event_name": "SessionStart"}),
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
            return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "in no product group" in hook(0)
        assert "in no product group" not in hook(1)
    finally:
        t.close()
        sess.kill()
