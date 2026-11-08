"""`pys setup` and the SessionStart hook it installs, on any system (no terminal needed)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PY = [sys.executable, "-m"]


def env_for(tmp: Path, **extra: str) -> dict:
    e = dict(os.environ, HOME=str(tmp), USERPROFILE=str(tmp), PYS_CONFIG=str(tmp / "cfg" / "config.py"),
             CLAUDE_CONFIG_DIR=str(tmp / "claude"))
    e.pop("PYS_WINDOW", None)                 # this test may run inside a pys window
    return e | extra


def setup(tmp: Path, *args: str, **extra: str) -> str:
    r = subprocess.run([*PY, "py_screen", "setup", *args], env=env_for(tmp, **extra),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_a_dry_run_changes_nothing(tmp_path: Path) -> None:
    out = setup(tmp_path)
    assert "dry run" in out and "write the sample" in out and "add a SessionStart hook" in out
    assert not (tmp_path / "cfg").exists() and not (tmp_path / "claude").exists()


def test_setup_does_it_all_once(tmp_path: Path) -> None:
    claude = tmp_path / "claude"
    claude.mkdir()
    settings = {"env": {"ANTHROPIC_MODEL": "x"}, "hooks": {"SessionStart": [
        {"matcher": "startup", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
    (claude / "settings.json").write_text(json.dumps(settings))
    setup(tmp_path, "--yes")

    cfg = (tmp_path / "cfg" / "config.py").read_text(encoding="utf-8")
    assert "sample pys config" in cfg and 'ON_CHANGE = ["pys-sessions", "save", "--quiet"]' in cfg
    assert (claude / "skills" / "pys-drive" / "SKILL.md").exists()
    assert (claude / "skills" / "pys-restore" / "SKILL.md").exists()
    s = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert s["env"] == {"ANTHROPIC_MODEL": "x"}                       # the rest is kept
    cmds = [h["command"] for g in s["hooks"]["SessionStart"] for h in g["hooks"]]
    assert cmds[0] == "echo mine" and len(cmds) == 2
    assert "py_screen.agents.instructions" in cmds[1] and Path(sys.executable).name in cmds[1]
    assert list(claude.glob("settings.json.2*"))                     # a copy, first

    again = setup(tmp_path, "--yes")                                  # nothing twice
    assert "has an ON_CHANGE already" in again and "runs this install already" in again
    assert "already linked" in again
    s2 = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert s2 == s


def test_setup_keeps_your_config_and_adds_only_on_change(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg" / "config.py"
    cfg.parent.mkdir()
    cfg.write_text("PREFIX = 'C-b'\n")
    setup(tmp_path, "--yes")
    assert cfg.read_text(encoding="utf-8").startswith("PREFIX = 'C-b'\n")
    assert "ON_CHANGE" in cfg.read_text(encoding="utf-8")
    assert [p.read_text() for p in cfg.parent.glob("config.py.2*")] == ["PREFIX = 'C-b'\n"]


def test_setup_moves_the_hook_to_this_install(tmp_path: Path) -> None:
    claude = tmp_path / "claude"
    claude.mkdir()
    old = '"/gone/venv/bin/python" -m py_screen.agents.instructions'
    (claude / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": old}]}]}}))
    assert "point the SessionStart hook at this install" in setup(tmp_path, "--yes")
    s = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for g in s["hooks"]["SessionStart"] for h in g["hooks"]]
    assert len(cmds) == 1 and cmds[0] != old


def hook(tmp: Path, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([*PY, "py_screen.agents.instructions"], env=env_for(tmp, **extra),
                          input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_the_hook_speaks_only_inside_pys(tmp_path: Path) -> None:
    r = hook(tmp_path)
    assert r.returncode == 0 and r.stdout == ""                       # outside pys: nothing
    r = hook(tmp_path, PYS_WINDOW="7")
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    ctx = out["additionalContext"]
    assert "window 7" in ctx and "title --owner $PPID" in ctx and "kill" in ctx


def test_the_hook_names_this_install_when_pys_is_not_on_path(tmp_path: Path) -> None:
    """uvx, or a bundle whose bin/ is not on PATH: the text says how to run pys from here."""
    r = hook(tmp_path, PYS_WINDOW="3", PATH=str(tmp_path / "empty"))
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    py = Path(sys.executable).as_posix() if os.name == "nt" else sys.executable
    assert f'"{py}" -m py_screen title' in ctx
