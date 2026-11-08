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
import os
from dataclasses import dataclass

from . import claude, gitinfo, procs


def look(ctx: dict) -> tuple[str | None, str | None, bool, str]:
    """(start, folder, by_agent, git text) for one window (blocking: git and /proc; run off
    the loop). start: where an agent in it started (its Bash goes back there after every
    command, and a resume runs there), else the window's folder. folder: where the agent
    last wrote inside a repo when there is one (by_agent), else start. git text: `fix-x wt
    2 mod ↑1` for folder, or empty outside a repo."""
    start = ctx.get("cwd")
    folder, by_agent = start, False
    pid = ctx.get("pid")
    if pid:
        try:
            tree = [pid, *procs.descendants(pid, procs.children_map())]
        except OSError:                   # no /proc (macOS): the folder alone
            tree = []
        live = claude.live_sessions() if tree else {}
        if s := next((live[p] for p in tree if p in live), None):
            start = folder = s.get("cwd") or start
            if pl := claude.works_in(s):
                folder, by_agent = pl["top"], True
    pl = gitinfo.place(folder)
    if pl is None:
        return start, folder, by_agent, ""
    text = pl["branch"] or f"@{pl['head'] or '?'}"
    if pl["worktree"]:
        text += " wt"
    st = gitinfo.state(gitinfo.changes(folder))       # "clean ↑2", "1 mod", "-" (failed)
    st = "" if st == "-" else st.removeprefix("clean").strip()
    return start, folder, by_agent, f"{text} {st}".rstrip()


def describe(ctx: dict) -> str:
    """The status-line text: `✎fix-x wt 2 mod` (✎: the agent's repo), or empty."""
    start, folder, by_agent, git = look(ctx)
    return ("✎" if by_agent and git else "") + git


def caption_text(ctx: dict) -> str:
    """The caption text: the start folder first, then, when an agent works elsewhere, ✎ and
    that repo (only its name when it sits beside the start folder, as worktrees do), then
    the git state: `~/src/app ✎ app-fix  fix-x wt 2 mod`; `~/notes` outside a repo."""
    from .listing import short_path
    start, folder, by_agent, git = look(ctx)
    text = short_path(start)
    if by_agent and folder and os.path.realpath(folder) != os.path.realpath(start or ""):
        near = start and os.path.dirname(os.path.realpath(folder)) == os.path.dirname(os.path.realpath(start))
        text += " ✎ " + (os.path.basename(folder) if near else short_path(folder))
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
