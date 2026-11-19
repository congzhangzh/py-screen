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


def test_a_crash_loop_backs_off_to_a_minute_and_stays_there():
    """The real decision (Keeper.after_crash), driven as run_brain drives it: a brain that
    dies 1 s after each start. The backoff's sleep is not running time, so the waits climb
    to 60 s and stay; a brain that then runs 30 s starts the count again."""
    k = keeper.Keeper()
    waits = [k.after_crash(1.0) for _ in range(12)]
    assert waits[:4] == [0, 0, 0, 0] and waits[4:8] == [5, 10, 20, 40]
    assert waits[8:] == [60, 60, 60, 60]
    assert k.after_crash(31.0) == 0 and k.crashes == 1


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


def test_a_keeper_whose_socket_is_gone_starts_no_more_brains():
    """keeper.sock removed (its dir cleaned up, logind at logout): a brain could not reach
    the keeper, and one started anyway would make the dir again and take a newer keeper's
    place. The keeper stops starting brains and keeps its shells."""
    import json
    import subprocess
    sess = Session("sockgone")
    t = Term(PYS + ["attach"], sess.env, 100, 30)
    try:
        t.wait_for("$ ")
        kpid = int((sess.dir / "keeper.pid").read_text(encoding="utf-8"))
        brain = json.loads(sess.pys("ls", "--json"))["pid"]
        t.close()
        (sess.dir / "keeper.sock").unlink()
        os.kill(brain, signal.SIGKILL)

        def brains() -> list[str]:
            out = subprocess.run(["ps", "-ww", "-o", "pid=,ppid=,command=", "-A"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace").stdout
            return [l for l in out.splitlines() if "_brain" in l and l.split()[1] == str(kpid)]
        wait(lambda: "no more brains" in (sess.dir / "server.log").read_text(encoding="utf-8"),
             timeout=10, what=lambda: (sess.dir / "server.log").read_text(encoding="utf-8")[-800:])
        assert brains() == [] and keeper_runs(kpid)            # no brain; the shells stay
    finally:
        sess.kill()
        assert not keeper_runs(kpid)
