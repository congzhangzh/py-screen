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
    assert [type(w).__name__ for w in cfg.widgets] == ["Cmd", "Cpu", "Mem", "Net", "Load", "Clock"]
    assert "T" in cfg.bind


def test_sample_widgets_in_a_session(tmp_path):
    sess = Session("sample")
    sess.env["PYS_CONFIG"] = str(SAMPLE)
    t = Term(PYS + ["attach"], sess.env, 160, 20)
    try:
        t.wait_for(" pys ")
        clock = lambda: t.text().splitlines()[-1].rstrip().rsplit(" ", 2)[-2]
        if sys.platform.startswith("linux"):              # cpu/mem/net read /proc
            t.wait_until(lambda s: all(k in s.splitlines()[-1] for k in ("cpu", "mem", "kB/s")))
        t.wait_until(lambda s: s.splitlines()[-1].rstrip()[-12:-4].count(":") == 2)  # HH:MM:SS
        first = clock()
        time.sleep(1.2)
        assert clock() != first                           # the seconds tick
    finally:
        t.close()
        sess.kill()
