"""What /proc says about a process: its children, command line, folder, environment.

Linux only. Every read tolerates a process that ended between two reads.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# the pys of this same install: `python -m py_screen` needs no `pys` on PATH and can't pick
# another version's
PYS = [sys.executable, "-m", "py_screen"]


def pys(*args: str, check: bool = True) -> str:
    r = subprocess.run([*PYS, *args], capture_output=True, text=True, timeout=30)
    if check and r.returncode:
        raise RuntimeError(f"pys {' '.join(args)}: {r.stderr.strip() or r.returncode}")
    return r.stdout


def children_map() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(p.name))
    return kids


def descendants(pid: int, kids: dict[int, list[int]]) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        for c in sorted(kids.get(p, [])):
            out.append(c)
            todo.append(c)
    return out


def cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a for a in raw.decode(errors="replace").split("\0") if a]


def comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def environ(pid: int, keys: list[str]) -> dict[str, str]:
    """Only the variables named in `keys` (never the whole environment: it holds secrets)."""
    if not keys:
        return {}
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return {}
    want = tuple(f"{k}=" for k in keys)
    return dict(e.split("=", 1) for e in env if e.startswith(want))


def alive(pid: int) -> bool:
    """Running, not a zombie: `kill(pid, 0)` succeeds on a zombie too, and an orphan stays
    one for good where PID 1 doesn't reap (a container without an init)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    try:
        return stat.rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except IndexError:
        return False
