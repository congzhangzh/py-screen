"""Which title a window shows: set by hand, then by `pys title`, then the app's own."""

from __future__ import annotations

from py_screen.brain import Window


def test_title_order() -> None:
    w = Window.new(0, 1, ["/bin/sh"], 40, 5)
    assert w.name == "sh"
    w.pending += b"\x1b]2;app\x07"
    w.parse(len(w.pending))
    assert w.name == "app"
    w.agent_title = "agent"
    assert w.name == "agent"
    w.title = "mine"
    assert w.name == "mine"
    w.title = None                                # a rename to nothing: the agent's again
    assert w.name == "agent"


def test_proc_start() -> None:
    import subprocess
    import sys

    from py_screen import platform
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    token = platform.proc_start(p.pid)
    assert token is not None and platform.proc_start(p.pid) == token   # stable while it runs
    p.kill()
    p.wait()
    assert platform.proc_start(p.pid) is None
