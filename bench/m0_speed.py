"""M0 speed tests (spec §8.1): py-screen vs GNU screen, same headless terminal.

Both run attached to a 200x50 pty that only drains output (no emulation), so the
numbers are the multiplexer's cost, not the test's. Usage:

    uv run python bench/m0_speed.py --work <dir for the 50 MB log> [--out results.md]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from harness import PYS, Session, Term  # noqa: E402

COLS, ROWS = 200, 50
TICK = os.sysconf("SC_CLK_TCK")
SCREENRC = "startup_message off\ndefscrollback 10000\nhardstatus alwayslastline '%-w%n %t%+w'\n"


def cpu_ticks(pids: list[int]) -> int:
    total = 0
    for pid in pids:
        try:
            f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            total += int(f[11]) + int(f[12])          # utime + stime
        except (OSError, IndexError):
            pass
    return total


def make_log(path: Path, size: int = 50 * 1024 * 1024) -> None:
    if path.exists() and path.stat().st_size >= size:
        return
    levels = [("\x1b[32mINFO \x1b[0m", 6), ("\x1b[33mWARN \x1b[0m", 2), ("\x1b[1;31mERROR\x1b[0m", 1)]
    with path.open("w") as f:
        n, i = 0, 0
        while n < size:
            lv = levels[i % 9 % 3] if i % 9 < 3 else levels[0]
            line = (f"\x1b[2m2026-11-01 01:{i // 60 % 60:02d}:{i % 60:02d}\x1b[0m {lv[0]} "
                    f"\x1b[36mworker-{i % 16}\x1b[0m request {i} handled in "
                    f"\x1b[1m{i % 997} ms\x1b[0m path=/api/v1/items/{i * 7919 % 100000}\n")
            f.write(line)
            n += len(line)
            i += 1


class Mux:
    name = ""

    def start(self) -> None: ...
    def attach(self) -> Term: ...
    def send(self, wid: int, text: str) -> None: ...
    def new_window(self, argv: list[str]) -> None: ...
    def server_pids(self) -> list[int]: ...
    def stop(self) -> None: ...


class PyScreen(Mux):
    name = "py-screen"

    def start(self) -> None:
        self.s = Session("bench")

    def attach(self) -> Term:
        t = Term(PYS + ["attach"], self.s.env, COLS, ROWS, emulate=False)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.s.pys("ls", check=False).strip():
                break
            time.sleep(0.05)
        time.sleep(0.5)
        return t

    def send(self, wid: int, text: str) -> None:
        self.s.pys("send", str(wid), text)

    def new_window(self, argv: list[str]) -> None:
        self.s.pys("new", "--", *argv)

    def server_pids(self) -> list[int]:
        brain = json.loads(self.s.pys("ls", "--json"))["pid"]
        keeper = int(Path(f"/proc/{brain}/stat").read_text().rsplit(")", 1)[1].split()[1])
        return [keeper, brain]

    def stop(self) -> None:
        self.s.kill()


class GnuScreen(Mux):
    name = "screen"

    def __init__(self, rc: Path) -> None:
        self.rc = rc

    def start(self) -> None:
        self.sn = f"pysbench-{uuid.uuid4().hex[:6]}"
        self.env = dict(os.environ, SHELL="/bin/sh", TERM="xterm-256color", PS1="$ ")
        subprocess.run(["screen", "-c", str(self.rc), "-dmS", self.sn, "/bin/sh"],
                       env=self.env, check=True)
        time.sleep(0.3)

    def x(self, *cmd: str) -> None:
        subprocess.run(["screen", "-S", self.sn, "-X", *cmd], env=self.env, check=True)

    def attach(self) -> Term:
        t = Term(["screen", "-r", self.sn], self.env, COLS, ROWS, emulate=False)
        time.sleep(0.8)
        return t

    def send(self, wid: int, text: str) -> None:
        self.x("at", str(wid), "stuff", text)

    def new_window(self, argv: list[str]) -> None:
        self.x("screen", *argv)
        self.x("select", "0")

    def server_pids(self) -> list[int]:
        out = subprocess.run(["screen", "-ls"], capture_output=True, text=True).stdout
        return [int(line.split(".")[0]) for line in out.split() if line.endswith(self.sn)][:1]

    def stop(self) -> None:
        subprocess.run(["screen", "-S", self.sn, "-X", "quit"], capture_output=True)


def run_timed(m: Mux, work: Path, cmd: str, timeout: float = 300) -> tuple[float, float]:
    """Run cmd in window 0 while attached; return (wall seconds, server CPU seconds)."""
    run = uuid.uuid4().hex[:6]
    done = work / f"done-{run}"
    # Only `sh <script>` is typed: screen's `-X stuff` expands $VARS in its argument.
    script = work / f"run-{run}.sh"
    script.write_text(f'S=$(date +%s.%N)\n{cmd}\nE=$(date +%s.%N)\necho "$S $E" > {done}\n')
    t = m.attach()
    pids = m.server_pids()
    c0 = cpu_ticks(pids)
    m.send(0, f"sh {script}\r")
    deadline = time.monotonic() + timeout
    while not done.exists() or not done.read_text().strip():
        if time.monotonic() > deadline:
            raise TimeoutError(cmd)
        time.sleep(0.05)
    s, e = map(float, done.read_text().split())
    cpu = (cpu_ticks(pids) - c0) / TICK
    t.close()
    return e - s, cpu


def latency(m: Mux, flood: bool, n: int = 40) -> list[float]:
    """Type a marker + Enter into `cat`; time until it first comes back on screen."""
    if flood:
        m.new_window(["sh", "-c", "yes 'flood line: the quick brown fox jumps over the lazy dog'"])
        time.sleep(1.0)
    t = m.attach()
    m.send(0, "cat\r")
    time.sleep(0.5)
    samples = []
    for i in range(n):
        marker = f"k{i:03d}q".encode()
        hit: dict = {}

        def on_data(data: bytes, ts: float, marker=marker, hit=hit) -> None:
            buf = hit.get("buf", b"")[-16:] + data
            hit["buf"] = buf
            if "t" not in hit and marker in buf:
                hit["t"] = ts
        with t.lock:
            t.on_data = on_data
        t0 = time.monotonic()
        t.send(marker + b"\r")
        while "t" not in hit and time.monotonic() - t0 < 5:
            time.sleep(0.0005)
        samples.append(hit.get("t", t0 + 5) - t0)
        with t.lock:
            t.on_data = None
        time.sleep(0.05)
    t.close()
    return samples


def idle_cpu(m: Mux, secs: float = 20) -> float:
    for _ in range(9):
        m.new_window(["sh"])
    time.sleep(1)
    t = m.attach()
    pids = m.server_pids() + [t.pid]
    c0 = cpu_ticks(pids)
    time.sleep(secs)
    used = (cpu_ticks(pids) - c0) / TICK
    t.close()
    return used / secs * 100            # percent of one core


def bench(m: Mux, work: Path, log: Path) -> dict:
    r: dict = {}
    for key, fn in [
        ("seq", lambda: run_timed(m, work, "seq 1 5000000")),
        ("cat", lambda: run_timed(m, work, f"cat {log}")),
    ]:
        m.start()
        try:
            r[key] = fn()
        finally:
            m.stop()
        print(f"  {m.name} {key}: {r[key][0]:.2f} s wall, {r[key][1]:.2f} s server CPU", flush=True)
    for key, flood in (("lat_idle", False), ("lat_flood", True)):
        m.start()
        try:
            s = latency(m, flood)
        finally:
            m.stop()
        s.sort()
        r[key] = (statistics.median(s), s[int(len(s) * 0.95) - 1], s[-1])
        print(f"  {m.name} {key}: median {r[key][0] * 1000:.1f} ms, p95 {r[key][1] * 1000:.1f} ms",
              flush=True)
    m.start()
    try:
        r["idle"] = idle_cpu(m)
    finally:
        m.stop()
    print(f"  {m.name} idle: {r['idle']:.2f} % of a core", flush=True)
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    log = a.work / "colored-50MB.log"
    make_log(log)
    rc = a.work / "bench.screenrc"
    rc.write_text(SCREENRC)
    results = {}
    for m in (GnuScreen(rc), PyScreen()):
        print(m.name, flush=True)
        results[m.name] = bench(m, a.work, log)
    sv = subprocess.run(["screen", "-v"], capture_output=True, text=True).stdout.strip()
    sc, py = results["screen"], results["py-screen"]

    def ratio(a: float, b: float) -> str:
        return f"{b / a:.1f}×" if a else "n/a"
    lines = [
        f"# M0 speed: py-screen vs screen ({time.strftime('%Y-%m-%d %H:%M')})", "",
        f"- Machine: {platform.node() and 'local desk'}, {os.cpu_count()} CPUs, Python {platform.python_version()}",
        f"- screen: {sv}, minimal screenrc (status line, 10000 scrollback)",
        f"- Both attached to a headless {COLS}x{ROWS} pty that only drains output", "",
        "| Test | screen | py-screen | py-screen / screen |", "|---|---|---|---|",
        f"| `seq 1 5000000` wall | {sc['seq'][0]:.2f} s | {py['seq'][0]:.2f} s | {ratio(sc['seq'][0], py['seq'][0])} |",
        f"| `seq` server CPU | {sc['seq'][1]:.2f} s | {py['seq'][1]:.2f} s | {ratio(sc['seq'][1], py['seq'][1])} |",
        f"| `cat` 50 MB coloured log, wall | {sc['cat'][0]:.2f} s | {py['cat'][0]:.2f} s | {ratio(sc['cat'][0], py['cat'][0])} |",
        f"| `cat` server CPU | {sc['cat'][1]:.2f} s | {py['cat'][1]:.2f} s | {ratio(sc['cat'][1], py['cat'][1])} |",
        f"| Key → echo, idle (median / p95) | {sc['lat_idle'][0] * 1e3:.1f} / {sc['lat_idle'][1] * 1e3:.1f} ms | {py['lat_idle'][0] * 1e3:.1f} / {py['lat_idle'][1] * 1e3:.1f} ms | |",
        f"| Key → echo, other window floods (median / p95) | {sc['lat_flood'][0] * 1e3:.1f} / {sc['lat_flood'][1] * 1e3:.1f} ms | {py['lat_flood'][0] * 1e3:.1f} / {py['lat_flood'][1] * 1e3:.1f} ms | |",
        f"| Idle CPU, 10 windows, attached | {sc['idle']:.2f} % | {py['idle']:.2f} % | |",
    ]
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text)


if __name__ == "__main__":
    main()
