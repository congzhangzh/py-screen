"""What the system says about a process: its children, command line, folder, environment,
the files it holds open, whether it still runs.

Two backends with the same answers: Linux reads /proc directly (fast, and nothing to
install); macOS and Windows ask psutil, a dependency of pys there (pyproject.toml). Every
read tolerates a process that ended between two reads, or one we may not look into: the
answer is then empty, never an exception.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# the pys of this same install: `python -m py_screen` needs no `pys` on PATH and can't pick
# another version's
PYS = [sys.executable, "-m", "py_screen"]

PROC = Path("/proc")
USE_PROC = (PROC / "self" / "stat").exists()     # Linux; elsewhere psutil


def pys(*args: str, check: bool = True) -> str:
    r = subprocess.run([*PYS, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=30)
    if check and r.returncode:
        raise RuntimeError(f"pys {' '.join(args)}: {r.stderr.strip() or r.returncode}")
    return r.stdout


def descendants(pid: int, kids: dict[int, list[int]]) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        for c in sorted(kids.get(p, [])):
            out.append(c)
            todo.append(c)
    return out


def _wanted(env: dict[str, str] | list[str], keys: list[str]) -> dict[str, str]:
    """Only the variables named in `keys` (never the whole environment: it holds secrets)."""
    items = env.items() if isinstance(env, dict) else (e.split("=", 1) for e in env if "=" in e)
    want = set(keys)
    return {k: v for k, v in items if k in want}


if USE_PROC:
    def children_map() -> dict[int, list[int]]:
        kids: dict[int, list[int]] = {}
        for p in PROC.iterdir():
            if not p.name.isdigit():
                continue
            try:
                stat = (p / "stat").read_text()
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            kids.setdefault(ppid, []).append(int(p.name))
        return kids

    def parent(pid: int) -> int | None:
        try:
            return int((PROC / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None

    def cmdline(pid: int) -> list[str]:
        try:
            raw = (PROC / str(pid) / "cmdline").read_bytes()
        except OSError:
            return []
        return [a for a in raw.decode(errors="replace").split("\0") if a]

    def comm(pid: int) -> str:
        try:
            return (PROC / str(pid) / "comm").read_text().strip()
        except OSError:
            return ""

    def cwd(pid: int) -> str | None:
        try:
            return os.readlink(PROC / str(pid) / "cwd")
        except OSError:
            return None

    def environ(pid: int, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        try:
            raw = (PROC / str(pid) / "environ").read_bytes()
        except OSError:
            return {}
        return _wanted(raw.decode(errors="replace").split("\0"), keys)

    def open_files(pid: int) -> list[str]:
        """The regular files it holds open (as psutil gives them: no pipes, sockets,
        devices or deleted files)."""
        d = PROC / str(pid) / "fd"
        try:
            fds = os.listdir(d)
        except OSError:
            return []
        out = []
        for fd in fds:
            try:
                path = os.readlink(d / fd)
            except OSError:
                continue
            if path.startswith("/") and os.path.isfile(path) and path not in out:
                out.append(path)
        return out

    def alive(pid: int) -> bool:
        """Running, not a zombie: `kill(pid, 0)` succeeds on a zombie too, and an orphan
        stays one for good where PID 1 doesn't reap (a container without an init)."""
        try:
            stat = (PROC / str(pid) / "stat").read_text()
        except OSError:
            return False
        try:
            return stat.rsplit(")", 1)[1].split()[0] not in ("Z", "X")
        except IndexError:
            return False

else:
    import psutil

    _GONE = (psutil.Error, OSError)       # NoSuchProcess, AccessDenied, ZombieProcess

    def children_map() -> dict[int, list[int]]:
        kids: dict[int, list[int]] = {}
        for p in psutil.process_iter(["ppid"]):
            ppid = p.info.get("ppid")
            if ppid is not None and ppid != p.pid:      # Windows' System Idle Process: 0 → 0
                kids.setdefault(ppid, []).append(p.pid)
        return kids

    def parent(pid: int) -> int | None:
        try:
            return psutil.Process(pid).ppid()
        except _GONE:
            return None

    def cmdline(pid: int) -> list[str]:
        try:
            return [a for a in psutil.Process(pid).cmdline() if a]
        except _GONE:
            return []

    def comm(pid: int) -> str:
        try:
            name = psutil.Process(pid).name()
        except _GONE:
            return ""
        return name[:-4] if name.lower().endswith(".exe") else name

    def cwd(pid: int) -> str | None:
        try:
            return psutil.Process(pid).cwd() or None
        except _GONE:
            return None

    def environ(pid: int, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        try:
            return _wanted(psutil.Process(pid).environ(), keys)
        except _GONE:
            return {}

    def open_files(pid: int) -> list[str]:
        """The regular files it holds open."""
        try:
            return list(dict.fromkeys(f.path for f in psutil.Process(pid).open_files()))
        except _GONE:
            return []

    def alive(pid: int) -> bool:
        """Running, not a zombie (an exited child its parent has not reaped yet)."""
        try:
            return psutil.Process(pid).status() not in (psutil.STATUS_ZOMBIE,
                                                          psutil.STATUS_DEAD)
        except _GONE:
            return False
