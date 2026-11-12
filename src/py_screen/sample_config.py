# A sample pys config: copy it to start your own.
#
#     pys doctor --init      # writes this file there (never over yours); then C-a R
#     pys doctor             # where it is, and a check: each widget runs once, typos found
#
# The file is plain Python, run on every brain start. pys passes in the helpers cmd(),
# load() and clock(), and reads WIDGETS, BIND, PREFIX, MOUSE, SCROLLBACK, SHOW_WIDGETS,
# STATUS_LINES, ON_CHANGE, KILL_HOOK_TIMEOUT, SESSIONS_KEEP_ENV and CAPTION from it.
#
# A status widget is any object with three things, so you can write your own here
# without changing pys itself:
#     .every          seconds pys waits between two updates
#     .text           what the status line shows now (pys sets it from update's result)
#     async update(ctx) -> str    ctx["cwd"] is the focused window's directory
# Keep update() from blocking: await a subprocess (cmd() does), or run a slow read in a
# thread with asyncio.to_thread, so a stuck read can't freeze the screen.
#
# The cpu, mem, net and disk widgets read the kernel's /proc on Linux, which needs nothing
# extra. On macOS and Windows they use psutil, which pys installs there (it shows nothing
# if psutil is missing all the same). The space widget needs neither: the space comes from
# the standard library everywhere.

import asyncio
import os
import re
import shutil
import subprocess
import time

try:
    import psutil
except ImportError:
    psutil = None

PROC = os.path.exists("/proc/stat")    # Linux: read the kernel's counters directly


def proc(path):
    """A /proc file's text, or None if it can't be read."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def loopback(name):                    # lo (Linux), lo0 (macOS), "Loopback Pseudo-Interface 1"
    return name in ("lo", "lo0") or "loopback" in name.lower()


def kb(a, b, dt):
    """kB/s (1 kB = 1000 bytes) between two byte counts, 5 columns wide so the line doesn't
    shift as the number changes; a counter that went backwards (reset) reads 0."""
    n = max(0, b - a) / dt / 1000
    return f"{n:5.1f}" if n < 999.95 else f"{n:5.0f}"


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
        first = self.last is None
        if first:
            self.last = (time.monotonic(), await asyncio.to_thread(self.read))
        # A counter may not move between two reads (a coarse clock: a VM's CPU ticks, an idle
        # disk): then there is nothing new to show, so the last text stays and the next read
        # measures from the same start, over a longer time. The first update waits for a
        # change, up to about a second, so a value shows at once.
        for _ in range(4 if first else 1):
            if first:
                await asyncio.sleep(0.25)
            (t0, prev), (t1, now) = self.last, (time.monotonic(), await asyncio.to_thread(self.read))
            if prev is None or now is None:
                self.last = (t1, now)
                return ""
            if t1 > t0 and (text := self.show(prev, now, t1 - t0)):
                self.last = (t1, now)
                self.text = text
                return text
        return self.text


class Cpu(Rate):
    """CPU busy share, all cores, over the last `every` seconds."""

    def read(self):                    # -> (busy, total), in any unit: only the ratio counts
        if PROC:                       # /proc/stat: cpu user nice system idle iowait irq softirq steal
            s = proc("/proc/stat")
            if s is None:
                return None
            v = [int(x) for x in s.split("\n", 1)[0].split()[1:9]]
            return sum(v) - v[3] - v[4], sum(v)      # guest is inside user already
        if psutil:                     # seconds; the fields differ by system
            t = psutil.cpu_times()
            total = sum(t) - getattr(t, "guest", 0) - getattr(t, "guest_nice", 0)
            return total - t.idle - getattr(t, "iowait", 0), total
        return None

    def show(self, prev, now, dt):
        total = now[1] - prev[1]
        return f"cpu{100 * (now[0] - prev[0]) / total:4.0f}%" if total > 0 else ""


class Net(Rate):
    """Network down/up in kB/s (1 kB = 1000 bytes): every interface but lo, or Net("eth0")."""

    def __init__(self, iface=None, every=2.0):
        super().__init__(every)
        self.iface = iface

    def pick(self, name):
        return name == self.iface if self.iface else not loopback(name)

    def read(self):                    # -> (received, sent) bytes
        rx = tx = 0
        if PROC:                       # /proc/net/dev: "eth0: rx_bytes ... (8 fields) tx_bytes ..."
            s = proc("/proc/net/dev")
            if s is None:
                return None
            for ln in s.splitlines()[2:]:
                name, _, rest = ln.partition(":")
                if self.pick(name.strip()):
                    v = rest.split()
                    rx, tx = rx + int(v[0]), tx + int(v[8])
            return rx, tx
        if psutil:
            for name, c in psutil.net_io_counters(pernic=True).items():
                if self.pick(name):
                    rx, tx = rx + c.bytes_recv, tx + c.bytes_sent
            return rx, tx
        return None

    def show(self, prev, now, dt):
        return f"↓{kb(prev[0], now[0], dt)} ↑{kb(prev[1], now[1], dt)} kB/s"


class Disk(Rate):
    """Disk read/write in kB/s: every physical disk, or Disk("nvme0n1") for one.
    On Linux, physical = has /sys/block/<name>/device, which skips partitions (sda1 is
    counted in sda) and stacked devices (loop, dm-*, md*, zram, zd*), so no byte is
    counted twice. psutil's own total counts those stacked devices too, so Linux reads
    /proc even when psutil is there; on macOS and Windows psutil lists whole disks only."""

    def __init__(self, dev=None, every=2.0):
        super().__init__(every)
        self.dev = dev

    def read(self):                    # -> (read, written) bytes
        rd = wr = 0
        if PROC:                       # /proc/diskstats: major minor name reads merged sectors_read
            s = proc("/proc/diskstats")    # ms writes merged sectors_written ...; a sector is 512 B
            if s is None:
                return None
            for ln in s.splitlines():
                v = ln.split()
                if len(v) >= 10 and (v[2] == self.dev or (
                        self.dev is None and os.path.exists(f"/sys/block/{v[2]}/device"))):
                    rd, wr = rd + int(v[5]) * 512, wr + int(v[9]) * 512
            return rd, wr
        if psutil:
            c = (psutil.disk_io_counters(perdisk=True).get(self.dev) if self.dev
                 else psutil.disk_io_counters())
            return None if c is None else (c.read_bytes, c.write_bytes)
        return None

    def show(self, prev, now, dt):
        return f"r {kb(prev[0], now[0], dt)} w {kb(prev[1], now[1], dt)} kB/s"


class Mem:
    """RAM in use: 1 - available / total, so the page cache doesn't count as used
    (psutil's percent is the same formula)."""

    def __init__(self, every=5.0):
        self.every, self.text = every, ""

    def read(self):                    # -> percent, or None
        if PROC:
            s = proc("/proc/meminfo")
            if s is None:
                return None
            kv = {ln.split(":")[0]: int(ln.split()[1]) for ln in s.splitlines() if ":" in ln}
            return 100 * (1 - kv["MemAvailable"] / kv["MemTotal"])
        return psutil.virtual_memory().percent if psutil else None

    async def update(self, ctx):
        used = await asyncio.to_thread(self.read)
        return "" if used is None else f"mem{used:4.0f}%"


class Space:
    """Space on the filesystem that holds `path`: "/ 623G/956G ×1.75" = used / total in
    GiB, as `df -h` counts, and on ZFS the compression ratio (the data's size / the room
    it takes on disk), which `zfs get` reads without root. On ZFS the total is used + the
    pool's free space, which every dataset of the pool shares, so it changes as the others
    grow. The default path is the root (C:\\ on Windows)."""

    def __init__(self, path=None, every=30.0):
        self.path, self.every, self.text = path or os.path.abspath(os.sep), every, ""

    def mount(self):                   # -> (device, fstype) of the mount holding self.path
        real, best = os.path.realpath(self.path), ("", "", "")
        if PROC:                       # /proc/self/mounts: device mountpoint fstype options ...
            s = proc("/proc/self/mounts") or ""
            unesc = lambda x: re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), x)
            mounts = [[unesc(x) for x in ln.split()[:3]] for ln in s.splitlines()]
        elif psutil:
            mounts = [(p.device, p.mountpoint, p.fstype) for p in psutil.disk_partitions(all=True)]
        else:
            mounts = []
        for dev, mnt, fs in mounts:
            inside = real == mnt or real.startswith(mnt.rstrip("/\\") + os.sep)
            if inside and len(mnt) >= len(best[1]):
                best = (dev, mnt, fs)
        return best[0], best[2]

    def read(self):
        try:
            u = shutil.disk_usage(self.path)
        except OSError:
            return ""
        size = lambda n: f"{n / 2**30:.0f}G" if n < 2**40 else f"{n / 2**40:.1f}T"
        out = f"{self.path} {size(u.used)}/{size(u.total)}"
        dev, fs = self.mount()
        if fs == "zfs":
            try:
                r = subprocess.run(["zfs", "get", "-Hpo", "value", "compressratio", dev],
                                   capture_output=True, text=True, timeout=5)
                out += f" ×{float(r.stdout):.2f}" if r.returncode == 0 else ""
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass                   # no zfs command, or an answer we can't read
        return out

    async def update(self, ctx):
        return await asyncio.to_thread(self.read)


class Clock:
    """Like clock(), but update() waits for the next whole second itself and every = 0,
    so the seconds never lag or skip (a plain sleep(1) after each update slowly drifts)."""

    def __init__(self, fmt="%m-%d %H:%M:%S %Z"):
        self.fmt, self.every, self.text = fmt, 0, ""

    async def update(self, ctx):
        await asyncio.sleep(1 - time.time() % 1 + 0.005)
        return time.strftime(self.fmt)


# The focused window's git branch, worktree and changes ("fix-x wt 2 mod ↑1"); for a window
# with a Claude Code session in it, ✎ and the repo it last wrote in, not where it started.
from py_screen.agents.widget import branch, caption

# The caption line under each pane: after its number and name, where the work is (folder, git
# branch and changes; ✎ for the repo an agent in it last wrote in). Without it: the folder.
CAPTION = caption()

# Shown right to left as room allows: when the line is short, the first ones drop.
WIDGETS = [
    branch(),                          # or cmd(["git", "-C", "{cwd}", "branch", "--show-current"])
    Cpu(),
    Mem(),
    Net(),
    Disk(),
    Space(),                           # or Space("/home"), one per filesystem you care about
    load(),                            # 1-minute load average (not on Windows)
    Clock(),                           # e.g. 11-04 14:05:09 UTC
]

BIND = {
    "T": ("top", ["top"]),             # C-a T opens a window running top (not on Windows)
}

# PREFIX = "C-a"       # default: C-] inside GNU screen, else C-a
# MOUSE = True         # False: plain drags select text in the terminal again
# SCROLLBACK = 5000    # rows of history per window; 0 = none
# SHOW_WIDGETS = True  # False: start with the widgets hidden (prefix S toggles them)
# STATUS_LINES = 2     # the window tabs wrap onto up to this many status rows when they
#                      # don't fit in one; 1 = one row, a tab that doesn't fit is left out

# ON_CHANGE: a command pys runs (about 2 s after) a window opens or exits, a title changes or
# the brain starts, with PYS_EVENT=change; and once more right before `pys kill` or prefix Q
# end every window, with PYS_EVENT=kill, while they all still run. PYS_DIR is set, so `pys
# ls --json` in it lists this session. One run at a time; its output goes to server.log.
# ON_CHANGE = ["pys-sessions", "save", "--quiet"]    # snapshot the windows, to restore them
#                                                     # after pys died (comes with pys)
# KILL_HOOK_TIMEOUT = 5   # seconds a kill waits for ON_CHANGE before it ends the windows
# SESSIONS_KEEP_ENV = []  # pys-sessions: variables of a claude's environment that its
#                         # resume gets back (only these: an environment holds secrets)
