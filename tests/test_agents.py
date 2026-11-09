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

# the pty harness is POSIX-only; tests/test_agents_smoke.py runs on Windows too
pytestmark = pytest.mark.skipif(os.name != "posix", reason="the pty harness is POSIX-only")

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
if t := os.environ.get("FAKE_TITLE"):          # an agent that names its window at once
    import subprocess
    subprocess.run([os.environ["PYS_PY"], "-m", "py_screen", "title", "--owner",
                    str(os.getpid()), t], check=False)
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
        from py_screen.agents import procs
        argv = procs.cmdline(pid)
        assert "--permission-mode" in argv and "plan" in argv
        assert "--secret-flag" not in argv                   # only the flags it knows
        assert procs.environ(pid, ["MY_VAR"]) == {"MY_VAR": "kept"}
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
    os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOWAIT)   # it ended; not reaped: a zombie
    try:
        assert not procs.alive(p.pid)
        assert procs.alive(os.getpid())
    finally:
        p.wait()


def make_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repo on `main` with one commit, and a linked worktree of it on branch `topic`."""
    repo, wt = tmp_path / "repo", tmp_path / "repo-topic"
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*g, "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a.txt").write_text("a\n")
    subprocess.run([*g, "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run([*g, "-C", str(repo), "commit", "-q", "-m", "one"], check=True)
    subprocess.run([*g, "-C", str(repo), "worktree", "add", "-q", "-b", "topic", str(wt)], check=True)
    return repo, wt


def open_in(sess: Session, folder: Path) -> int:
    subprocess.run(PYS + ["new", "sleep", "600"], env=sess.env, cwd=folder, check=True,
                   capture_output=True, timeout=15)
    return max(w["wid"] for w in json.loads(sess.pys("ls", "--json"))["windows"])


def test_where_shows_branch_worktree_and_changes(tmp_path: Path) -> None:
    sess = setup(tmp_path, "wh")
    repo, wt = make_repo(tmp_path)
    (wt / "a.txt").write_text("changed\n")
    (wt / "new.txt").write_text("new\n")
    try:
        a, b = open_in(sess, repo), open_in(sess, wt)
        rows = {r["window"]: r for r in json.loads(run(sess, "listing", "where", "--json").stdout)}
        ga, gb = rows[a]["git"], rows[b]["git"]
        assert ga["branch"] == "main" and not ga["worktree"] and ga["changed"] == 0
        assert gb["branch"] == "topic" and gb["worktree"] and gb["repo"] == "repo"
        assert gb["changed"] == 1 and gb["untracked"] == 1 and gb["head"] == ga["head"]
        table = run(sess, "listing", "where").stdout
        assert "topic @" in table and "(wt)" in table and "1 mod 1 new" in table
    finally:
        sess.kill()


def test_restore_says_which_branch_and_when_its_worktree_is_gone(tmp_path: Path) -> None:
    sess = setup(tmp_path, "wg")
    repo, wt = make_repo(tmp_path)
    try:
        b = open_in(sess, wt)
        run(sess, "sessions", "save")
        snap = json.loads(next((tmp_path / "snaps").glob("*/windows.json")).read_text())
        g = [w for w in snap["windows"] if w["wid"] == b][0]["git"]
        assert g["branch"] == "topic" and g["worktree"]
        sess.pys("kill", check=False)
        wait(lambda: not (sess.dir / "keeper.sock").exists())
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], check=True)
        dry = run(sess, "sessions", "restore").stdout
        assert "worktree of repo, branch topic @" in dry
        assert f"folder gone: {wt}" in dry and "worktree add" in dry
        # the hint works as printed: the folder comes back on its branch
        hint = dry.split("to bring it back: ", 1)[1].splitlines()[0]
        subprocess.run(hint, shell=True, check=True, capture_output=True)
        assert (wt / "a.txt").exists()
    finally:
        sess.kill()


def test_where_follows_the_agents_writes_not_the_folder_it_started_in(tmp_path: Path) -> None:
    """A claude started in the main checkout that edits files in a worktree works in the
    worktree: its transcript says so; its process folder never changes."""
    sess = setup(tmp_path, "wi")
    repo, wt = make_repo(tmp_path)
    try:
        win = start_claude(sess, repo, "--name", "helper")
        s = json.loads(next(Path(sess.env["CLAUDE_CONFIG_DIR"], "sessions").glob("*.json")).read_text())
        t = Path(sess.env["CLAUDE_CONFIG_DIR"], "projects", "demo", f"{s['sessionId']}.jsonl")
        use = lambda name, inp: json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": name, "input": inp}]}}) + "\n"
        with t.open("a") as f:
            f.write(use("Bash", {"command": f"cd {tmp_path} && ls"}))         # no repo: skipped
            f.write(use("Edit", {"file_path": str(wt / "a.txt")}))
            f.write(use("Read", {"file_path": str(repo / "a.txt")}))           # a read is no work
        r = [x for x in json.loads(run(sess, "listing", "where", "--json").stdout)
             if x["window"] == win["wid"]][0]
        assert r["cwd"] == str(repo.resolve())
        assert r["works_in"] == str(wt.resolve()) and r["git"]["branch"] == "topic"
        assert "✎" in run(sess, "listing", "where").stdout
    finally:
        sess.kill()


def test_a_snapshot_keeps_where_the_agent_worked(tmp_path: Path) -> None:
    sess = setup(tmp_path, "sw")
    repo, wt = make_repo(tmp_path)
    try:
        win = start_claude(sess, repo, "--name", "helper")
        s = json.loads(next(Path(sess.env["CLAUDE_CONFIG_DIR"], "sessions").glob("*.json")).read_text())
        t = Path(sess.env["CLAUDE_CONFIG_DIR"], "projects", "demo", f"{s['sessionId']}.jsonl")
        with t.open("a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use",
                    "name": "Write", "input": {"file_path": str(wt / "b.txt")}}]}}) + "\n")
        run(sess, "sessions", "save")
        snap = json.loads(next((tmp_path / "snaps").glob("*/windows.json")).read_text())
        w = [x for x in snap["windows"] if x["wid"] == win["wid"]][0]
        assert w["works_in"] == str(wt.resolve()) and w["git"]["branch"] == "topic"
        assert w["agent"]["cwd"] == str(repo.resolve())      # it resumes where it started
        sess.pys("kill", check=False)
        wait(lambda: not (sess.dir / "keeper.sock").exists())
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], check=True)
        dry = run(sess, "sessions", "restore").stdout
        assert f"folder gone: {wt} (where it worked)" in dry and "worktree add" in dry
    finally:
        sess.kill()


def test_branch_widget_follows_the_agent(tmp_path: Path, monkeypatch) -> None:
    from py_screen.agents import widget
    sess = setup(tmp_path, "bw")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", sess.env["CLAUDE_CONFIG_DIR"])
    repo, wt = make_repo(tmp_path)
    try:
        assert widget.describe({"cwd": str(repo)}) == "main"
        (repo / "a.txt").write_text("changed\n")
        assert widget.describe({"cwd": str(repo)}) == "main 1 mod"
        assert widget.describe({"cwd": str(tmp_path)}) == ""            # no repo: nothing
        win = start_claude(sess, repo, "--name", "helper")
        s = json.loads(next(Path(sess.env["CLAUDE_CONFIG_DIR"], "sessions").glob("*.json")).read_text())
        t = Path(sess.env["CLAUDE_CONFIG_DIR"], "projects", "demo", f"{s['sessionId']}.jsonl")
        with t.open("a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use",
                    "name": "Edit", "input": {"file_path": str(wt / "a.txt")}}]}}) + "\n")
        # the window's shell sits in repo (main); the agent in it works in the worktree
        assert widget.describe({"cwd": str(repo), "pid": win["pid"]}) == "✎topic wt"
    finally:
        sess.kill()


def test_a_widget_gets_the_focused_windows_wid_and_pid(tmp_path: Path) -> None:
    out = tmp_path / "ctx.json"
    sess = setup(tmp_path, "cx", f"""import json
class Probe:
    every, text = 0.3, ""
    async def update(self, ctx):
        import os                                   # whole or not at all: the test reads
        open({str(out) + ".tmp"!r}, "w").write(json.dumps(ctx))   # it while it is written
        os.replace({str(out) + ".tmp"!r}, {str(out)!r})
        return ""
WIDGETS = [Probe()]
""")
    try:
        subprocess.run(PYS + ["new", "sleep", "600"], env=sess.env, cwd=tmp_path, check=True,
                       capture_output=True, timeout=15)
        w = json.loads(sess.pys("ls", "--json"))["windows"][0]
        ctx = wait(lambda: out.exists() and (d := json.loads(out.read_text())).get("pid") and d)
        assert ctx == {"cwd": str(tmp_path.resolve()), "wid": w["wid"], "pid": w["pid"]}
    finally:
        sess.kill()


def test_restore_leaves_an_agents_own_title(tmp_path: Path) -> None:
    sess = setup(tmp_path, "rt")
    sess.env.update(FAKE_TITLE="own title", PYS_PY=sys.executable)
    try:
        win = start_claude(sess, tmp_path / "work", "--name", "helper")
        wait(lambda: [x for x in json.loads(sess.pys("ls", "--json"))["windows"]
                      if x["name"] == "own title"])
        run(sess, "sessions", "save")
        sess.pys("kill", check=False)
        wait(lambda: not (sess.dir / "keeper.sock").exists())
        run(sess, "sessions", "restore", "--yes")
        time.sleep(1.5)                     # both writers are done by now, in either order
        names = [x["name"] for x in json.loads(sess.pys("ls", "--json"))["windows"]]
        assert names == ["own title"]
    finally:
        sess.kill()


def test_caption_names_where_the_agent_works(tmp_path: Path, monkeypatch) -> None:
    from py_screen.agents import widget
    sess = setup(tmp_path, "cp")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", sess.env["CLAUDE_CONFIG_DIR"])
    monkeypatch.setenv("HOME", str(tmp_path))                 # paths show as ~/…
    repo, wt = make_repo(tmp_path)
    try:
        assert widget.caption_text({"cwd": str(tmp_path / "bin")}) == "~/bin"   # no repo
        assert widget.caption_text({"cwd": str(repo)}) == "~/repo  main"
        win = start_claude(sess, repo, "--name", "helper")
        s = json.loads(next(Path(sess.env["CLAUDE_CONFIG_DIR"], "sessions").glob("*.json")).read_text())
        t = Path(sess.env["CLAUDE_CONFIG_DIR"], "projects", "demo", f"{s['sessionId']}.jsonl")
        with t.open("a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use",
                    "name": "Edit", "input": {"file_path": str(wt / "a.txt")}}]}}) + "\n")
        text = widget.caption_text({"cwd": str(repo), "pid": win["pid"]})
        # where it started first (its Bash goes back there), then where it works, both in
        # the same form (here HOME is tmp_path: ~/repo, ~/repo-topic)
        assert text == "~/repo ✎ ~/repo-topic  topic wt"
    finally:
        sess.kill()


def test_the_psutil_backend_answers_as_proc_does(tmp_path: Path) -> None:
    """macOS and Windows read processes through psutil: on Linux, where both backends work,
    they must give the same answers (the module, loaded once more with /proc switched off)."""
    if not sys.platform.startswith("linux"):
        pytest.skip("needs /proc to compare with")
    import inspect
    import types
    from py_screen.agents import procs
    src = inspect.getsource(procs).replace(
        'USE_PROC = (PROC / "self" / "stat").exists()', "USE_PROC = False")
    ps = types.ModuleType("procs_psutil")
    exec(compile(src, "procs_psutil", "exec"), ps.__dict__)
    assert not ps.USE_PROC and procs.USE_PROC
    # The child says when it has started: until then the dynamic loader may hold libc.so.6
    # open for an instant, and two reads one after the other would see two moments (CI)
    child = subprocess.Popen([sys.executable, "-c", "import time; print('ready', flush=True); "
                              "time.sleep(30)"], cwd=tmp_path, env=dict(os.environ, MY_VAR="kept"),
                             stdout=subprocess.PIPE, text=True)
    try:
        me = os.getpid()
        assert child.stdout.readline() == "ready\n"
        for f in ("cmdline", "comm", "cwd", "alive", "open_files"):
            assert getattr(ps, f)(child.pid) == getattr(procs, f)(child.pid), f
        assert ps.environ(child.pid, ["MY_VAR"]) == procs.environ(child.pid, ["MY_VAR"]) \
            == {"MY_VAR": "kept"}
        assert child.pid in ps.children_map()[me] and child.pid in procs.children_map()[me]
        assert child.pid in ps.descendants(me, ps.children_map())
    finally:
        child.kill()
        child.wait()
    assert not ps.alive(child.pid) and not procs.alive(child.pid)


def test_a_restored_windows_command_keeps_only_known_flags() -> None:
    """The launcher builds the resume from the snapshot, as argv (no shell quoting), the
    same on every system; a Windows Node claude's script path is not taken for a flag."""
    from py_screen.agents import reopen
    w = {"wid": 3, "agent": {"kind": "claude", "session": "abc", "argv": [
        r"C:\nodejs\node.exe", r"C:\npm\node_modules\cli.js", "--model", "opus",
        "--secret", "--dangerously-skip-permissions", "--name=it's mine"]}}
    assert reopen.agent_argv(w) == ["claude", "--model", "opus",
                                    "--dangerously-skip-permissions", "--name=it's mine",
                                    "--resume", "abc"]
    assert reopen.agent_argv({"wid": 1}) is None
    assert reopen.notes({"wid": 1, "foreground": ["vim", "a b"]}, Path("/s/20261108-1"))[1] \
        == "it was running: vim 'a b'"


def test_skills_install_renews_a_link_to_a_pys_install_that_is_gone(tmp_path: Path, capsys) -> None:
    """A link into another pys install's skills is pys's own, not the user's: renewed when
    that install is gone, named (not called "not ours") while it is there."""
    from py_screen.agents import listing
    dest = tmp_path / "skills"
    dest.mkdir()
    gone = tmp_path / "old-venv" / "py_screen" / "agents" / "skills" / "pys-drive"
    (dest / "pys-drive").symlink_to(gone)                     # uninstalled since
    live = tmp_path / "other" / "py_screen" / "agents" / "skills" / "pys-restore"
    live.mkdir(parents=True)
    (dest / "pys-restore").symlink_to(live)                   # another install, still there
    assert listing.main(["skills", "--install", "--dir", str(dest)]) == 0
    out = capsys.readouterr().out
    assert (dest / "pys-drive" / "SKILL.md").exists() and "relinked" in out
    assert os.path.realpath(dest / "pys-restore") == os.path.realpath(live)
    assert "linked to another pys install" in out and "not ours" not in out
