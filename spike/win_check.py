"""M0 Windows check (spec §6, §8.2): do the pieces of the design work on Windows?

Runs on a Windows CI runner: `uv run --with pywinpty python spike/win_check.py`.
Each check prints PASS/FAIL with evidence; the exit code is the number of failures.

1. ConPTY via pywinpty: spawn cmd.exe, type a command, read its output, resize.
2. pyte can rebuild the screen from ConPTY's output (ConPTY re-renders on its own).
3. Named pipes via multiprocessing.connection (the Ipc interface).
4. The keeper/brain split: a keeper process owns the ConPTY and a pipe server; brain 1
   connects, types, leaves; brain 2 connects, gets the replay, types; cmd.exe is the
   same process the whole time.
5. A detached process keeps running after its parent exits (the Daemon interface).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path

PIPE = r"\\.\pipe\pys-wincheck-" + str(os.getpid())
fails = 0


def report(name: str, ok: bool, evidence: str) -> None:
    global fails
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {evidence}", flush=True)


def read_until(proc, needle: str, timeout: float = 15.0) -> str:
    buf = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and needle not in buf:
        try:
            buf += proc.read(4096)
        except EOFError:
            break
    return buf


def check_conpty_and_pyte() -> None:
    import pyte
    from winpty import PtyProcess

    p = PtyProcess.spawn("cmd.exe", dimensions=(30, 100))
    read_until(p, ">")
    p.write("echo hello-%NUMBER_OF_PROCESSORS%-py\r\n")
    out = read_until(p, "-py\r")
    ok = "hello-" in out and "-py" in out
    report("conpty spawn+io", ok, f"pid {p.pid}, {len(out)} chars read")
    p.setwinsize(40, 120)
    report("conpty resize", p.getwinsize() == (40, 120), f"size now {p.getwinsize()}")

    screen = pyte.Screen(120, 40)
    stream = pyte.Stream(screen)
    p.write("echo pyte-sees-this\r\n")
    out2 = read_until(p, "pyte-sees-this\r")
    stream.feed(out + out2)
    text = "\n".join(screen.display)
    report("pyte rebuilds ConPTY output", "pyte-sees-this" in text,
           f"{sum(1 for line in screen.display if line.strip())} non-empty lines")
    p.terminate(force=True)


def check_pipe() -> None:
    with Listener(PIPE + "-a", family="AF_PIPE") as ln:
        def serve() -> None:
            with ln.accept() as c:
                c.send_bytes(c.recv_bytes().upper())
        th = threading.Thread(target=serve)
        th.start()
        with Client(PIPE + "-a", family="AF_PIPE") as c:
            c.send_bytes(b"over a named pipe")
            reply = c.recv_bytes()
        th.join(5)
    report("named pipe rpc", reply == b"OVER A NAMED PIPE", repr(reply))


KEEPER = r'''
import sys, threading, time
from multiprocessing.connection import Listener
from winpty import PtyProcess
pipe = sys.argv[1]
p = PtyProcess.spawn("cmd.exe", dimensions=(30, 100))
ring, lock, conn = [], threading.Lock(), [None]
def pump():
    while True:
        try:
            data = p.read(4096)
        except EOFError:
            return
        with lock:
            ring.append(data)
            if conn[0] is not None:
                try:
                    conn[0].send(("out", data))
                except OSError:
                    conn[0] = None
threading.Thread(target=pump, daemon=True).start()
with Listener(pipe, family="AF_PIPE") as ln:
    for _ in range(2):                      # two brains, one after the other
        c = ln.accept()
        with lock:
            c.send(("hello", p.pid, "".join(ring)))
            conn[0] = c
        while True:
            try:
                kind, data = c.recv()
            except (EOFError, OSError):
                break
            if kind == "keys":
                p.write(data)
        with lock:
            conn[0] = None
p.terminate(force=True)
'''

BRAIN = r'''
import sys, time
from multiprocessing.connection import Client
pipe, word = sys.argv[1], sys.argv[2]
c = Client(pipe, family="AF_PIPE")
_, pid, replay = c.recv()
c.send(("keys", f"echo {word}-done\r\n"))
seen, deadline = "", time.monotonic() + 15
while time.monotonic() < deadline and f"{word}-done\r" not in seen:
    if c.poll(0.2):
        seen += c.recv()[1]
print(pid, len(replay), "first-done" in replay, f"{word}-done" in seen)
c.close()
'''


def check_keeper_brain() -> None:
    d = Path(tempfile.mkdtemp())
    (d / "keeper.py").write_text(KEEPER)
    (d / "brain.py").write_text(BRAIN)
    pipe = PIPE + "-k"
    keeper = subprocess.Popen([sys.executable, str(d / "keeper.py"), pipe])
    time.sleep(3)
    results = []
    for word in ("first", "second"):
        r = subprocess.run([sys.executable, str(d / "brain.py"), pipe, word],
                           capture_output=True, text=True, timeout=60)
        results.append(r.stdout.split())
        print(f"      brain {word}: {r.stdout.strip()} {r.stderr.strip()[-200:]}")
    keeper.wait(30)
    ok = (len(results) == 2 and all(len(x) == 4 for x in results)
          and results[0][0] == results[1][0]            # same cmd.exe pid for both brains
          and results[0][3] == "True" and results[1][3] == "True"
          and results[1][2] == "True")                  # brain 2's replay holds brain 1's work
    report("keeper owns ConPTY across two brains", ok,
           f"cmd pid {results[0][0] if results and results[0] else '?'}; {results}")


def check_detached() -> None:
    flag = Path(tempfile.mkdtemp()) / "alive.txt"
    child = f"import time, pathlib; time.sleep(3); pathlib.Path(r'{flag}').write_text('ok')"
    parent = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}],\n"
        "    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,\n"
        "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )
    subprocess.run([sys.executable, "-c", parent], timeout=30)   # parent exits at once
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not flag.exists():
        time.sleep(0.2)
    report("detached process outlives its parent", flag.exists(), str(flag.exists()))


def main() -> None:
    if sys.platform != "win32":
        sys.exit("win_check.py runs on Windows only")
    print(sys.version, flush=True)
    for check in (check_conpty_and_pyte, check_pipe, check_keeper_brain, check_detached):
        try:
            check()
        except Exception as e:  # report and go on: we want every answer from one run
            report(check.__name__, False, repr(e))
    print(f"{fails} failure(s)")
    sys.exit(fails)


if __name__ == "__main__":
    main()
