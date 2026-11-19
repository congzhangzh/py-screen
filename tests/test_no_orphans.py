"""A test server is never left behind: when `pys kill` can't reach it (a brain that can't
answer), the harness stops its keeper by keeper.pid; a brain that keeps crashing is restarted
with a growing wait, and server.log is cut back before it can fill a tmpfs."""
from __future__ import annotations

import os
import signal
import sys

import pytest

from harness import PYS, Session, Term, keeper_runs
from py_screen import keeper
from test_mouse import wait

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")


def test_crash_wait_grows_and_stops_at_a_minute():
    now = 1000.0
    waits = [keeper.crash_wait([now - i for i in range(n)], now) for n in range(1, 12)]
    assert waits[:4] == [0, 0, 0, 0] and waits[4] == 5 and waits[5] == 10
    assert max(waits) == 60 and waits == sorted(waits)


def test_kill_stops_a_keeper_whose_brain_cannot_answer():
    sess = Session("orphan")
    t = Term(PYS + ["attach"], sess.env, 100, 30)
    t.wait_for("$ ")
    pid = int((sess.dir / "keeper.pid").read_text(encoding="utf-8"))
    assert keeper_runs(pid)
    t.close()
    (sess.dir / "brain.sock").unlink()        # pys kill can reach nothing now
    try:
        sess.kill()
        wait(lambda: not keeper_runs(pid), timeout=10, what=lambda: f"keeper {pid} still runs")
    finally:
        if keeper_runs(pid):
            os.kill(pid, signal.SIGKILL)


def test_the_log_is_cut_in_a_crash_loop(tmp_path, monkeypatch):
    log = tmp_path / "server.log"
    with open(log, "ab") as f:                # as the server's stderr is opened (append)
        f.write(b"x" * (keeper.LOG_CAP + 4096))
        f.flush()
        fake = type("E", (), {"fileno": lambda self: f.fileno()})()
        monkeypatch.setattr(keeper.sys, "stderr", fake)
        keeper.cap_log(log)
        f.write(b"after\n")
    data = log.read_bytes()
    assert len(data) < keeper.LOG_KEEP + 200 and data.startswith(b"[server.log cut")
    assert data.endswith(b"after\n")
