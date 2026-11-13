"""The settings panel: prefix o or a click on " pys " opens it; a change applies at once and
is kept in config.d/50-settings.py, which the next brain reads like any drop-in."""

from __future__ import annotations

import json
import sys

import pytest

from py_screen import settings


def test_the_file_reads_back_only_plain_values(tmp_path):
    p = tmp_path / "50-settings.py"
    settings.write({"STATUS_LINES": 3, "BUTTONS": False}, p)
    text = p.read_text()
    assert text.index("BUTTONS = False") < text.index("STATUS_LINES = 3")   # panel order
    assert settings.read(p) == {"BUTTONS": False, "STATUS_LINES": 3}
    p.write_text(text + "MOUSE = 'no'\nSTATUS_LINES = 9\nimport os\nWIDGETS = []\n")
    # not a value it offers: left out (the config loader still runs the file, so 9 counts
    # there; the panel's next write keeps only its own lines)
    assert settings.read(p) == {"BUTTONS": False, "STATUS_LINES": 3}
    assert settings.read(tmp_path / "none.py") == {}
    p.write_text("BUTTONS = (")                           # broken: as if absent
    assert settings.read(p) == {}
    assert not list(tmp_path.glob(".*.tmp"))              # the atomic write left nothing


def test_the_about_line():
    from py_screen import about
    assert about.SLOGAN in about.line()
    import subprocess
    from pathlib import Path
    repo = Path(about.__file__).resolve().parents[2]
    if (repo / ".git").exists():                          # tests run from a checkout
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        assert sha in about.version() and "0.1.0" not in about.version()


def test_step():
    assert settings.step("BUTTONS", True) is False
    assert settings.step("STATUS_LINES", 2) == 3 and settings.step("STATUS_LINES", 3) == 1
    assert settings.step("STATUS_LINES", 7) == 1          # a config's own value: back to the first


@pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")
def test_the_panel_changes_and_keeps_a_setting(tmp_path):
    from harness import PYS, Session, Term
    from test_mouse import click, wait
    cfg = tmp_path / "config.py"
    cfg.write_text("STATUS_LINES = 2\n")
    sess = Session("settings")
    sess.env["PYS_CONFIG"] = str(cfg)
    t = Term(PYS + ["attach"], sess.env, 100, 20)
    kept = tmp_path / "config.d" / settings.FILE
    try:
        t.wait_for("$ ")
        assert t.text().splitlines()[-1].startswith(" pys  +  ▾  ?  ")
        t.send(b"\x1do")                                  # prefix o
        t.wait_for(" py-screen settings")
        assert "from config.py" in t.text()               # STATUS_LINES' row says where
        assert " Files pys uses" in t.text() and "✓ config" in t.text()
        assert "dev · " in t.text().splitlines()[0]      # the version, right of the title
        assert "this pys's code" in t.text()
        t.send(b"\r")                                     # row 1: the buttons, off
        wait(lambda: kept.exists() and "BUTTONS = False" in kept.read_text(),
             what=lambda: t.text())
        wait(lambda: "set here" in t.text(), what=lambda: t.text())
        t.send(b"q")
        wait(lambda: t.text().splitlines()[-1].startswith(" pys  0* sh"), what=lambda: t.text())

        click(t, 2, 19)                                   # a click on " pys " opens it too
        t.wait_for(" py-screen settings")
        click(t, 10, settings.KEYS.index("STATUS_LINES") + 2)   # 2 -> 3
        wait(lambda: "STATUS_LINES = 3" in kept.read_text(), what=lambda: kept.read_text())
        click(t, 10, 15)                                  # off the rows: closes
        wait(lambda: " py-screen settings" not in t.text(), what=lambda: t.text())

        old = sess.pys("ls", "--json")
        sess.pys("reload")                                # a new brain reads the file
        wait(lambda: sess.pys("ls", "--json", check=False).strip(), timeout=10)
        t.send(b"\x1d\x0c")                               # prefix C-l: its own frame
        wait(lambda: t.text().splitlines()[-1].startswith(" pys  0* sh"), what=lambda: t.text())
        assert json.loads(old)["windows"] == json.loads(sess.pys("ls", "--json"))["windows"]
    finally:
        t.close()
        sess.kill()
