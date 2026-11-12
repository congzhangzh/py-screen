"""The status line takes a second row when the window tabs don't fit in one (STATUS_LINES,
2 by default), and gives it back when a window ends and one row holds them again."""

from __future__ import annotations

import json
import re
import sys
import time

import pytest

from harness import PYS, Session, Term
from test_mouse import click, wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")

COLS, ROWS = 60, 20


def ls(sess: Session) -> dict:
    return json.loads(sess.pys("ls", "--json"))


def pane_h(sess: Session) -> int:
    return ls(sess)["panes"][0]["h"]


def test_tabs_wrap_onto_a_second_row_and_give_it_back():
    sess = Session("rows")
    t = Term(PYS + ["attach"], sess.env, COLS, ROWS)
    try:
        t.wait_for("$ ")
        assert pane_h(sess) == ROWS - 1                       # one status row
        while len(ls(sess)["windows"]) < 9:                    # " 3 sh " each: too many for 60
            sess.pys("new")
        wait(lambda: pane_h(sess) == ROWS - 2, what=lambda: t.text())
        rows = t.text().splitlines()
        top, bottom = rows[-2], rows[-1]
        assert top.startswith(" pys  + ")                      # tabs flow from the first row
        last = max(w["wid"] for w in ls(sess)["windows"])
        assert re.search(rf" {last}[-*]? sh ", bottom)          # the newest wrapped to the last
        assert re.search(r"\d\d-\d\d \d\d:\d\d $", bottom)     # widgets stay bottom right

        # a click on a tab of the first row selects it
        other = ls(sess)["windows"][1]["wid"]
        m = re.search(rf" {other}[-*]? sh ", top)
        click(t, m.start() + 1, ROWS - 2)
        wait(lambda: ls(sess)["current"] == other, what=lambda: t.text())

        # windows end until one row holds the rest: the row goes back to the panes
        for w in ls(sess)["windows"][4:]:
            sess.pys("send", "--enter", str(w["wid"]), "exit")
        wait(lambda: len(ls(sess)["windows"]) == 4, what=lambda: ls(sess))
        wait(lambda: pane_h(sess) == ROWS - 1, what=lambda: t.text())
        assert t.text().splitlines()[-1].startswith(" pys  + ")
    finally:
        t.close()
        sess.kill()


def test_one_status_row_when_the_config_says_so(tmp_path):
    cfg = tmp_path / "config.py"
    cfg.write_text("STATUS_LINES = 1\n")
    sess = Session("rows1")
    sess.env["PYS_CONFIG"] = str(cfg)
    t = Term(PYS + ["attach"], sess.env, COLS, ROWS)
    try:
        t.wait_for("$ ")
        while len(ls(sess)["windows"]) < 9:
            sess.pys("new")
        t.wait_for(" 4 sh")
        time.sleep(0.5)                                        # the frames after the last new
        assert pane_h(sess) == ROWS - 1                        # one row: tabs are left out
        assert " 8 " not in t.text().splitlines()[-1]
    finally:
        t.close()
        sess.kill()
