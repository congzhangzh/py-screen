"""Status-line widgets for agents. In ~/.config/pys/config.py:

    from py_screen.agents.widget import branch
    WIDGETS = [branch(), load(), clock()]

branch() shows the git state of the focused window: `main`, `fix-x wt 2 mod ↑1`, and
for a window with a Claude Code session in it, `✎` and the repo it last wrote in (its
transcript), not the folder it started in (see `pys-agents where`). Empty outside a repo.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import claude, gitinfo, procs


def describe(ctx: dict) -> str:
    """The text for one focused window (blocking: git and /proc; run off the loop)."""
    folder, mark = ctx.get("cwd"), ""
    pid = ctx.get("pid")
    if pid:
        try:
            tree = [pid, *procs.descendants(pid, procs.children_map())]
        except OSError:                   # no /proc (macOS): the folder alone
            tree = []
        live = claude.live_sessions() if tree else {}
        if s := next((live[p] for p in tree if p in live), None):
            if pl := claude.works_in(s):
                folder, mark = pl["top"], "✎"
            else:
                folder = s.get("cwd") or folder
    pl = gitinfo.place(folder)
    if pl is None:
        return ""
    text = mark + (pl["branch"] or f"@{pl['head'] or '?'}")
    if pl["worktree"]:
        text += " wt"
    st = gitinfo.state(gitinfo.changes(folder))       # "clean ↑2", "1 mod", "-" (failed)
    st = "" if st == "-" else st.removeprefix("clean").strip()
    return f"{text} {st}".rstrip()


@dataclass
class Branch:
    every: float = 5.0
    text: str = ""

    async def update(self, ctx: dict) -> str:
        return await asyncio.to_thread(describe, ctx)


def branch(every: float = 5.0) -> Branch:
    return Branch(every=every)
