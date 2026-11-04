"""pys inside pys: attaching from a window of the same session is refused (it would show
the session inside itself, and the keys would loop between the window and the brain);
another session may be nested; and in a real window, `pys` says so and the shell goes on."""

import os
import shutil
import subprocess
import sys

import pytest
from harness import PYS, Session, Term, session_dir


def attach(env: dict) -> subprocess.CompletedProcess:
    # no terminal on stdin: the guard comes first, so a passing guard ends at "needs a terminal"
    return subprocess.run(PYS + ["attach"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=15, check=False)


@pytest.fixture
def d():
    """A short session dir (pys creates it when it resolves it), removed afterwards."""
    p = session_dir("nest")
    yield p
    shutil.rmtree(p, ignore_errors=True)


def env_in_window(d, **extra) -> dict:
    # d from session_dir(), not pytest's tmp_path: macOS's tmp_path is longer than the
    # 104 bytes a socket path may have there, and pys stops at that before the guard
    env = dict(os.environ, PYS_DIR=str(d), PYS_WINDOW="3")
    env.pop("PYS_SESSION", None)
    return {**env, **extra}


def test_same_session_is_refused(d):
    r = attach(env_in_window(d, PYS_SESSION=str(d)))
    assert r.returncode == 1 and "already inside this session (window 3)" in r.stderr
    assert not (d / "server.log").exists()                  # no server was started


def test_old_keeper_without_pys_session_counts_as_same(d):
    r = attach(env_in_window(d))
    assert "already inside this session (window 3)" in r.stderr


def test_another_session_may_nest(d):
    r = attach(env_in_window(d, PYS_SESSION=str(d) + "-other"))
    assert "already inside" not in r.stderr and "attach needs a terminal" in r.stderr


def test_outside_pys_is_unchanged(d):
    env = env_in_window(d)
    del env["PYS_WINDOW"]
    assert "attach needs a terminal" in attach(env).stderr


def test_pys_typed_in_a_window_is_refused():
    sess = Session("nest")
    t = Term(PYS + ["attach"], sess.env, 140, 20)
    try:
        t.wait_for(" pys ")
        t.send(f'echo "[$PYS_SESSION]"; {sys.executable} -m py_screen; echo "rc=$?"\r')
        t.wait_for("rc=1")
        screen = t.text()
        assert f"[{os.path.realpath(sess.dir)}]" in screen       # the keeper names the session
        assert "already inside this session (window 0)" in screen
        t.send("echo still-here\r")                                # the window still answers
        t.wait_until(lambda s: s.count("still-here") >= 2, timeout=5)   # typed, then printed
    finally:
        t.close()
        sess.kill()
