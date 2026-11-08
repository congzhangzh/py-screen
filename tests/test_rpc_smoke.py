"""Smoke test of the whole server through the `pys` RPC commands, on Linux, macOS and
Windows alike: no outer terminal is needed, so it doesn't import the pty harness.

It covers every platform interface but the attached terminal: start_detached (the
keeper), serve/connect/alive (both IPC links), spawn_pty (a shell, its output, input,
resize through the screen), proc_cwd (POSIX), a brain reload (keeper replay), kill.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PYS = [sys.executable, "-m", "py_screen"]
WIN = sys.platform == "win32"


def wait(pred, timeout: float = 20.0, step: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if v := pred():
            return v
        time.sleep(step)
    raise AssertionError("timed out")


def test_rpc_smoke(tmp_path: Path) -> None:
    # AF_UNIX paths are short (104 bytes on macOS), and macOS's tmp_path is long.
    run_dir = Path(tempfile.mkdtemp(prefix="pys-", dir=None if WIN else "/tmp"))
    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ, PYS_DIR=str(run_dir), PYS_CONFIG=str(tmp_path / "none.py"),
               XDG_STATE_HOME=str(tmp_path / "state"),
               PYS_PREFIX="C-]")
    for k in ("PYS_WINDOW", "PYS_SESSION"):                      # run from inside a pys window
        env.pop(k, None)
    if not WIN:
        env.update(SHELL="/bin/sh", PS1="$ ")
        env.pop("PROMPT_COMMAND", None)

    def pys(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(PYS + list(args), env=env, cwd=work, capture_output=True,
                           text=True, timeout=30)
        if check and r.returncode:
            raise AssertionError(f"pys {args} -> {r.returncode}\n{r.stdout}\n{r.stderr}\n"
                                 + (run_dir / "server.log").read_text(errors="replace"))
        return r

    def screen(wid: int) -> str:
        r = pys("capture", str(wid), check=False)
        return r.stdout if r.returncode == 0 else ""

    try:
        new = json.loads(pys("new", "--select").stdout)          # starts keeper + brain
        wid = new["wid"]
        assert new["pid"] > 0
        if WIN:
            line, want = "echo hello-%OS%", "hello-Windows_NT"   # typed line != output
        else:
            line, want = "echo hello-$((6*7))", "hello-42"
        wait(lambda: screen(wid).strip())                        # the prompt is up
        pys("send", str(wid), line, "--enter")
        wait(lambda: want in screen(wid))

        ls = json.loads(pys("ls", "--json").stdout)
        assert [w["wid"] for w in ls["windows"]] == [wid]
        # proc_cwd: /proc, libproc, or the PEB (psutil) on Windows
        assert os.path.realpath(ls["windows"][0]["cwd"]) == os.path.realpath(work)

        def win() -> dict:
            return json.loads(pys("ls", "--json").stdout)["windows"][0]

        assert pys("title", "x", check=False).returncode != 0    # not inside a window here
        pys("title", "--wid", str(wid), " engineering ·\tpys\n #1  ")   # one clean line
        assert win()["agent_title"] == win()["name"] == "engineering · pys #1"
        assert pys("title", "--wid", str(wid)).stdout == "engineering · pys #1\n"
        assert pys("title", "--wid", "99", "x", check=False).returncode != 0

        agent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:                                                     # a title with an owner
            pys("title", "--wid", str(wid), "--owner", str(agent.pid), "agent")
            assert win()["agent_title"] == "agent" and win()["agent_owner"] == agent.pid
        finally:
            agent.kill()
            agent.wait()
        wait(lambda: win()["agent_title"] is None, 10)           # went with its owner
        assert win()["agent_owner"] is None
        gone = pys("title", "--wid", str(wid), "--owner", str(agent.pid), "x", check=False)
        assert gone.returncode != 0 and "no process" in gone.stderr
        pys("title", "--wid", str(wid), " engineering ·\tpys\n #1  ")   # no owner: stays

        brain_pid = ls["pid"]
        pys("reload")                                            # a new brain, same shell
        wait(lambda: (r := pys("ls", "--json", check=False)).returncode == 0
             and json.loads(r.stdout)["pid"] != brain_pid)
        assert want in wait(lambda: screen(wid))                 # rebuilt from the ring
        assert json.loads(pys("ls", "--json").stdout)["windows"][0]["pid"] == new["pid"]
        assert win()["agent_title"] == "engineering · pys #1"   # kept across the reload
        if not WIN:                                              # from inside: $PYS_WINDOW
            pys("send", str(wid), f"{sys.executable} -m py_screen title --clear", "--enter")
            wait(lambda: win()["agent_title"] is None)

        missing = pys("new", "--", "no-such-command-pys", check=False)
        if WIN:                                                  # CreateProcess fails
            assert missing.returncode != 0
        pys("ls")                                                # the server is still fine

        pys("kill")
        wait(lambda: pys("ls", check=False).returncode != 0, 10)
    finally:
        pys("kill", check=False)
        time.sleep(0.3)
        shutil.rmtree(run_dir, ignore_errors=True)
