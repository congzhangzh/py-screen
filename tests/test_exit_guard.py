"""When the focused window ends, the keys that follow are held, not sent to the window that
pys focused by itself: a second Ctrl-D must not end the next window too."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from harness import PYS, Session, Term


def test_keys_after_the_focused_window_ended_are_held(tmp_path: Path) -> None:
    sess = Session("eg", shell="/bin/sh")
    t = Term(PYS + ["attach"], sess.env)
    try:
        t.wait_for("$ ")
        # window 1: a shell in tmp_path; then window 2, focused, ends on the first Enter
        t.send(f"cd {tmp_path}\r")
        subprocess.run(PYS + ["new", "--select", "sh", "-c", "read x"], env=sess.env,
                       check=True, capture_output=True, timeout=15)
        time.sleep(0.5)
        t.send("\r")                              # window 2 reads it and ends
        t.wait_for("ended")                       # the notice: focus moved by itself
        t.send("touch leaked\r")                  # meant for window 2: must not reach window 0
        time.sleep(EXIT_WAIT)
        t.send("touch typed-later\r")             # after the hold: goes to window 0 again
        deadline = time.time() + 5
        while time.time() < deadline and not (tmp_path / "typed-later").exists():
            time.sleep(0.1)
        assert (tmp_path / "typed-later").exists()
        assert not (tmp_path / "leaked").exists()
        assert "ended while focused: keys held" in (sess.dir / "server.log").read_text()
    finally:
        t.close()
        sess.kill()


EXIT_WAIT = 2.0          # past EXIT_GUARD (1 s) and the burst gap after the last held key
