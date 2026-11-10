"""`pys setup`: everything pys offers for AI agents, in one step, the same with uvx, pip, a uv
tool or the bundle. A dry run by default (what it would do); --yes does it.

  1. a config: the sample when there is none; then ON_CHANGE = pys-sessions save, so every
     change of the windows (and a kill) leaves a snapshot to restore from, in a file of its
     own, config.d/50-pys-setup.py, and only when your config has no ON_CHANGE yet. Your
     config.py is never edited
  2. the agent skills (pys-drive, pys-restore) linked into Claude Code's skills folder
  3. hooks in Claude Code's settings.json (a copy of it first): SessionStart, so every
     session that starts in a pys window gets a few lines on how to behave there
     (instructions.py); Notification, Stop, UserPromptSubmit and SessionEnd, so its window's
     tab turns red when it asks you, green when its turn ended, and back when you answer
     (attn.py)

Each step is skipped when it is done already, so running it again is safe. Claude Code's
folder is $CLAUDE_CONFIG_DIR, else ~/.claude. After a pip/uv upgrade nothing needs redoing;
run it again after moving a bundle, or with another install, to point everything at it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .. import config, doctor, utf8_pipes
from . import attn, claude, instructions, listing

DROP_IN = "50-pys-setup.py"
ON_CHANGE_LINES = ('# written by `pys setup` (config.d runs after config.py, by name): a snapshot\n'
                   '# at each change of the windows and before a kill, for `pys-sessions restore`\n'
                   '# after pys died. Delete this file to stop it, or set ON_CHANGE in a later one.\n'
                   'ON_CHANGE = ["pys-sessions", "save", "--quiet"]\n')


def keep_copy(p: Path) -> Path:
    """A copy beside it, never over an older one (config.py.20261108-1530, .v2, …)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = p.with_name(f"{p.name}.{stamp}"), 1
    while dest.exists():
        n += 1
        dest = p.with_name(f"{p.name}.{stamp}.v{n}")
    dest.write_bytes(p.read_bytes())
    return dest


def step_config(do: bool) -> str:
    p, drop = config.path(), config.drop_in_dir() / DROP_IN
    had = bool(config.load_config().on_change)   # the sample's own ON_CHANGE is commented out
    steps = []
    if not p.is_file():
        steps.append(f"write the sample to {p}")
        if do:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(doctor.SAMPLE.read_bytes())
    if had:
        steps.append("ON_CHANGE is set already; left as it is")
    else:
        steps.append(f"turn on snapshots (ON_CHANGE) in {drop}")
        if do:
            drop.parent.mkdir(parents=True, exist_ok=True)
            drop.write_text(ON_CHANGE_LINES, encoding="utf-8")
    return "config: " + "; ".join(steps)


def step_skills(do: bool) -> str:
    dest = claude.home() / "skills"
    if not do:
        return f"skills: link pys-drive and pys-restore into {dest}"
    listing.cmd_skills(type("A", (), {"install": True, "dir": str(dest)})())
    return f"skills: done (above) in {dest}"


def hook_command(module: str) -> str:
    """What settings.json runs: this install's Python, whatever is on PATH."""
    return instructions.hook_command().replace(instructions.MARK, module)


# (event, the module that runs, what it is for): each module is its own mark
HOOKS = [("SessionStart", instructions.MARK, "instructions")] + \
        [(e, attn.MARK, "window marks") for e in attn.EVENTS]


def step_hook(do: bool) -> str:
    p = claude.home() / "settings.json"
    try:
        s = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError as e:
        return f"hook: ⚠ {p} is not valid JSON ({e}); left as it is, fix it and run again"
    todo = []                            # (event, the groups, ours there, the command)
    for event, mark, _ in HOOKS:
        want = hook_command(mark)
        groups = s.setdefault("hooks", {}).setdefault(event, [])
        ours = [(g, h) for g in groups for h in g.get("hooks", [])
                if mark in str(h.get("command", ""))]
        if not any(h.get("command") == want for _, h in ours):
            todo.append((event, groups, ours, want))
    if not todo:
        return f"hook: every hook in {p} runs this install already"
    what = "; ".join(f"point the {event} hook at this install" if ours else f"add a {event} hook"
                     for event, _, ours, _ in todo)
    if not do:
        return f"hook: {what} in {p}: {todo[0][3]}" + (" (and the others)" if len(todo) > 1
                                                          else "")
    kept = f" (a copy kept as {keep_copy(p).name})" if p.is_file() else ""
    for event, groups, ours, want in todo:
        for g, h in ours:                # another install's: this one replaces it
            g["hooks"].remove(h)
        s["hooks"][event] = [g for g in groups if g.get("hooks")] + \
            [{"hooks": [{"type": "command", "command": want}]}]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"hook: {what} in {p}{kept}"


def from_uv_cache() -> bool:
    """Runs from uv's cache (uvx), which `uv cache clean` empties."""
    import subprocess
    try:
        r = subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    cache = r.stdout.strip()
    return bool(cache) and os.path.realpath(sys.prefix).startswith(os.path.realpath(cache) + os.sep)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    utf8_pipes()
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    do = "--yes" in argv
    print(f"pys setup, from {Path(sys.executable).parent}" + ("" if do else " (a dry run)"))
    if from_uv_cache():
        print("  ⚠ this pys runs from uv's cache (uvx): the skills and the hook would point "
              "into it,\n    and `uv cache clean` empties it. `uv tool install py-screen`, "
              "then `pys setup`, lasts.")
    for step in (step_config, step_skills, step_hook):
        print("  " + step(do))
    if not do:
        print("a dry run; add --yes to do it")
        return 0
    print("then: a new claude in a pys window gets the instructions; `pys doctor` checks the rest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
