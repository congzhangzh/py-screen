"""The sample config that ships in the wheel: `pys sample-config` prints it, it loads with
no error, and in a real session its widgets fill the status line."""

import subprocess
import sys
import time
from pathlib import Path

import py_screen
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
    assert [type(w).__name__ for w in cfg.widgets] == ["Cmd", "Cpu", "Mem", "Net", "Disk", "Load", "Clock"]
    assert "T" in cfg.bind


def test_sample_widgets_in_a_session(tmp_path):
    sess = Session("sample")
    sess.env["PYS_CONFIG"] = str(SAMPLE)
    t = Term(PYS + ["attach"], sess.env, 200, 20)
    try:
        t.wait_for(" pys ")
        clock = lambda: t.text().splitlines()[-1].rstrip().rsplit(" ", 2)[-2]
        if sys.platform.startswith("linux"):              # cpu/mem/net read /proc
            t.wait_until(lambda s: all(k in s.splitlines()[-1] for k in ("cpu", "mem", "↓", " r", "kB/s")))
        t.wait_until(lambda s: s.splitlines()[-1].rstrip()[-12:-4].count(":") == 2)  # HH:MM:SS
        first = clock()
        time.sleep(1.2)
        assert clock() != first                           # the seconds tick
    finally:
        t.close()
        sess.kill()


def test_disk_counts_whole_physical_disks_once(tmp_path, monkeypatch):
    ns = {"cmd": lambda *a, **k: None, "load": lambda *a, **k: None}
    exec(SAMPLE.read_text(), ns)
    stats = (" 254 0 vda 10 0 4 0 20 0 8 0 0 0 0\n"         # disk: 4 sectors read, 8 written
             " 254 1 vda1 10 0 4 0 20 0 8 0 0 0 0\n"        # its partition: already in vda
             " 230 0 zd0 10 0 100 0 20 0 100 0 0 0 0\n")    # a zvol: no device link
    monkeypatch.setitem(ns, "proc", lambda path: stats)
    monkeypatch.setattr(ns["os"].path, "exists", lambda p: p == "/sys/block/vda/device")
    assert ns["Disk"]().read() == (4 * 512, 8 * 512)
    assert ns["Disk"]("zd0").read() == (100 * 512, 100 * 512)
    assert ns["Disk"]().show((0, 0), (24_600, 2_000_000), 2.0) == "r  12.3 w  1000 kB/s"
