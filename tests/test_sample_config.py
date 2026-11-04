"""The sample config that ships in the wheel: `pys sample-config` prints it, it loads with
no error, and in a real session its widgets fill the status line."""

import collections
import os
import subprocess
import sys
import time
from pathlib import Path

import asyncio

import pytest

import py_screen

try:
    import psutil
except ImportError:
    psutil = None
from harness import PYS, Session, Term

SAMPLE = Path(py_screen.__file__).parent / "sample_config.py"


def test_pys_prints_the_sample():
    out = subprocess.run(PYS + ["sample-config"], capture_output=True, text=True, timeout=15)
    assert out.returncode == 0 and out.stdout == SAMPLE.read_text()


def test_sample_loads(monkeypatch):
    from py_screen import config
    monkeypatch.setenv("PYS_CONFIG", str(SAMPLE))
    cfg = config.load_config()
    assert cfg.error is None
    assert [type(w).__name__ for w in cfg.widgets] == ["Cmd", "Cpu", "Mem", "Net", "Disk", "Space", "Load", "Clock"]
    assert "T" in cfg.bind


def test_sample_widgets_in_a_session(tmp_path):
    sess = Session("sample")
    sess.env["PYS_CONFIG"] = str(SAMPLE)
    t = Term(PYS + ["attach"], sess.env, 200, 20)
    try:
        t.wait_for(" pys ")
        clock = lambda: t.text().splitlines()[-1].rstrip().rsplit(" ", 2)[-2]
        if sys.platform.startswith("linux") or psutil:    # /proc, or psutil elsewhere
            t.wait_until(lambda s: all(k in s.splitlines()[-1] for k in ("cpu", "mem", "↓", " r", "kB/s", "G/")))
        t.wait_until(lambda s: s.splitlines()[-1].rstrip()[-12:-4].count(":") == 2)  # HH:MM:SS
        first = clock()
        time.sleep(1.2)
        assert clock() != first                           # the seconds tick
    finally:
        t.close()
        sess.kill()


def test_disk_counts_whole_physical_disks_once(tmp_path, monkeypatch):
    """The /proc path's filter, on fake diskstats: PROC is forced on, because on macOS
    and Windows the widget would (rightly) ask psutil for the real disks instead."""
    ns = sample_ns(PROC=True)
    stats = (" 254 0 vda 10 0 4 0 20 0 8 0 0 0 0\n"         # disk: 4 sectors read, 8 written
             " 254 1 vda1 10 0 4 0 20 0 8 0 0 0 0\n"        # its partition: already in vda
             " 230 0 zd0 10 0 100 0 20 0 100 0 0 0 0\n")    # a zvol: no device link
    monkeypatch.setitem(ns, "proc", lambda path: stats)
    monkeypatch.setattr(ns["os"].path, "exists", lambda p: p == "/sys/block/vda/device")
    assert ns["Disk"]().read() == (4 * 512, 8 * 512)
    assert ns["Disk"]("zd0").read() == (100 * 512, 100 * 512)
    assert ns["Disk"]().show((0, 0), (24_600, 2_000_000), 2.0) == "r  12.3 w  1000 kB/s"


def sample_ns(**over):
    ns = {"cmd": lambda *a, **k: None, "load": lambda *a, **k: None}
    exec(SAMPLE.read_text(), ns)
    ns.update(over)                    # the widgets read PROC / psutil from here at call time
    return ns


@pytest.mark.skipif(psutil is None, reason="psutil not installed")
def test_psutil_path_matches_proc():
    """The psutil path (what macOS and Windows use), run here with /proc turned off; on
    Linux both paths see the same kernel, so they must agree."""
    via_ps, via_proc = sample_ns(PROC=False), sample_ns()
    for name in ("Cpu", "Net", "Disk"):
        w = via_ps[name]()
        assert asyncio.run(w.update({})).endswith(("%", "kB/s")), name
        assert all(x >= 0 for x in w.read()), name
    busy, total = via_ps["Cpu"]().read()
    assert 0 <= busy <= total
    assert abs(via_ps["Mem"]().read() - via_proc["Mem"]().read()) < 5          # percent
    if sys.platform.startswith("linux"):
        ps_rx, _ = via_ps["Net"]().read()
        proc_rx, _ = via_proc["Net"]().read()
        assert abs(ps_rx - proc_rx) < 10_000_000                               # same counters
        assert via_ps["Disk"]().read()[0] >= via_proc["Disk"]().read()[0] * 0.99  # psutil adds zvols etc.


def test_no_proc_no_psutil_shows_nothing():
    ns = sample_ns(PROC=False, psutil=None)
    for name in ("Cpu", "Mem", "Net", "Disk"):
        assert asyncio.run(ns[name]().update({})) == "", name


def test_space_reads_this_machine():
    text = sample_ns()["Space"]().read()
    assert text.startswith(os.path.abspath(os.sep) + " ") and "/" in text.split(" ", 1)[1]


Usage = collections.namedtuple("Usage", "total used free")    # as shutil.disk_usage


class Ran:                             # what subprocess.run returns, for a fake zfs
    def __init__(self, out, code=0):
        self.stdout, self.returncode = out, code


def test_space_on_zfs_shows_the_ratio(monkeypatch):
    """The mount is the longest one that holds the path (/home over /), its device is the
    dataset that zfs is asked about, and an escaped space in a mount point is decoded."""
    ns = sample_ns(PROC=True)
    mounts = ("rpool/ROOT/debian / zfs rw 0 0\n"
              "rpool/home /home zfs rw 0 0\n"
              "tank/my\\040data /my\\040data zfs rw 0 0\n"
              "/dev/sda1 /boot ext4 rw 0 0\n")
    monkeypatch.setitem(ns, "proc", lambda path: mounts)
    monkeypatch.setattr(ns["os"].path, "realpath", lambda p: p)
    monkeypatch.setattr(ns["shutil"], "disk_usage", lambda p: Usage(4 * 2**30, 2**30, 3 * 2**30))
    asked = []
    monkeypatch.setattr(ns["subprocess"], "run", lambda argv, **k: asked.append(argv[-1]) or Ran("1.75\n"))
    assert ns["Space"]("/home/me").mount() == ("rpool/home", "zfs")
    assert ns["Space"]("/my data/x").mount() == ("tank/my data", "zfs")
    assert ns["Space"]("/").read() == "/ 1G/4G ×1.75" and asked == ["rpool/ROOT/debian"]
    assert ns["Space"]("/boot").read() == "/boot 1G/4G"                     # not zfs: no ratio


def test_space_without_zfs_command(monkeypatch):
    ns = sample_ns(PROC=True)
    monkeypatch.setitem(ns, "proc", lambda path: "rpool/ROOT/debian / zfs rw 0 0\n")
    monkeypatch.setattr(ns["os"].path, "realpath", lambda p: p)
    monkeypatch.setattr(ns["shutil"], "disk_usage", lambda p: Usage(2**41, 2**40, 2**40))

    def missing(*a, **k):
        raise FileNotFoundError("zfs")
    monkeypatch.setattr(ns["subprocess"], "run", missing)
    assert ns["Space"]("/").read() == "/ 1.0T/2.0T"
    monkeypatch.setattr(ns["shutil"], "disk_usage", lambda p: (_ for _ in ()).throw(OSError()))
    assert ns["Space"]("/gone").read() == ""


def test_loopback_names():
    ns = sample_ns()
    assert all(ns["loopback"](n) for n in ("lo", "lo0", "Loopback Pseudo-Interface 1"))
    assert not any(ns["loopback"](n) for n in ("eth0", "en0", "Ethernet", "wlan0"))
