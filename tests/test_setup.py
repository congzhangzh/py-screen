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
    for k in ("PYS_WINDOW", "PYS_SESSION"):  # this test may run inside a pys window: never
        e.pop(k, None)                        # reach that server (the hook asks its group)
    e["PYS_DIR"] = str(tmp / "run")           # a runtime dir of its own, with no server
    return e | extra


def setup(tmp: Path, *args: str, **extra: str) -> str:
    r = subprocess.run([*PY, "py_screen", "setup", *args], env=env_for(tmp, **extra),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_a_dry_run_changes_nothing(tmp_path: Path) -> None:
    out = setup(tmp_path)
    assert "dry run" in out and "write the sample" in out and "add a SessionStart hook" in out
    assert "rule: write" in out
    assert not (tmp_path / "cfg").exists() and not (tmp_path / "claude").exists()


def test_setup_does_it_all_once(tmp_path: Path) -> None:
    claude = tmp_path / "claude"
    claude.mkdir()
    settings = {"env": {"ANTHROPIC_MODEL": "x"}, "hooks": {"SessionStart": [
        {"matcher": "startup", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
    (claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    setup(tmp_path, "--yes")

    cfg = (tmp_path / "cfg" / "config.py").read_text(encoding="utf-8")
    assert "sample pys config" in cfg                                 # the sample, as it is
    drop = (tmp_path / "cfg" / "config.d" / "50-pys-setup.py").read_text(encoding="utf-8")
    assert 'ON_CHANGE = ["pys-sessions", "save", "--quiet"]' in drop
    assert (claude / "skills" / "pys-drive" / "SKILL.md").exists()
    assert (claude / "skills" / "pys-restore" / "SKILL.md").exists()
    s = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert s["env"] == {"ANTHROPIC_MODEL": "x"}                       # the rest is kept
    cmds = [h["command"] for g in s["hooks"]["SessionStart"] for h in g["hooks"]]
    assert cmds[0] == "echo mine" and len(cmds) == 2
    assert "py_screen.agents.instructions" in cmds[1] and Path(sys.executable).name in cmds[1]
    assert list(claude.glob("settings.json.2*"))                     # a copy, first

    again = setup(tmp_path, "--yes")                                  # nothing twice
    assert "ON_CHANGE is set already" in again and "runs this install already" in again
    assert "already linked" in again
    assert "are linked into" in setup(tmp_path)                        # and a dry run says so
    s2 = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert s2 == s


def test_setup_writes_the_rule_and_the_hook_then_points_at_it(tmp_path: Path) -> None:
    """Claude Code's rules/pys.md: pys's rules in a file every session loads. While it is
    this install's own, the hook only adds the window's number, so nothing comes twice."""
    rule = tmp_path / "claude" / "rules" / "pys.md"
    assert "is there and not pys's" not in setup(tmp_path)          # a dry run writes nothing
    assert not rule.exists()
    full = json.loads(hook(tmp_path, PYS_WINDOW="7").stdout)["hookSpecificOutput"]["additionalContext"]
    assert "title --agent" in full                                    # no rule yet: the hook has it all

    assert "rule: write" in setup(tmp_path, "--yes")
    text = rule.read_text(encoding="utf-8")
    assert text.startswith("<!-- written by `pys setup`") and "$PYS_WINDOW" in text
    for line in full.splitlines()[1:]:                                # the same rules, every one
        assert line in text
    assert "rule: " in (again := setup(tmp_path, "--yes")) and "is up to date" in again
    short = json.loads(hook(tmp_path, PYS_WINDOW="7").stdout)["hookSpecificOutput"]["additionalContext"]
    assert "window 7" in short and "pys.md" in short and "title --agent" not in short
    doc = subprocess.run([*PY, "py_screen", "doctor"], env=env_for(tmp_path), capture_output=True,
                         text=True, encoding="utf-8", timeout=60).stdout
    assert "every claude reads pys's rule" in doc

    rule.write_text(text.replace("Never", "Do not"), encoding="utf-8")   # an older pys's
    assert "up to date" in setup(tmp_path, "--yes") and rule.read_text(encoding="utf-8") == text
    assert "title --agent" not in json.loads(hook(tmp_path, PYS_WINDOW="7").stdout)[
        "hookSpecificOutput"]["additionalContext"]


def test_a_rule_file_of_yours_is_left_alone(tmp_path: Path) -> None:
    rule = tmp_path / "claude" / "rules" / "pys.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("# my own pys notes\n", encoding="utf-8")
    assert "is there and not pys's" in setup(tmp_path, "--yes")
    assert rule.read_text(encoding="utf-8") == "# my own pys notes\n"
    ctx = json.loads(hook(tmp_path, PYS_WINDOW="2").stdout)["hookSpecificOutput"]["additionalContext"]
    assert "title --agent" in ctx                                     # so the hook says it all


def test_setup_never_edits_your_config(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg" / "config.py"
    cfg.parent.mkdir()
    cfg.write_text("PREFIX = 'C-b'\n", encoding="utf-8")
    setup(tmp_path, "--yes")
    assert cfg.read_text(encoding="utf-8") == "PREFIX = 'C-b'\n"
    assert (cfg.parent / "config.d" / "50-pys-setup.py").exists()
    mine = tmp_path / "cfg2" / "config.py"                  # an ON_CHANGE of your own wins
    mine.parent.mkdir()
    mine.write_text('ON_CHANGE = ["my-hook"]\n', encoding="utf-8")
    out = setup(tmp_path, "--yes", PYS_CONFIG=str(mine))
    assert "ON_CHANGE is set already" in out and not (mine.parent / "config.d").exists()


def test_setup_moves_the_hook_to_this_install(tmp_path: Path) -> None:
    claude = tmp_path / "claude"
    claude.mkdir()
    old = '"/gone/venv/bin/python" -m py_screen.agents.instructions'
    (claude / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": old}]}]}}), encoding="utf-8")
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
    assert "window 7" in ctx and "title --agent" in ctx and "kill" in ctx


def test_the_hook_names_this_install_when_pys_is_not_on_path(tmp_path: Path) -> None:
    """uvx, or a bundle whose bin/ is not on PATH: the text says how to run pys from here."""
    r = hook(tmp_path, PYS_WINDOW="3", PATH=str(tmp_path / "empty"))
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    py = Path(sys.executable).as_posix() if os.name == "nt" else sys.executable
    assert f'"{py}" -m py_screen title' in ctx


def test_config_d_adds_to_config_py_and_a_broken_file_is_left_out(tmp_path: Path) -> None:
    """config.d/*.py run after config.py, by name, in its namespace; one that fails is
    named and left out, the others count (pys doctor says which)."""
    cfg = tmp_path / "config.py"
    cfg.write_text("PREFIX = 'C-b'\nBIND = {}\n", encoding="utf-8")
    d = tmp_path / "config.d"
    d.mkdir()
    (d / "10-mine.py").write_text("SCROLLBACK = 123\nBIND['x'] = ('new', [])\n", encoding="utf-8")
    (d / "20-broken.py").write_text("BIND['y'] = ('new', [])\nPREFIX = 'C-q'\n1/0\n", encoding="utf-8")
    (d / "30-later.py").write_text("MOUSE = False\n", encoding="utf-8")
    e = env_for(tmp_path, PYS_CONFIG=str(cfg))
    code = ("from py_screen import config; c = config.load_config(); "
            "print(c.prefix, c.scrollback, sorted(c.bind), c.mouse, c.error)")
    out = subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True,
                         timeout=30, encoding="utf-8", errors="replace").stdout.split()
    assert out[:3] == ["C-b", "123", "['x']"] and out[3] == "False"   # 20-broken left out
    assert "20-broken.py" in " ".join(out)
    doc = subprocess.run([*PY, "py_screen", "doctor"], env=e, capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert "+ 10-mine.py" in doc.stdout and "20-broken.py does not run (line 3)" in doc.stdout


def test_the_command_is_also_named_like_the_package() -> None:
    """`uvx py-screen` runs the command named like the package: py-screen is pys."""
    from importlib.metadata import entry_points
    eps = {e.name: e.value for e in entry_points(group="console_scripts")
           if e.value.startswith("py_screen")}
    assert eps.get("py-screen") == eps.get("pys") == "py_screen:main"


def test_the_hook_never_fails_on_a_runtime_dir_too_long(tmp_path: Path) -> None:
    """A PYS_DIR past AF_UNIX's 104 bytes (macOS's temp dirs are long): the hook still
    answers, and makes no dir."""
    long_dir = tmp_path / ("x" * 120)
    r = hook(tmp_path, PYS_WINDOW="4", PYS_DIR=str(long_dir))
    assert r.returncode == 0 and "window 4" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert not long_dir.exists()
