"""`pys setup`: everything pys offers for AI agents, in one step, the same with uvx, pip, a uv
tool or the bundle. A dry run by default (what it would do); --yes does it.

  1. a config: the sample when there is none; then ON_CHANGE = pys-sessions save, so every
     change of the windows (and a kill) leaves a snapshot to restore from. A config of your
     own is kept: a copy beside it first, then the ON_CHANGE lines added at its end, and only
     when it has no ON_CHANGE yet
  2. the agent skills (pys-drive, pys-restore) linked into Claude Code's skills folder
  3. a SessionStart hook in Claude Code's settings.json (a copy of it first): every session
     that starts in a pys window gets a few lines on how to behave there (instructions.py)

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
from . import claude, instructions, listing

ON_CHANGE_LINES = ('\n# added by `pys setup`: a snapshot at each change of the windows and '
                   'before a kill,\n# for `pys-sessions restore` after pys died\n'
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
    p = config.path()
    if not p.is_file():
        if do:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(doctor.SAMPLE.read_bytes() + ON_CHANGE_LINES.encode())
        return f"config: write the sample, with ON_CHANGE on, to {p}"
    if config.load_config().on_change:
        return f"config: {p} has an ON_CHANGE already; left as it is"
    if do:
        kept = keep_copy(p)
        with p.open("a", encoding="utf-8") as f:
            f.write(ON_CHANGE_LINES)
        return f"config: kept a copy as {kept.name}, added ON_CHANGE to {p}"
    return f"config: add ON_CHANGE (pys-sessions save) to the end of {p}, a copy kept first"


def step_skills(do: bool) -> str:
    dest = claude.home() / "skills"
    if not do:
        return f"skills: link pys-drive and pys-restore into {dest}"
    listing.cmd_skills(type("A", (), {"install": True, "dir": str(dest)})())
    return f"skills: done (above) in {dest}"


def step_hook(do: bool) -> str:
    p = claude.home() / "settings.json"
    want = instructions.hook_command()
    try:
        s = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError as e:
        return f"hook: ⚠ {p} is not valid JSON ({e}); left as it is, fix it and run again"
    groups = s.setdefault("hooks", {}).setdefault("SessionStart", [])
    ours = [(g, h) for g in groups for h in g.get("hooks", [])
            if instructions.MARK in str(h.get("command", ""))]
    if any(h.get("command") == want for _, h in ours):
        return f"hook: SessionStart in {p} runs this install already"
    what = "point the SessionStart hook at this install" if ours else "add a SessionStart hook"
    if not do:
        return f"hook: {what} in {p}: {want}"
    kept = f" (a copy kept as {keep_copy(p).name})" if p.is_file() else ""
    for g, h in ours:                    # another install's: this one replaces it
        g["hooks"].remove(h)
    s["hooks"]["SessionStart"] = [g for g in groups if g.get("hooks")] + \
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
              "into it,\n    and `uv cache clean` empties it. `uv tool install py-screen-mux`, "
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
