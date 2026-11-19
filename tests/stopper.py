"""Stop a test's pys server for sure, on every system: `pys kill` first (by the caller), then
its keeper by the pid it wrote (keeper.pid in its runtime dir) or its server record's name,
when that pid is still a pys keeper: SIGTERM, then SIGKILL after 5 s. Without it, a test whose
brain crashed (so `pys kill` reached nothing) left its keeper running after its dir was gone,
restarting brains into an unlinked log (2026-11-19: about 60 of them filled /run/user)."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


def keeper_runs(pid: int) -> bool:
    """pid is alive and a pys keeper (not a number reused by another process)."""
    try:
        import psutil
        try:
            cmd = " ".join(psutil.Process(pid).cmdline())
        except (psutil.Error, OSError):
            return False
    except ImportError:
        try:
            cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
    return "py_screen" in cmd and "_keeper" in cmd


def keeper_pids(run_dir: Path, state_dir: Path | None = None) -> set[int]:
    pids = set()
    try:
        pids.add(int((run_dir / "keeper.pid").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    for rec in ((state_dir / "pys" / "servers").glob("*-*.json") if state_dir else ()):
        try:
            pids.add(int(rec.stem.rsplit("-", 1)[1]))
        except ValueError:
            pass
    return pids


def stop_keepers(run_dir: Path, state_dir: Path | None = None) -> list[int]:
    """Stop every keeper this test server left; returns the pids it had to stop."""
    stopped = []
    for pid in keeper_pids(run_dir, state_dir):
        if not keeper_runs(pid):
            continue
        stopped.append(pid)
        for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
            try:
                os.kill(pid, sig)
            except OSError:
                break
            for _ in range(50):
                if not keeper_runs(pid):
                    break
                time.sleep(0.1)
            else:
                continue
            break
    return stopped
