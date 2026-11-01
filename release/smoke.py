#!/usr/bin/env python3
"""Smoke-test an installed `pys` (the release's wheel, not the checkout), on Linux, macOS
and Windows alike: start a private server, open a window with the system's shell, type
into it, read its screen back, end the session. No outer terminal is needed: it all goes
over the RPC link.

Usage: release/smoke.py [pys command]      (default: pys from PATH)
Set PYS_TERM=vterm to require the native engine: the server then fails to start without it.
Exit 0 = ok, 1 = failed (the server's log is printed).
"""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

WIN = sys.platform == "win32"
pys = shutil.which(sys.argv[1] if len(sys.argv) > 1 else "pys")
if not pys:
    sys.exit("smoke: no pys on PATH")

# A private server: its own runtime dir (short on POSIX, for AF_UNIX's sun_path), no user
# config, a plain shell. It never touches a session already running on this machine.
run_dir = Path(tempfile.mkdtemp(prefix="pys-smoke.", dir=None if WIN else "/tmp"))
env = dict(os.environ, PYS_DIR=str(run_dir), PYS_CONFIG=str(run_dir / "none.py"),
           TERM="xterm-256color")
if WIN:
    # cmd expands %OS% only in the output, so the screen shows the answer, not the typing
    line, want = "echo smoke-%OS%", "smoke-Windows_NT"
else:
    env.update(SHELL="/bin/sh", PS1="$ ")
    env.pop("PROMPT_COMMAND", None)
    line, want = "echo smoke-$((6 * 7))", "smoke-42"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([pys, *args], env=env, capture_output=True, text=True, timeout=30)


def screen(wid: int) -> list[str]:
    r = run("capture", str(wid))
    return [s.rstrip() for s in r.stdout.splitlines()] if r.returncode == 0 else []


def fail(why: str) -> None:
    print(f"smoke: {why}", file=sys.stderr)
    for log in sorted(run_dir.glob("*.log")):
        tail = log.read_text(errors="replace").splitlines()[-20:]
        print(f"--- {log}", *tail, sep="\n", file=sys.stderr)
    sys.exit(1)


print(f"== {pys}, runtime dir {run_dir}, engine {os.environ.get('PYS_TERM', 'the default')}")
# The smoke test starts the keeper itself, so it can end it even when the brain can't start
# (a missing engine): then `pys kill` has nobody to ask.
with open(run_dir / "server.log", "ab") as log:
    keeper = subprocess.Popen([pys, "_keeper"], env=env, stdin=subprocess.DEVNULL,
                              stdout=log, stderr=log)
try:
    # Wait until it serves, or `pys new` finds no keeper and starts one of its own, which
    # this script couldn't end: its socket on POSIX, its pipe key on Windows.
    ready = run_dir / ("pipe.key" if WIN else "keeper.sock")
    deadline = time.monotonic() + 10
    while not ready.exists():
        if keeper.poll() is not None or time.monotonic() > deadline:
            fail(f"the keeper didn't start (exit {keeper.poll()})")
        time.sleep(0.05)
    r = run("new")
    try:
        wid = json.loads(r.stdout)["wid"]
    except (ValueError, KeyError):
        fail(f"pys new failed ({r.returncode}): {r.stdout.strip()} {r.stderr.strip()}")
    print(f"window {wid}")
    deadline = time.monotonic() + 20
    while not any(screen(wid)):                      # the prompt is up
        if time.monotonic() > deadline:
            fail("the shell never showed a prompt")
        time.sleep(0.2)
    run("send", str(wid), line, "--enter")
    while want not in screen(wid):
        if time.monotonic() > deadline + 20:
            fail(f"the window never printed {want}; its screen:\n" + "\n".join(screen(wid)))
        time.sleep(0.2)
    print(run("ls").stdout, end="")
    print("smoke: ok")
finally:
    run("kill")
    try:
        keeper.wait(5)
    except subprocess.TimeoutExpired:
        keeper.kill()
        keeper.wait(5)
    shutil.rmtree(run_dir, ignore_errors=True)
