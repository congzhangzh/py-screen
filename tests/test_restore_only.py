"""`pys-sessions restore --only`: by old window number, by group, by name (a word or the start
of a title), across snapshots when no SNAP is given; a token that names nothing stops it all.
Dry runs on made-up snapshots (no server: PYS_DIR points at an empty dir), and one --yes on a
second server of its own."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PY = [sys.executable, "-m", "py_screen.agents.sessions"]


def win(wid, title, group=None, session=None, cwd="/"):
    w = {"wid": wid, "name": "claude" if session else "sh", "agent_title": title, "title": None,
         "group": group, "cwd": cwd, "shell_pid": 1, "foreground": [], "git": None,
         "agent": None}
    if session:
        w["agent"] = {"kind": "claude", "session": session, "cwd": cwd, "argv": ["claude"]}
    return w


def snap(root: Path, name: str, server: int, wins: list[dict]) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "windows.json").write_text(json.dumps({"saved_at": 0, "saved": name, "server_pid": server,
                                                "windows": wins}), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    snaps = tmp_path / "snaps"
    # an earlier server: pys's PO and an avm pair; then the last server: samvada's PO and brahman
    snap(snaps, "20261119-042959", 111, [
        win(53, "po-pys-g1 free", "pys", "aaaa-pys"),
        win(26, "comp-avm-g1 #425 busy", "avm", "aaaa-avm1"),
        win(56, "rel-avm #425 resume busy", "avm", "aaaa-avm2"),
        win(54, "po-brahman-a8 busy", "brahman", "aaaa-br1")])
    snap(snaps, "20261119-160214", 222, [
        win(59, "po-samvada-g2 busy", None, "bbbb-sam"),
        win(54, "po-brahman-a8 busy", "brahman", "aaaa-br1"),
        win(55, "comp-blive wait mac", "brahman", "bbbb-br2"),
        win(7, "", None, None)])                                   # a plain shell
    e = dict(os.environ, PYS_SESSIONS_DIR=str(snaps), PYS_DIR=str(tmp_path / "run"),
             XDG_STATE_HOME=str(tmp_path / "state"), CLAUDE_CONFIG_DIR=str(tmp_path / "claude"))
    for k in ("PYS_WINDOW", "PYS_SESSION"):
        e.pop(k, None)
    return e


def restore(env, *args) -> subprocess.CompletedProcess:
    return subprocess.run(PY + ["restore", *args], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)


def rows(out: str) -> list[str]:
    return [l.split("→ new window")[1].split()[0] for l in out.splitlines() if "→ new window" in l]


def test_by_number_in_one_snapshot(env):
    r = restore(env, "20261119-042959", "--only", "26")
    assert r.returncode == 0 and rows(r.stdout) == ["comp-avm-g1"], r.stdout + r.stderr
    assert "restore from" in r.stdout and "dry run" in r.stdout


def test_by_group_and_several_matches_are_all_listed(env):
    r = restore(env, "20261119-042959", "--only", "avm")
    assert rows(r.stdout) == ["comp-avm-g1", "rel-avm"], r.stdout + r.stderr


def test_by_name_prefix_and_word(env):
    r = restore(env, "--only", "po-sam")                              # the start of a title
    assert rows(r.stdout) == ["po-samvada-g2"], r.stdout + r.stderr
    r = restore(env, "--only", "samvada")                             # a word of it, no group
    assert rows(r.stdout) == ["po-samvada-g2"], r.stdout + r.stderr


def test_a_token_that_names_nothing_opens_nothing(env):
    r = restore(env, "--only", "pys,nosuch", "--yes")
    assert r.returncode != 0 and "'nosuch' names no window" in r.stderr
    assert "opened" not in r.stdout
    r = restore(env, "20261119-042959", "--only", "99")
    assert r.returncode != 0 and "'99' names no window" in r.stderr


def test_across_snapshots_each_from_its_newest(env):
    """pys's PO is only in the earlier server's snapshot, samvada's only in the last one;
    brahman's PO is in both: the newest wins, once."""
    r = restore(env, "--only", "pys,samvada")
    assert rows(r.stdout) == ["po-pys-g1", "po-samvada-g2"], r.stdout + r.stderr
    assert "each from its snapshot" in r.stdout
    assert "from 20261119-042959" in r.stdout and "from 20261119-160214" in r.stdout
    r = restore(env, "--only", "po-brahman,brahman")                  # named twice: once
    assert rows(r.stdout) == ["po-brahman-a8", "comp-blive"], r.stdout + r.stderr


def test_a_running_session_is_skipped(env, tmp_path):
    sessions = tmp_path / "claude" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / f"{os.getpid()}.json").write_text(json.dumps(       # this test process: alive
        {"pid": os.getpid(), "sessionId": "aaaa-pys", "cwd": "/"}), encoding="utf-8")
    r = restore(env, "--only", "pys,samvada")
    assert rows(r.stdout) == ["po-samvada-g2"], r.stdout + r.stderr
    assert "skip win 53 (po-pys-g1 free, from 20261119-042959)" in r.stdout
    assert "aaaa-pys is already running" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="the pty harness is POSIX-only")
def test_yes_opens_only_the_named_window_on_a_second_server(env, tmp_path):
    """A shell window named by its title, from one snapshot: opened with its title and group
    on a server of its own (PYS_DIR), and nothing else."""
    snap(Path(env["PYS_SESSIONS_DIR"]), "20261119-170000", 333, [
        win(4, "notes-shell", "pys", None, cwd=str(tmp_path)),
        win(5, "other-shell", None, None, cwd=str(tmp_path))])
    pys = [sys.executable, "-m", "py_screen"]
    from harness import session_dir
    from stopper import stop_keepers
    env = dict(env, PYS_DIR=str(session_dir("restoreonly")))   # short: AF_UNIX takes 104 bytes,
    try:                                                          # macOS's tmp path is longer
        r = restore(env, "20261119-170000", "--only", "notes", "--yes")
        assert r.returncode == 0 and "opened win" in r.stdout, r.stdout + r.stderr
        ls = json.loads(subprocess.run(pys + ["ls", "--json"], env=env, capture_output=True,
                                       text=True, encoding="utf-8", timeout=15).stdout)
        assert [(w["agent_title"] or w["title"] or "", w["group"]) for w in ls["windows"]] \
            == [("↺notes-shell", "pys")]
    finally:
        subprocess.run(pys + ["kill"], env=env, capture_output=True, timeout=30)   # its own server
        stop_keepers(Path(env["PYS_DIR"]))
        import shutil
        shutil.rmtree(env["PYS_DIR"], ignore_errors=True)


# ---- after review: matching, this server's snapshots, odd input ------------------------
def test_matching_is_the_member_name_or_group_not_status_words(env):
    snaps = Path(env["PYS_SESSIONS_DIR"])
    snap(snaps, "20261119-180000", 444, [win(3, "↺rel-rdp-g1 #439 busy", "rdp", "cccc-rdp"),
                                         win(8, "all-edu-g1 free", "shared", "cccc-edu")])
    r = restore(env, "--only", "rel-rdp-g1")                          # a ↺ title still names it
    assert rows(r.stdout) == ["↺rel-rdp-g1"], r.stdout + r.stderr  # from the newest one
    assert "20261119-180000" in r.stdout, r.stdout + r.stderr
    for loose in ("busy", "free", "g1", "claude"):                    # state words, the program
        r = restore(env, "--only", loose)
        assert r.returncode != 0 and "names no window" in r.stderr, (loose, r.stdout)
    assert rows(restore(env, "--only", "edu").stdout) == ["all-edu-g1"]   # a part of the name


def test_odd_input_is_refused_cleanly(env):
    snaps = Path(env["PYS_SESSIONS_DIR"])
    (snaps / "20261119-190000").mkdir()
    (snaps / "20261119-190000" / "windows.json").write_text("[1, 2]", encoding="utf-8")
    r = restore(env, "--only", "samvada")                             # a broken snapshot: skipped
    assert rows(r.stdout) == ["po-samvada-g2"], r.stdout + r.stderr
    for bad in (",", "²"):
        r = restore(env, "--only", bad)
        assert r.returncode != 0 and "Traceback" not in r.stderr, (bad, r.stderr)


def test_this_servers_snapshots(env, monkeypatch):
    """The dead server's whole group comes first; this server adds only windows that are
    gone (their shell ended) and whose session no longer runs; a newer one of this server
    never hides the dead server's."""
    from py_screen.agents import sessions
    monkeypatch.setenv("PYS_SESSIONS_DIR", env["PYS_SESSIONS_DIR"])
    snaps = Path(env["PYS_SESSIONS_DIR"])
    dead_shell = 2 ** 22 + 12345                                      # no such process
    gone = win(70, "pys-comp-g1 free", "pys", "dddd-gone")
    gone["shell_pid"] = dead_shell
    here = win(71, "pys-rev-g1 free", "pys", "dddd-here")
    here["shell_pid"] = os.getpid()                                   # its window still runs
    snap(snaps, "20261119-200000", 999, [gone, here])                 # this server: 999
    found = sessions.find_named("pys", live=set(), now=999)
    got = [(s.name, w["wid"]) for s, w in found]
    assert got == [("20261119-042959", 53), ("20261119-200000", 70)], got
    found = sessions.find_named("pys", live={"dddd-gone"}, now=999)   # its session runs: not lost
    assert [w["wid"] for _, w in found] == [53]
