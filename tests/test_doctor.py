"""CAPTION on the caption line, and `pys doctor` (where, and a check) / `--init` / `--reset`."""
from __future__ import annotations

import subprocess
from pathlib import Path

from harness import PYS, Session, Term

from py_screen import config


def pys_doctor(cfg: Path, *args: str) -> subprocess.CompletedProcess:
    import os
    env = dict(os.environ, PYS_CONFIG=str(cfg))
    return subprocess.run(PYS + ["doctor", *args], env=env, capture_output=True, text=True,
                          timeout=60)


def test_clean_drops_whole_escape_sequences() -> None:
    # the bare ESC used to match first and leave "[31m" behind
    assert config.clean("\x1b[31mred\x1b[0m done\nsecond") == "red done"


def test_caption_shows_after_the_windows_name(tmp_path: Path) -> None:
    cfg = tmp_path / "config.py"
    cfg.write_text('''
class Probe:
    every = 0.3
    async def update(self, ctx):
        if ctx["wid"] == 1:
            raise ValueError("boom")
        return f"cap{ctx['wid']}\\x1b[31m!"
CAPTION = Probe()
''')
    sess = Session("cap")
    sess.env["PYS_CONFIG"] = str(cfg)
    t = Term(PYS + ["attach"], sess.env)
    try:
        t.wait_for("$ ")
        t.wait_for("cap0!")                      # the text, its escape sequence dropped
        t.send("\x1d|")                          # split: window 1 shows beside it
        t.wait_for("!ValueError")                # a failing caption names its error
    finally:
        t.close()
        sess.kill()


def test_a_bad_caption_is_a_config_error(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.py"
    cfg.write_text("CAPTION = 'folder'\n")
    monkeypatch.setenv("PYS_CONFIG", str(cfg))
    c = config.load_config()
    assert c.caption is None and c.error == "config error: CAPTION"


def test_init_writes_the_sample_and_never_over_yours(tmp_path: Path) -> None:
    cfg = tmp_path / "pys" / "config.py"
    assert f"no config at {cfg}" in pys_doctor(cfg).stdout          # where, before there is one
    r = pys_doctor(cfg, "--init")
    assert r.returncode == 0 and cfg.read_text().startswith("# A sample pys config")
    cfg.write_text("PREFIX = 'C-b'\n")                       # now it is yours
    r = pys_doctor(cfg, "--init")
    assert r.returncode == 1 and "exists" in r.stderr
    assert cfg.read_text() == "PREFIX = 'C-b'\n"
    r = pys_doctor(cfg, "--reset")
    assert r.returncode == 0
    kept = [p for p in cfg.parent.iterdir() if p.name.startswith("config.py.")]
    assert len(kept) == 1 and kept[0].read_text() == "PREFIX = 'C-b'\n"
    assert cfg.read_text().startswith("# A sample pys config")


def test_doctor_finds_typos_failing_widgets_and_a_missing_hook(tmp_path: Path) -> None:
    cfg = tmp_path / "config.py"
    cfg.write_text('''
class Broken:
    every, text = 5, ""
    async def update(self, ctx):
        raise RuntimeError("no data")
WIDGETS = [Broken(), clock("%Y")]
WIDGET = []                                   # a typo: pys never reads it
ON_CHANGE = ["no-such-command-pys-test"]
''')
    r = pys_doctor(cfg)
    assert r.returncode == 1
    assert "✗ widget 0 Broken: RuntimeError: no data" in r.stdout
    assert "✓ widget 1 Clock: '20" in r.stdout
    assert "WIDGET is not read by pys: did you mean WIDGETS?" in r.stdout
    assert "'no-such-command-pys-test' is not on PATH" in r.stdout


def test_doctor_names_the_line_that_does_not_run(tmp_path: Path) -> None:
    cfg = tmp_path / "config.py"
    cfg.write_text("PREFIX = 'C-a'\nWIDGETS = [load(), nope()]\n")
    r = pys_doctor(cfg)
    assert r.returncode == 1 and "does not run (line 2): NameError" in r.stdout


def test_doctor_passes_the_sample(tmp_path: Path) -> None:
    cfg = tmp_path / "config.py"
    pys_doctor(cfg, "--init")
    r = pys_doctor(cfg)
    assert r.returncode == 0, r.stdout
    assert "✓ widget 0 Branch" in r.stdout and "no errors" in r.stdout


def test_pys_own_commands_need_not_be_on_path() -> None:
    """ON_CHANGE = ["pys-sessions", ...] works for a uvx install or a bundle whose bin/ is
    not on PATH: pys runs its own commands with its own Python, never another install's."""
    import sys
    from py_screen import config
    assert config.command(["pys-sessions", "save", "--quiet"]) == \
        [sys.executable, "-m", "py_screen.agents.sessions", "save", "--quiet"]
    assert config.command(["no-such-command-pys-test", "x"]) == ["no-such-command-pys-test", "x"]
    assert config.command([]) == []
