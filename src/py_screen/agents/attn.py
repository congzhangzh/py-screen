"""The Claude Code hook that marks a pys window when the claude in it waits for you.

`pys setup` registers it for four events, with the Python of the install that ran setup:

    Notification       -> "ask": Claude can't go on without you (a permission prompt, a
                          question); the tab turns red. Its idle reminder is left out: the
                          turn's end marked the window already, and once you looked, it
                          stays seen.
    Stop               -> "done": the turn ended and Claude waits; the tab turns green
    UserPromptSubmit   -> cleared: you answered
    SessionEnd         -> cleared: no claude waits there any more

The window is $PYS_WINDOW, of the session in $PYS_SESSION (both from the keeper, inherited
by claude and its hooks). Outside pys it does nothing. The window you are looking at is
never marked (the brain decides that: Brain.attend), and switching to a marked window
clears it.

A hook must never get in Claude's way: it prints nothing, always exits 0, and gives up
after a few seconds if the server doesn't answer.

    python -m py_screen.agents.attn          the hook (reads the event on stdin)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

MARK = "py_screen.agents.attn"             # how setup knows the hook is ours
EVENTS = ("Notification", "Stop", "UserPromptSubmit", "SessionEnd")
TIMEOUT = 3.0


def level_of(event: dict) -> tuple[str | None, str] | None:
    """What the hook event means for the window: (level, why), level None = clear it;
    None: leave the window as it is."""
    name = event.get("hook_event_name")
    if name == "Notification":
        kind = event.get("notification_type")
        if kind in ("idle_prompt", "auth_success"):
            return None
        return "ask", str(event.get("message") or "Claude needs you")
    if name == "Stop":
        return "done", "turn ended"
    if name in ("UserPromptSubmit", "SessionEnd"):
        return None, ""
    return None


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return 0
    wid = os.environ.get("PYS_WINDOW")
    what = level_of(event) if isinstance(event, dict) else None
    if wid is None or not wid.isdigit() or what is None:
        return 0
    if os.environ.get("PYS_SESSION"):          # the session this window is in, whatever
        os.environ["PYS_DIR"] = os.environ["PYS_SESSION"]   # PYS_DIR says
    from .. import proto
    level, why = what
    try:
        asyncio.run(asyncio.wait_for(
            proto.rpc({"cmd": "attn", "wid": int(wid), "level": level, "why": why}), TIMEOUT))
    except Exception:                          # no server, an old one, a timeout: never
        pass                                   # Claude's problem
    return 0


if __name__ == "__main__":
    sys.exit(main())
