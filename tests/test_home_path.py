"""Folders on screen in the home style: ~/x below the home folder, as they are elsewhere."""
from __future__ import annotations

import subprocess
from pathlib import Path

from harness import PYS, Session, Term

from py_screen.config import home_path


def test_home_path(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/home/u")
    assert home_path("/home/u") == "~"
    assert home_path("/home/u/src/app") == "~/src/app"
    assert home_path("/home/user2/x") == "/home/user2/x"      # a name that only starts the same
    assert home_path("/tmp/x") == "/tmp/x"
    assert home_path(None) == ""


def test_the_caption_line_shows_the_folder_home_style(tmp_path: Path) -> None:
    """No CAPTION in the config: the caption line shows the window's folder, as ~/…"""
    sess = Session("home", shell="/bin/sh")
    sess.env["HOME"] = str(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(PYS + ["new"], env=sess.env, cwd=work, check=True, capture_output=True,
                   timeout=15)
    t = Term(PYS + ["attach"], sess.env)
    try:
        t.wait_for("~/work")
        assert str(work) not in t.text()
    finally:
        t.close()
        sess.kill()
