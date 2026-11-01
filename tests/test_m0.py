"""M0 done-criteria (spec §6): attach, detach, reattach; a hot restart keeps vim."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from harness import PYS, Session, Term

C_RB = "\x1d"  # C-], the prefix


@pytest.fixture
def sess():
    s = Session()
    yield s
    s.kill()


def attach(s: Session) -> Term:
    t = Term(PYS + ["attach"], s.env)
    t.wait_for(" pys ")
    return t


def test_attach_detach_reattach(sess):
    t = attach(sess)
    t.wait_for("$ ")
    t.send("echo hi-$((40+2))\r")
    t.wait_for("hi-42")
    t.send(C_RB + "d")
    assert t.wait_exit() == 0
    assert b"[detached]" in t.raw
    t.close()

    windows = json.loads(sess.pys("ls", "--json"))["windows"]
    assert len(windows) == 1

    t2 = attach(sess)                   # full redraw from the brain's screen
    t2.wait_for("hi-42")
    t2.send(C_RB + "c")                 # a second window
    t2.wait_for(" 1 sh ")
    t2.send("echo second\r")
    t2.wait_for("second")
    t2.send(C_RB + "0")
    t2.wait_for("hi-42")
    t2.send("exit\r")
    t2.wait_until(lambda txt: " 0 sh " not in txt and "second" in txt)  # window 1 is current
    t2.send("exit\r")
    assert t2.wait_exit() == 0
    assert b"[session ended]" in t2.raw
    t2.close()


def vim_pid(shell_pid: int) -> int | None:
    r = subprocess.run(["pgrep", "-P", str(shell_pid), "vim"], capture_output=True, text=True)
    return int(r.stdout.split()[0]) if r.stdout.strip() else None


def test_reload_keeps_vim(sess, record_property):
    t = attach(sess)
    t.wait_for("$ ")
    t.send("vim --clean -N\r")
    t.wait_for("~")
    t.send("ihello-vim\x1b")
    t.wait_for("hello-vim")
    info = json.loads(sess.pys("ls", "--json"))
    shell = info["windows"][0]["pid"]
    vim = vim_pid(shell)
    assert vim, "vim not running"

    times = []
    brain = info["pid"]
    for _ in range(20):
        t0 = time.monotonic()
        sess.pys("reload")
        while True:                          # until a new brain answers
            out = subprocess.run(PYS + ["ls", "--json"], env=sess.env,
                                 capture_output=True, text=True)
            if out.returncode == 0 and json.loads(out.stdout)["pid"] != brain:
                brain = json.loads(out.stdout)["pid"]
                break
            time.sleep(0.01)
        times.append(time.monotonic() - t0)
        assert "hello-vim" in sess.pys("capture", "0")      # the brain rebuilt the screen
        t.wait_for("hello-vim")                              # the client shows it again
        assert vim_pid(shell) == vim, "vim was restarted or killed"
        assert t.alive, "client exited on reload"
    times.sort()
    record_property("reload_median_s", times[len(times) // 2])
    record_property("reload_max_s", times[-1])
    print(f"\nreload x20: median {times[10] * 1000:.0f} ms, max {times[-1] * 1000:.0f} ms")

    t.send("\x1b:q!\r")
    t.wait_for("$ ")
    t.send("exit\r")
    assert t.wait_exit() == 0
    t.close()
