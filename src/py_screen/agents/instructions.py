"""The instructions every Claude Code session in a pys window gets, from a SessionStart hook.

A skill is read when its description matches the task; these few lines are in the context of
every session that starts (or resumes, or is cleared or compacted) inside pys, and of none
outside it: the hook prints nothing when $PYS_WINDOW is not set. `pys setup` registers it in
Claude Code's settings.json, with the Python of the install that ran setup.

    python -m py_screen.agents.instructions          the hook (reads the event on stdin)
    python -m py_screen.agents.instructions --show   print the text, as a session gets it

The text names the commands as this install runs them: `pys` when that is on PATH, else this
install's own Python (`… -m py_screen`), so it is right for uvx and a bundle off PATH too.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

MARK = "py_screen.agents.instructions"     # how setup knows the hook is ours


def _py() -> str:
    p = Path(sys.executable)
    return p.as_posix() if os.name == "nt" else str(p)   # C:/…: fine in Git Bash and cmd


def command_for(name: str, module: str) -> str:
    """How an agent's shell runs one of pys's commands from here."""
    if shutil.which(name):
        return name
    return f'"{_py()}" -m {module}'


def hook_command() -> str:
    """What settings.json runs: this install's Python, whatever is on PATH."""
    return f'"{_py()}" -m {MARK}'


def text(wid: str) -> str:
    pys = command_for("pys", "py_screen")
    agents = command_for("pys-agents", "py_screen.agents.listing")
    return f"""You run inside py-screen (pys), window {wid}, a terminal workspace the user shares with other windows and often other agents.
- Name your window at the start and when your work changes, short (about 24 characters): {pys} title --owner $PPID "<role> <what you work on>". In Claude Code's Bash tool $PPID is this session, so the title ends with it.
- {agents} lists the agents and their windows; `{agents} where` each window's folder and git branch. Look before you work in a repo another window works in.
- `{pys} capture <wid>` reads a window. Type into one only with the user's word and only when it is idle: the text first, then Enter on its own (`{pys} send --enter <wid> ""`).
- Never `{pys} kill`, prefix Q, or a pkill that could match pys: they end every window, the user's and other agents' too.
- The pys-drive skill has the details; pys-restore brings windows back after pys died."""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from .. import utf8_pipes
    utf8_pipes()
    wid = os.environ.get("PYS_WINDOW")
    if "--show" in argv:
        print(text(wid or "<N>"))
        return 0
    if not wid:                       # not in pys: nothing to add
        return 0
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        event = {}
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event.get("hook_event_name", "SessionStart"),
        "additionalContext": text(wid)}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
