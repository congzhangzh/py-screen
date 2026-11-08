"""What pys keeps of its windows: ON_CHANGE runs after they change and before a kill
ends them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from harness import PYS, Session, Term

# The hook: append "<PYS_EVENT> <number of windows>" to events, keep `pys ls --json`.
HOOK = """\
import json, os, subprocess, sys
out, event = sys.argv[1], os.environ["PYS_EVENT"]
ls = subprocess.run([sys.executable, "-m", "py_screen", "ls", "--json"],
                    capture_output=True, text=True).stdout
n = len(json.loads(ls)["windows"]) if ls else -1
with open(os.path.join(out, "ls-" + event + ".tmp"), "w") as f:
    f.write(ls)
os.replace(os.path.join(out, "ls-" + event + ".tmp"), os.path.join(out, "ls-" + event + ".json"))
with open(os.path.join(out, "events"), "a") as f:
    f.write(f"{event} {n}\\n")
"""


def hooked(tmp_path: Path, tag: str) -> Session:
    (tmp_path / "hook.py").write_text(HOOK)
    (tmp_path / "config.py").write_text(
        f"ON_CHANGE = [{sys.executable!r}, {str(tmp_path / 'hook.py')!r}, {str(tmp_path)!r}]\n")
    sess = Session(tag)
    sess.env["PYS_CONFIG"] = str(tmp_path / "config.py")
    return sess


def events(tmp_path: Path) -> list[str]:
    try:
        return (tmp_path / "events").read_text().splitlines()
    except FileNotFoundError:
        return []


def wait(pred, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if v := pred():
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def new_in(sess: Session, cwd: Path, *argv: str) -> dict:
    r = subprocess.run(PYS + ["new", *argv], env=sess.env, cwd=cwd, capture_output=True,
                       text=True, timeout=15)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_on_change_after_a_new_window(tmp_path: Path) -> None:
    sess = hooked(tmp_path, "chg")
    try:
        new_in(sess, tmp_path)
        wait(lambda: "change 1" in events(tmp_path))      # the brain's start, the first window
        new_in(sess, tmp_path)
        wait(lambda: "change 2" in events(tmp_path))
    finally:
        sess.kill()


def test_kill_runs_on_change_while_the_windows_live(tmp_path: Path) -> None:
    sess = hooked(tmp_path, "kil")
    work = tmp_path / "work"
    work.mkdir()
    try:
        a = new_in(sess, work)
        b = new_in(sess, work, "sleep", "300")
        r = subprocess.run(PYS + ["kill"], env=sess.env, capture_output=True, text=True,
                           timeout=30)
        assert r.returncode == 0, r.stderr
        assert events(tmp_path)[-1] == "kill 2"           # it ran before `pys kill` returned
        ls = json.loads((tmp_path / "ls-kill.json").read_text())
        assert [w["wid"] for w in ls["windows"]] == [a["wid"], b["wid"]]
        assert {w["cwd"] for w in ls["windows"]} == {str(work.resolve())}
    finally:
        sess.kill()


def test_prefix_q_runs_on_change_first(tmp_path: Path) -> None:
    sess = hooked(tmp_path, "q")
    t = Term(PYS + ["attach"], sess.env)
    try:
        t.wait_for("$ ")
        t.send("\x1dQ")                                    # the tests' prefix is C-]
        assert t.wait_exit() == 0
        assert events(tmp_path)[-1] == "kill 1"
    finally:
        t.close()
        sess.kill()
