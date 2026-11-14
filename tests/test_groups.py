"""Product groups: `pys group` puts a window in one; the status line shows the groups on one
row while it holds every tab, else a line per group with pys's own row last; the group
lasts across a brain reload; `pys-agents team` lists a group's windows with their roles."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from harness import PYS, Session, Term
from test_mouse import click, wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")


def ls(sess: Session) -> dict:
    return json.loads(sess.pys("ls", "--json"))


def pane_h(sess: Session) -> int:
    return ls(sess)["panes"][0]["h"]


def three_windows(sess: Session) -> list[int]:
    for _ in range(2):
        sess.pys("new")
    wids = [w["wid"] for w in ls(sess)["windows"]]
    sess.pys("group", "--wid", str(wids[0]), "product-a")
    sess.pys("group", "--wid", str(wids[1]), "product-b")
    sess.pys("group", "--wid", str(wids[2]), "product-a")
    return wids


def test_group_names_and_listing():
    sess = Session("grp")
    t = Term(PYS + ["attach"], sess.env, 120, 20)
    try:
        t.wait_for("$ ")
        a, b, c = three_windows(sess)
        assert [w["group"] for w in ls(sess)["windows"]] == ["product-a", "product-b", "product-a"]
        assert sess.pys("group", "--wid", str(b)).strip() == "product-b"
        assert sess.pys("group", "--list").splitlines() == [f"product-a: {a} {c}", f"product-b: {b}"]
        r = subprocess.run(PYS + ["group", "--wid", str(a), "no spaces"], env=sess.env,
                           capture_output=True, text=True, timeout=15)
        assert r.returncode and "letters, digits" in r.stderr
        sess.pys("group", "--wid", str(b), "--clear")
        assert ls(sess)["windows"][1]["group"] is None
    finally:
        t.close()
        sess.kill()


def test_one_row_while_it_holds_them_then_a_line_per_group():
    sess = Session("grprows")
    t = Term(PYS + ["attach"], sess.env, 120, 20)
    try:
        t.wait_for("$ ")
        a, b, c = three_windows(sess)
        # few windows: one row, each group led by its name
        wait(lambda: " product-b│" in t.text().splitlines()[-1], what=lambda: t.text())
        row = t.text().splitlines()[-1]
        assert row.startswith(" pys  +  ▾  ?  product-a│")
        assert row.index(f" {a}") < row.index(f" {c}") < row.index(" product-b│")  # a's together
        assert pane_h(sess) == 20 - 1

        # long titles: a line per product, pys's own row last
        for w in (a, b, c):
            sess.pys("title", "--wid", str(w), f"member-of-a-product-with-a-long-name-{w}")
        wait(lambda: pane_h(sess) == 20 - 3, what=lambda: t.text())
        lines = t.text().splitlines()
        assert lines[-3].startswith(" product-a ") and f" {a}" in lines[-3] and f" {c}" in lines[-3]
        assert lines[-2].startswith(" product-b ") and f" {b}" in lines[-2]
        assert lines[-1].startswith(" pys  +  ▾  ? ")

        # a click on a tab in a product line selects that window
        x = lines[-2].index(f" {b}") + 1
        click(t, x, 20 - 2)
        wait(lambda: ls(sess)["current"] == b, what=lambda: t.text())

        # a reload keeps the groups and the lines
        sess.pys("reload")
        wait(lambda: sess.pys("ls", "--json", check=False).strip(), timeout=10)
        assert [w["group"] for w in ls(sess)["windows"]] == ["product-a", "product-b", "product-a"]
        t.send(b"\x1d\x0c")                                # prefix C-l: the new brain's frame
        wait(lambda: t.text().splitlines()[-3].startswith(" product-a "), what=lambda: t.text())
    finally:
        t.close()
        sess.kill()


def test_team_lists_a_group_with_roles():
    sess = Session("grpteam")
    t = Term(PYS + ["attach"], sess.env, 120, 20)
    try:
        t.wait_for("$ ")
        a, b, c = three_windows(sess)
        sess.pys("title", "--wid", str(a), "po-product-a-g1 free")
        sess.pys("title", "--wid", str(c), "rel-product-a-g2 #4 busy")
        team = [sys.executable, "-m", "py_screen.agents.listing", "team"]
        r = subprocess.run(team + ["--group", "product-a", "--json"], env=sess.env,
                           capture_output=True, text=True, timeout=30, check=True)
        got = json.loads(r.stdout)
        assert got["group"] == "product-a"
        assert [(w["window"], w["role"]) for w in got["windows"]] == [(a, "po"), (c, "rel")]
        # a window in the group "shared" (an edu for every product) is in every team
        sess.pys("new")
        d = max(w["wid"] for w in ls(sess)["windows"])
        sess.pys("title", "--wid", str(d), "edu-all-g1 free")
        sess.pys("group", "--wid", str(d), "shared")
        for g in ("product-a", "product-b"):
            r = subprocess.run(team + ["--group", g, "--json"], env=sess.env,
                               capture_output=True, text=True, timeout=30, check=True)
            rows = json.loads(r.stdout)["windows"]
            assert (rows[-1]["window"], rows[-1]["role"], rows[-1]["shared"]) == (d, "edu", True)
            assert not any(w["shared"] for w in rows[:-1])
        # from inside a window: its own group
        env = dict(sess.env, PYS_WINDOW=str(b), PYS_SESSION=str(sess.dir))
        r = subprocess.run(team, env=env, capture_output=True, text=True, timeout=30, check=True)
        assert r.stdout.startswith("group product-b:")
    finally:
        t.close()
        sess.kill()


def test_a_product_line_wraps_under_its_name():
    """A product whose tabs don't fit its row goes on on a row of its own, under its name,
    every tab whole (its PO must not shrink to a number)."""
    sess = Session("grpwrap")
    t = Term(PYS + ["attach"], sess.env, 80, 20)
    try:
        t.wait_for("$ ")
        a, b, c = three_windows(sess)
        for w in (a, b, c):
            sess.pys("title", "--wid", str(w), f"po-a-product-with-a-long-name-{w}")
        wait(lambda: pane_h(sess) == 20 - 4, what=lambda: t.text())   # a: 2 rows, b, pys
        lines = t.text().splitlines()
        assert lines[-4].startswith(" product-a ") and f" {a}* po-a-product" in lines[-4]
        assert lines[-3].startswith("           ") and f" {c} po-a-product" in lines[-3]
        assert lines[-2].startswith(" product-b ")
    finally:
        t.close()
        sess.kill()


def test_windows_in_no_group_get_a_line_and_pys_row_holds_none():
    """In the product lines a window in no group has a line of its own, "others"; pys's own
    row holds no window, only the buttons, the motto and its maker, the widgets. GROUPS =
    False in the panel gives the plain flow back."""
    from py_screen import about
    sess = Session("grpothers")
    t = Term(PYS + ["attach"], sess.env, 140, 20)
    try:
        t.wait_for("$ ")
        a, b, c = three_windows(sess)
        sess.pys("group", "--wid", str(c), "--clear")
        # few windows: one row, the loose one led by "others" (not after product-b's)
        wait(lambda: " others│" in t.text().splitlines()[-1], what=lambda: t.text())
        row = t.text().splitlines()[-1]
        assert row.index(" product-b│") < row.index(" others│") < row.index(f" {c}")
        for w in (a, b, c):
            sess.pys("title", "--wid", str(w), f"member-of-a-product-with-a-long-name-{w}")
        wait(lambda: pane_h(sess) == 20 - 4, what=lambda: t.text())   # a, b, others, pys
        lines = t.text().splitlines()
        assert lines[-4].startswith(" product-a ") and lines[-3].startswith(" product-b ")
        assert lines[-2].startswith(" others ") and f" {c}" in lines[-2]
        assert lines[-1].startswith(" pys  +  ▾  ? ")
        assert about.SLOGAN in lines[-1]                   # no tabs: the motto and its maker
        assert not any(f" {w} " in lines[-1] or f" {w}* " in lines[-1] for w in (a, b, c))

        t.send(b"\x1do")                                   # the panel: groups off
        t.wait_for(" py-screen settings")
        from py_screen import settings
        click(t, 10, settings.KEYS.index("GROUPS") + 2)
        wait(lambda: "GROUPS = False" in t.text(), what=lambda: t.text())
        t.send(b"q")
        wait(lambda: pane_h(sess) == 20 - 2, what=lambda: t.text())   # the plain flow
        assert "product-a" not in t.text().splitlines()[-2]
        assert sess.pys("group", "--wid", str(a)).strip() == "product-a"   # kept
    finally:
        t.close()
        sess.kill()


def test_shared_and_others_share_a_line():
    """The windows of the group "shared" (they serve every product) and those in no group
    take one line: shared first, then others│; each window once, never under every group."""
    sess = Session("grpshared")
    t = Term(PYS + ["attach"], sess.env, 140, 20)
    try:
        t.wait_for("$ ")
        a, b, c = three_windows(sess)
        sess.pys("new")
        d = max(w["wid"] for w in ls(sess)["windows"])          # d: in no group
        sess.pys("group", "--wid", str(c), "shared")
        for w in (a, b, c, d):
            sess.pys("title", "--wid", str(w), f"member-of-a-product-with-a-long-name-{w}")
        wait(lambda: pane_h(sess) == 20 - 4, what=lambda: t.text())   # a, b, shared+others, pys
        lines = t.text().splitlines()
        assert lines[-4].startswith(" product-a ") and lines[-3].startswith(" product-b ")
        tail = lines[-2]
        assert tail.startswith(" shared ")
        assert tail.index(f" {c} ") < tail.index(" others│") < tail.index(f" {d} ")
        assert sum(l.count(f" {c} member") for l in lines) == 1         # drawn once
        assert lines[-1].startswith(" pys  +  ▾  ? ")

        sess.pys("group", "--wid", str(d), "product-b")                 # no loose window left
        wait(lambda: " others│" not in t.text(), what=lambda: t.text())
        assert t.text().splitlines()[-2].startswith(" shared ")
    finally:
        t.close()
        sess.kill()
