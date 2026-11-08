"""The first process of a window `pys-sessions restore` opens: go to the window's folder, say
where it comes from, run its agent again (`claude <its flags> --resume <id>`), then the
user's own shell in that folder.

    python -m py_screen.agents.reopen <snapshot dir> <old window number>

It takes the window from the snapshot itself, so nothing passes through a shell's quoting,
and it works the same on every system: POSIX hands over to `sh -c '"$@"; exec "$0" -i'`
(the agent, then the shell pys gives a new window, in the same process); Windows, which
has no exec, runs the agent and then that shell as its children and ends with the shell.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .. import platform

HOME = Path.home()

# flags of the original claude that a resume keeps (value-taking ones take the next word)
KEEP_VAL = {"--name", "-n", "--permission-mode", "--model", "--add-dir", "--agent", "--settings",
            "--mcp-config", "--allowedTools", "--allowed-tools", "--disallowedTools",
            "--disallowed-tools", "--effort", "--fallback-model"}
KEEP_BOOL = {"--dangerously-skip-permissions", "--chrome", "--ide", "--verbose"}


def keep_flags(argv: list[str]) -> list[str]:
    """The flags a resume keeps, from a claude's whole command line. Its first word is the
    program; on Windows a Node claude also shows its script (`node.exe …\\cli.js …`), which
    is not a flag and is skipped like any other word that isn't one we keep."""
    out, i = [], 1
    while i < len(argv):
        a = argv[i]
        key = a.split("=", 1)[0]
        if key in KEEP_BOOL:
            out.append(a)
        elif key in KEEP_VAL:
            if "=" in a:
                out.append(a)
            elif i + 1 < len(argv):
                out += [a, argv[i + 1]]
                i += 1
        i += 1
    return out


def agent_argv(w: dict) -> list[str] | None:
    """What runs the window's agent again, or None for a plain shell."""
    ag = w.get("agent")
    if not ag:
        return None
    if ag["kind"] == "claude":
        return ["claude", *keep_flags(ag.get("argv") or ["claude"]), "--resume", ag["session"]]
    if ag["kind"] == "codex":
        return ["codex", "resume", ag["session"]]
    return None


def notes(w: dict, snapdir: Path) -> list[str]:
    """What the window says first: where it comes from, and what ran in it."""
    out = [f"restored by pys-sessions from {snapdir.name}, window {w['wid']}"]
    if w.get("screen"):
        out[0] += f"; its last screen (gzip): {snapdir / w['screen']}"
    if not w.get("agent"):
        if e := w.get("ended_agent"):
            out.append(f"{e['kind']} {e['session']} ran here and ended; to go on: "
                       f"claude --resume {e['session']}")
        elif fg := w.get("foreground"):
            out.append("it was running: " + shlex.join(fg))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    snapdir, wid = Path(argv[0]), int(argv[1])
    snap = json.loads((snapdir / "windows.json").read_text(encoding="utf-8"))
    w = next(x for x in snap["windows"] if x["wid"] == wid)
    cwd = w.get("cwd") or str(HOME)
    try:
        os.chdir(cwd)
    except OSError:
        print(f"folder gone: {cwd}; opening in {HOME}")
        os.chdir(HOME)
    for line in notes(w, snapdir):
        print(line)
    sys.stdout.flush()
    cmd = agent_argv(w)
    extra = (w.get("agent") or {}).get("env") or {}   # SESSIONS_KEEP_ENV: for the agent only
    shell = platform.default_shell()
    if os.name == "posix":
        if cmd:
            if extra:
                cmd = ["env", *(f"{k}={v}" for k, v in extra.items()), *cmd]
            os.execvp("sh", ["sh", "-c", '"$@"; exec "$0" -i', shell, *cmd])
        os.execvp(shell, [shell, "-i"])
    # Windows: Ctrl-C reaches every process on the console; it is the agent's, not ours
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if cmd:
        exe = shutil.which(cmd[0])                      # claude.exe, or npm's claude.cmd
        if exe is None:
            print(f"{cmd[0]}: not found on PATH; to go on by hand: {subprocess.list2cmdline(cmd)}")
        else:
            subprocess.run([exe, *cmd[1:]], env=dict(os.environ, **extra))
    return subprocess.run([shell]).returncode


if __name__ == "__main__":
    sys.exit(main())
