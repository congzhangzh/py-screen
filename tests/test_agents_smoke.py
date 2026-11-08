"""pys-agents and pys-sessions on Linux, macOS and Windows alike, through the `pys` RPC
commands (no outer terminal, so no pty harness): a stand-in `claude`, typed into a window's
own shell as a person does, is found in its window; a snapshot keeps it; after `pys kill`,
restore opens the window again in its folder, resumes the same session with the flags and
the variables it kept, and leaves the user's shell in the window when the agent ends.
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

WIN = sys.platform == "win32"
PY = [sys.executable, "-m"]

FAKE = r'''
import json, os, sys, time, uuid
home = os.environ["CLAUDE_CONFIG_DIR"]
sid = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else str(uuid.uuid4())
name = sys.argv[sys.argv.index("--name") + 1] if "--name" in sys.argv else None
os.makedirs(os.path.join(home, "projects", "demo"), exist_ok=True)
with open(os.path.join(home, "projects", "demo", sid + ".jsonl"), "a") as f:
    f.write(json.dumps({"type": "assistant", "message": {"model": "claude-test-1", "usage": {
        "input_tokens": 5, "cache_read_input_tokens": 1000}}}) + "\n")
os.makedirs(os.path.join(home, "sessions"), exist_ok=True)
with open(os.path.join(home, "sessions", f"{os.getpid()}.json"), "w") as f:
    json.dump({"pid": os.getpid(), "sessionId": sid, "cwd": os.getcwd(), "name": name,
               "status": "idle"}, f)
print("fake claude", sid, flush=True)
time.sleep(600)
'''


def wait(pred, timeout: float = 30.0, step: float = 0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if v := pred():
            return v
        time.sleep(step)
    raise AssertionError("timed out")


def test_agents_and_sessions_on_any_system(tmp_path: Path) -> None:
    run_dir = Path(tempfile.mkdtemp(prefix="pys-", dir=None if WIN else "/tmp"))
    work, bin_, claude_home = tmp_path / "work", tmp_path / "bin", tmp_path / "claude"
    for d in (work, bin_):
        d.mkdir()
    (bin_ / "fake_claude.py").write_text(FAKE)
    if WIN:                                   # what npm installs: claude.cmd on PATH
        (bin_ / "claude.cmd").write_text(
            f'@"{sys.executable}" "{bin_ / "fake_claude.py"}" %*\r\n')
    else:
        (bin_ / "claude").write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{bin_ / "fake_claude.py"}" "$@"\n')
        (bin_ / "claude").chmod(0o755)
    cfg = tmp_path / "config.py"
    cfg.write_text('SESSIONS_KEEP_ENV = ["MY_VAR"]\n')
    env = dict(os.environ, PYS_DIR=str(run_dir), PYS_CONFIG=str(cfg),
               XDG_STATE_HOME=str(tmp_path / "state"), PYS_PREFIX="C-]",
               CLAUDE_CONFIG_DIR=str(claude_home), PYS_SESSIONS_DIR=str(tmp_path / "snaps"),
               PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", MY_VAR="kept")
    for k in ("PYS_WINDOW", "PYS_SESSION"):
        env.pop(k, None)
    if not WIN:
        env.update(SHELL="/bin/sh", PS1="$ ")
        env.pop("PROMPT_COMMAND", None)

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(list(args), env=env, cwd=work, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        if check and r.returncode:
            log = run_dir / "server.log"
            raise AssertionError(f"{args} -> {r.returncode}\n{r.stdout}\n{r.stderr}\n"
                                 + (log.read_text(errors="replace") if log.exists() else ""))
        return r

    def pys(*a: str, check: bool = True):
        return run(*PY, "py_screen", *a, check=check)

    def agents(*a: str):                      # `pys-agents [where] --json`
        return json.loads(run(*PY, "py_screen.agents.listing", *a, "--json").stdout)

    def sessions(*a: str):
        return run(*PY, "py_screen.agents.sessions", *a)

    def windows() -> list[dict]:
        r = pys("ls", "--json", check=False)
        return json.loads(r.stdout)["windows"] if r.returncode == 0 else []

    def session_files() -> list[dict]:
        out = []
        for f in (claude_home / "sessions").glob("*.json"):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except ValueError:                # being written
                pass
        return out

    from py_screen.agents import procs
    try:
        wid = json.loads(pys("new", "--select").stdout)["wid"]
        wait(lambda: (r := pys("capture", str(wid), check=False)).returncode == 0
             and r.stdout.strip())                                # the prompt is up
        pys("send", str(wid), "claude --name helper --permission-mode plan --secret-flag",
            "--enter")
        first = wait(session_files)[0]
        sid = first["sessionId"]

        rows = agents()
        assert [r["window"] for r in rows] == [wid], rows
        assert rows[0]["name"] == "helper" and rows[0]["model"] == "claude-test-1"
        assert os.path.realpath(rows[0]["cwd"]) == os.path.realpath(work)
        where = [r for r in agents("where") if r["window"] == wid]
        assert where and where[0]["agent"]["session"] == sid, where
        assert os.path.realpath(where[0]["cwd"]) == os.path.realpath(work)

        sessions("save")
        snap = json.loads(next((tmp_path / "snaps").glob("*/windows.json")).read_text(encoding="utf-8"))
        ag = snap["windows"][0]["agent"]
        assert ag["kind"] == "claude" and ag["session"] == sid
        assert ag["env"] == {"MY_VAR": "kept"}

        pys("kill")
        wait(lambda: pys("ls", check=False).returncode != 0)
        wait(lambda: not procs.alive(first["pid"]))
        for f in (claude_home / "sessions").glob("*.json"):
            f.unlink()                                            # Claude Code's, at its end
        env.pop("MY_VAR")                                         # only the snapshot has it

        dry = sessions("restore").stdout
        assert f"claude {sid[:8]}" in dry or sid in dry, dry
        sessions("restore", "--yes")
        again = wait(session_files, 60)[0]
        assert again["sessionId"] == sid and again["name"] == "helper"
        assert os.path.realpath(again["cwd"]) == os.path.realpath(work)
        argv = procs.cmdline(again["pid"])
        assert "--permission-mode" in argv and "plan" in argv, argv
        assert "--secret-flag" not in argv                        # only the flags it knows
        assert procs.environ(again["pid"], ["MY_VAR"]) == {"MY_VAR": "kept"}
        assert [r["window"] for r in agents()] == [w["wid"] for w in windows()]

        # the agent ends: the user's shell is left in the window, in the same folder
        os.kill(again["pid"], 9) if not WIN else subprocess.run(
            ["taskkill", "/F", "/PID", str(again["pid"])], capture_output=True)
        wait(lambda: not procs.alive(again["pid"]))
        time.sleep(1.0)
        [w] = windows()
        probe, want = ("echo left-%OS%", "left-Windows_NT") if WIN else ("echo left-$((6*7))", "left-42")
        pys("send", str(w["wid"]), probe, "--enter")
        wait(lambda: want in pys("capture", str(w["wid"]), check=False).stdout)
        pys("kill")
    finally:
        pys("kill", check=False)
        time.sleep(0.3)
        shutil.rmtree(run_dir, ignore_errors=True)
