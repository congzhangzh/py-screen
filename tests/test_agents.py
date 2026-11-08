"""pys-agents and pys-sessions on a server of their own, with a stand-in `claude`.

The stand-in writes what Claude Code writes (its session file, a transcript with a usage
line and a compaction) and then waits, so the tests need no Claude Code and no account.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from harness import PYS, Session

# pys-agents and pys-sessions read /proc (README: Linux for now); macOS runs this suite too
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="the agent tools read /proc: Linux only")

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time, uuid
home = os.environ["CLAUDE_CONFIG_DIR"]
sid = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else str(uuid.uuid4())
name = sys.argv[sys.argv.index("--name") + 1] if "--name" in sys.argv else None
os.makedirs(f"{home}/projects/demo", exist_ok=True)
with open(f"{home}/projects/demo/{sid}.jsonl", "a") as f:
    f.write(json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n")
    f.write(json.dumps({"type": "assistant", "message": {"model": "claude-test-1", "usage": {
        "input_tokens": 5, "cache_read_input_tokens": 120000,
        "cache_creation_input_tokens": 1000, "output_tokens": 50}}}) + "\n")
os.makedirs(f"{home}/sessions", exist_ok=True)
with open(f"{home}/sessions/{os.getpid()}.json", "w") as f:
    json.dump({"pid": os.getpid(), "sessionId": sid, "cwd": os.getcwd(), "name": name,
               "status": "idle", "entrypoint": "cli"}, f)
print("fake claude", sid, flush=True)
time.sleep(600)
'''


def wait(cond, timeout: float = 10.0):
    end = time.time() + timeout
    while time.time() < end:
        if v := cond():
            return v
        time.sleep(0.1)
    raise AssertionError("timed out")


def setup(tmp_path: Path, tag: str, config: str = "") -> Session:
    sess = Session(tag, shell="/bin/bash")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "claude").write_text(FAKE)
    (bin_ / "claude").chmod(0o755)
    cfg = tmp_path / "config.py"
    cfg.write_text(config)
    sess.env.update(PATH=f"{bin_}:{sess.env['PATH']}", CLAUDE_CONFIG_DIR=str(tmp_path / "claude"),
                    PYS_SESSIONS_DIR=str(tmp_path / "snaps"), PYS_CONFIG=str(cfg),
                    HOME=str(tmp_path))
    return sess


def run(sess: Session, module: str, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, "-m", f"py_screen.agents.{module}", *args],
                       env=sess.env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r


def start_claude(sess: Session, cwd: Path, *flags: str) -> dict:
    work = cwd
    work.mkdir(exist_ok=True)
    before = {w["wid"] for w in json.loads(sess.pys("ls", "--json"))["windows"]} \
        if (sess.dir / "keeper.sock").exists() else set()
    subprocess.run(PYS + ["new", "bash", "-c", f"MY_VAR=kept claude {' '.join(flags)}"],
                   env=sess.env, cwd=work, check=True, capture_output=True, timeout=15)
    win = wait(lambda: [w for w in json.loads(sess.pys("ls", "--json"))["windows"]
                        if w["wid"] not in before])[0]
    wait(lambda: list(Path(sess.env["CLAUDE_CONFIG_DIR"], "sessions").glob("*.json")))
    return win


def test_agents_finds_the_claude_in_its_window(tmp_path: Path) -> None:
    sess = setup(tmp_path, "ag")
    try:
        win = start_claude(sess, tmp_path / "work", "--name", "helper")
        rows = json.loads(run(sess, "listing", "--json").stdout)
        assert len(rows) == 1
        r = rows[0]
        assert r["window"] == win["wid"]
        assert r["name"] == "helper" and r["state"] == "idle"
        assert r["context_tokens"] == 121005 and r["model"] == "claude-test-1"
        assert r["compactions"] == 1
        assert r["cwd"] == str((tmp_path / "work").resolve())
        table = run(sess, "listing").stdout
        assert "121k" in table and "helper" in table and "test-1" in table
    finally:
        sess.kill()


def test_save_then_restore_brings_the_session_back(tmp_path: Path) -> None:
    sess = setup(tmp_path, "rs", 'SESSIONS_KEEP_ENV = ["MY_VAR"]\n')
    try:
        win = start_claude(sess, tmp_path / "work", "--name", "helper",
                           "--permission-mode", "plan", "--secret-flag")
        sess.pys("title", "--wid", str(win["wid"]), "builder")
        run(sess, "sessions", "save")
        snap = json.loads(next((tmp_path / "snaps").glob("*/windows.json")).read_text())
        w = [x for x in snap["windows"] if x["wid"] == win["wid"]][0]
        ag = w["agent"]
        assert ag["kind"] == "claude" and ag["name"] == "helper"
        assert ag["env"] == {"MY_VAR": "kept"}               # only the variables asked for
        sid = ag["session"]
        assert "unchanged" in run(sess, "sessions", "save").stdout

        sess.pys("kill", check=False)
        wait(lambda: not (sess.dir / "keeper.sock").exists())
        for f in Path(sess.env["CLAUDE_CONFIG_DIR"], "sessions").glob("*.json"):
            f.unlink()                                       # Claude Code's, when it ends
        # no server now: restore starts one with its first window
        dry = run(sess, "sessions", "restore").stdout
        assert f"claude {sid}" in dry and "dry run" in dry
        run(sess, "sessions", "restore", "--yes")
        names = wait(lambda: [x["name"] for x in json.loads(sess.pys("ls", "--json"))["windows"]
                              if x["name"].startswith("↺")])
        assert names == ["↺builder"]
        resumed = wait(lambda: [json.loads(f.read_text()) for f in
                                Path(sess.env["CLAUDE_CONFIG_DIR"], "sessions").glob("*.json")])
        assert resumed[0]["sessionId"] == sid and resumed[0]["name"] == "helper"
        pid = resumed[0]["pid"]
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
        assert "--permission-mode" in argv and "plan" in argv
        assert "--secret-flag" not in argv                   # only the flags it knows
        env = Path(f"/proc/{pid}/environ").read_bytes().decode().split("\0")
        assert "MY_VAR=kept" in env
    finally:
        sess.kill()


def test_forensic_takes_folders_and_titles_from_the_server_record(tmp_path: Path) -> None:
    sess = setup(tmp_path, "fo")
    try:
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(PYS + ["new", "sleep", "600"], env=sess.env, cwd=work, check=True,
                       capture_output=True, timeout=15)
        ls = json.loads(sess.pys("ls", "--json"))
        sess.pys("title", "--wid", str(ls["windows"][0]["wid"]), "sleeper")
        records = sess.dir / "state" / "pys" / "servers"
        rec = wait(lambda: [f for f in records.glob("*.json") if "sleeper" in f.read_text()])[0]
        # a crash, not a kill: no hook runs, no snapshot is taken. The record's pid is the
        # keeper's (`ls --json` gives the brain's, which the keeper would just restart)
        keeper = json.loads(rec.read_text())["pid"]
        for pid in [w["pid"] for w in ls["windows"]] + [keeper, ls["pid"]]:
            os.kill(pid, signal.SIGKILL)
        wait(lambda: subprocess.run(PYS + ["ls"], env=sess.env, capture_output=True).returncode)
        subprocess.run(PYS + ["new"], env=sess.env, check=True, capture_output=True, timeout=15)
        out = run(sess, "sessions", "forensic", "--write").stdout
        assert "sleeper" in out and str(work.resolve()) in out
        assert "from the server record" in out
        snap = json.loads(next((tmp_path / "snaps").glob("*-forensic/windows.json")).read_text())
        assert snap["record"] and snap["windows"][0]["cwd"] == str(work.resolve())
    finally:
        sess.kill()


def test_skills_install_links_and_never_replaces(tmp_path: Path) -> None:
    from py_screen.agents import listing
    dest = tmp_path / "skills"
    (dest / "pys-restore").mkdir(parents=True)              # the user's own version
    assert listing.main(["skills", "--install", "--dir", str(dest)]) == 0
    assert (dest / "pys-drive").is_symlink()
    assert (dest / "pys-drive" / "SKILL.md").read_text().startswith("---\nname: pys-drive")
    assert not (dest / "pys-restore").is_symlink()
    assert listing.main(["skills", "--install", "--dir", str(dest)]) == 0   # twice is fine


def test_a_zombie_is_not_alive() -> None:
    """A dead claude nobody reaped (PID 1 without an init, in a container) is not a running
    session: restore must bring it back, not skip it as `already running`."""
    from py_screen.agents import procs
    p = subprocess.Popen(["true"])
    wait(lambda: Path(f"/proc/{p.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z")
    try:
        assert not procs.alive(p.pid)
        assert procs.alive(os.getpid())
    finally:
        p.wait()
