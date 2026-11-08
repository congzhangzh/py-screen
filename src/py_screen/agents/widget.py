"""Status-line widgets for agents. In ~/.config/pys/config.py:

    from py_screen.agents.widget import branch, caption
    WIDGETS = [branch(), load(), clock()]
    CAPTION = caption()      # the caption line: folder (or the agent's repo) + branch

branch() shows the git state of the focused window: `main`, `fix-x wt 2 mod ↑1`, and
for a window with a Claude Code session in it, `✎` and the repo it last wrote in (its
transcript), not the folder it started in (see `pys-agents where`). Empty outside a repo.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import claude, gitinfo, procs


def look(ctx: dict) -> tuple[str | None, bool, str]:
    """(folder, by_agent, git text) for one window (blocking: git and /proc; run off the
    loop): the folder is where an agent in it last wrote inside a repo when there is one
    (by_agent), else the window's own; the git text is `fix-x wt 2 mod ↑1`, or empty
    outside a repo."""
    folder, by_agent = ctx.get("cwd"), False
    pid = ctx.get("pid")
    if pid:
        try:
            tree = [pid, *procs.descendants(pid, procs.children_map())]
        except OSError:                   # no /proc (macOS): the folder alone
            tree = []
        live = claude.live_sessions() if tree else {}
        if s := next((live[p] for p in tree if p in live), None):
            if pl := claude.works_in(s):
                folder, by_agent = pl["top"], True
            else:
                folder = s.get("cwd") or folder
    pl = gitinfo.place(folder)
    if pl is None:
        return folder, by_agent, ""
    text = pl["branch"] or f"@{pl['head'] or '?'}"
    if pl["worktree"]:
        text += " wt"
    st = gitinfo.state(gitinfo.changes(folder))       # "clean ↑2", "1 mod", "-" (failed)
    st = "" if st == "-" else st.removeprefix("clean").strip()
    return folder, by_agent, f"{text} {st}".rstrip()


def describe(ctx: dict) -> str:
    """The status-line text: `✎fix-x wt 2 mod` (✎: the agent's repo), or empty."""
    folder, by_agent, git = look(ctx)
    return ("✎" if by_agent and git else "") + git


def caption_text(ctx: dict) -> str:
    """The caption text: `✎ ~/src/app-fix  fix-x wt 2 mod`, `~/notes` outside a repo."""
    from .listing import short_path
    folder, by_agent, git = look(ctx)
    text = ("✎ " if by_agent else "") + short_path(folder)
    return f"{text}  {git}" if git else text


@dataclass
class Branch:
    every: float = 5.0
    text: str = ""

    async def update(self, ctx: dict) -> str:
        return await asyncio.to_thread(describe, ctx)


def branch(every: float = 5.0) -> Branch:
    return Branch(every=every)


@dataclass
class Caption:
    every: float = 5.0

    async def update(self, ctx: dict) -> str:
        return await asyncio.to_thread(caption_text, ctx)


def caption(every: float = 5.0) -> Caption:
    """CAPTION = caption(): after each shown window's name, where the work is: the folder,
    or for an agent the repo it last wrote in (✎), and its git branch and changes."""
    return Caption(every=every)
