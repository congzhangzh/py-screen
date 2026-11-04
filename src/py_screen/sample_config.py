# A sample pys config: copy it to start your own.
#
#     pys sample-config > ~/.config/pys/config.py      # then C-a R (or `pys reload`)
#
# The file is plain Python, run on every brain start. pys passes in the helpers cmd(),
# load() and clock(), and reads WIDGETS, BIND, PREFIX, MOUSE and SCROLLBACK from it.
#
# A status widget is any object with three things, so you can write your own here
# without changing pys itself:
#     .every          seconds pys waits between two updates
#     .text           what the status line shows now (pys sets it from update's result)
#     async update(ctx) -> str    ctx["cwd"] is the focused window's directory
# Keep update() from blocking: await a subprocess (cmd() does), or run a slow read in a
# thread with asyncio.to_thread, so a stuck read can't freeze the screen.
#
# The cpu, mem and net widgets below read Linux's /proc; elsewhere they show nothing.

import asyncio
import time


def proc(path):
    """A /proc file's text, or None where there is no /proc (macOS, Windows)."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


class Rate:
    """How fast a counter grows: keep the last read, divide the change by the time
    between reads. The first update reads twice, 0.25 s apart, so a value shows at once."""

    def __init__(self, every=2.0):
        self.every, self.text, self.last = every, "", None

    def read(self):                    # -> a tuple of counters, or None
        return None

    def show(self, prev, now, dt):     # -> the text for the status line
        return ""

    async def update(self, ctx):
        if self.last is None:
            self.last = (time.monotonic(), await asyncio.to_thread(self.read))
            await asyncio.sleep(0.25)
        (t0, prev), (t1, now) = self.last, (time.monotonic(), await asyncio.to_thread(self.read))
        self.last = (t1, now)
        if prev is None or now is None or t1 <= t0:
            return ""
        return self.show(prev, now, t1 - t0)


class Cpu(Rate):
    """CPU busy share, all cores, over the last `every` seconds."""

    def read(self):                    # /proc/stat: cpu user nice system idle iowait irq softirq steal
        s = proc("/proc/stat")
        if s is None:
            return None
        v = [int(x) for x in s.split("\n", 1)[0].split()[1:9]]
        return sum(v) - v[3] - v[4], sum(v)          # (busy, total) jiffies

    def show(self, prev, now, dt):
        total = now[1] - prev[1]
        return f"cpu{100 * (now[0] - prev[0]) / total:4.0f}%" if total > 0 else ""


class Net(Rate):
    """Network down/up in kB/s (1 kB = 1000 bytes): every interface but lo, or Net("eth0")."""

    def __init__(self, iface=None, every=2.0):
        super().__init__(every)
        self.iface = iface

    def read(self):                    # /proc/net/dev: "eth0: rx_bytes ... (8 fields) tx_bytes ..."
        s = proc("/proc/net/dev")
        if s is None:
            return None
        rx = tx = 0
        for ln in s.splitlines()[2:]:
            name, _, rest = ln.partition(":")
            name = name.strip()
            if name == self.iface or (self.iface is None and name != "lo"):
                v = rest.split()
                rx, tx = rx + int(v[0]), tx + int(v[8])
        return rx, tx

    def show(self, prev, now, dt):
        def kb(a, b):                  # 5 columns, so the line doesn't shift; a reset reads 0
            n = max(0, b - a) / dt / 1000
            return f"{n:5.1f}" if n < 999.95 else f"{n:5.0f}"
        return f"↓{kb(prev[0], now[0])} ↑{kb(prev[1], now[1])} kB/s"


class Mem:
    """RAM in use: 1 - MemAvailable / MemTotal, so the page cache doesn't count as used."""

    def __init__(self, every=5.0):
        self.every, self.text = every, ""

    async def update(self, ctx):
        s = await asyncio.to_thread(proc, "/proc/meminfo")
        if s is None:
            return ""
        kv = {ln.split(":")[0]: int(ln.split()[1]) for ln in s.splitlines() if ":" in ln}
        return f"mem{100 * (1 - kv['MemAvailable'] / kv['MemTotal']):4.0f}%"


class Clock:
    """Like clock(), but update() waits for the next whole second itself and every = 0,
    so the seconds never lag or skip (a plain sleep(1) after each update slowly drifts)."""

    def __init__(self, fmt="%m-%d %H:%M:%S %Z"):
        self.fmt, self.every, self.text = fmt, 0, ""

    async def update(self, ctx):
        await asyncio.sleep(1 - time.time() % 1 + 0.005)
        return time.strftime(self.fmt)


# Shown right to left as room allows: when the line is short, the first ones drop.
WIDGETS = [
    cmd(["git", "-C", "{cwd}", "branch", "--show-current"], every=10),   # focused dir's branch
    Cpu(),
    Mem(),
    Net(),
    load(),                            # 1-minute load average (not on Windows)
    Clock(),                           # e.g. 11-04 14:05:09 UTC
]

BIND = {
    "T": ("top", ["top"]),             # C-a T opens a window running top
}

# PREFIX = "C-a"       # default: C-] inside GNU screen, else C-a
# MOUSE = True         # False: plain drags select text in the terminal again
# SCROLLBACK = 5000    # rows of history per window; 0 = none
