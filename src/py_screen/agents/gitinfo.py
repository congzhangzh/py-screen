"""What git says about a window's folder: its repo, branch, HEAD, worktree, changes.

Two depths, because they cost differently:

- `place(cwd)`: `git rev-parse` and `symbolic-ref` only, which read a few files under .git
  and never the working tree: fast in any repo, so `pys-sessions save` can run it for every
  window inside the kill hook.
- `changes(cwd)`: `git status`, which stats every tracked file: for the live view
  (`pys-agents where`) only.

Every call runs with `--no-optional-locks` (a status must never take the index lock while
the user commits), no prompt, and a timeout; a folder that is no repo, or a git that fails or
is missing, gives None.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

TIMEOUT = 3.0
ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=TIMEOUT,
                           env={**os.environ, **ENV})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def place(cwd: str | None) -> dict | None:
    """{"top", "repo", "common", "worktree", "branch", "head"}: the work tree's root, the
    repo's name (the main checkout's folder), its shared .git dir, whether this is a linked
    worktree (`git worktree add`),
    the branch (None when detached) and HEAD's short sha (None before the first commit)."""
    if not cwd or not os.path.isdir(cwd):
        return None
    out = git(cwd, "rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir")
    if out is None or len(lines := out.splitlines()) < 3:
        return None
    top, gitdir, common = lines[:3]
    common_p = Path(common) if os.path.isabs(common) else Path(cwd, common)
    common_p, gitdir_p = common_p.resolve(), Path(gitdir).resolve()
    repo = common_p.parent.name if common_p.name == ".git" else common_p.name.removesuffix(".git")
    branch = (git(cwd, "symbolic-ref", "-q", "--short", "HEAD") or "").strip() or None
    head = (git(cwd, "rev-parse", "-q", "--short", "--verify", "HEAD") or "").strip() or None
    return {"top": top, "repo": repo, "common": str(common_p), "worktree": gitdir_p != common_p,
            "branch": branch, "head": head}


def changes(cwd: str | None) -> dict | None:
    """{"changed", "untracked", "ahead", "behind"} from `git status --porcelain=v2 --branch`;
    ahead/behind are None without an upstream."""
    if not cwd or not os.path.isdir(cwd):
        return None
    out = git(cwd, "status", "--porcelain=v2", "--branch")
    if out is None:
        return None
    c = {"changed": 0, "untracked": 0, "ahead": None, "behind": None}
    for line in out.splitlines():
        if line.startswith("# branch.ab "):
            a, b = line.split()[2:4]
            c["ahead"], c["behind"] = int(a), -int(b)
        elif line[:2] in ("1 ", "2 ", "u "):
            c["changed"] += 1
        elif line.startswith("? "):
            c["untracked"] += 1
    return c


def describe(p: dict | None) -> str:
    """One line for show/restore: `worktree of py-screen, branch x @0fd1d4a`."""
    if not p:
        return ""
    what = f"worktree of {p['repo']}" if p.get("worktree") else f"repo {p['repo']}"
    at = f"branch {p['branch']}" if p.get("branch") else "detached"
    return f"{what}, {at}" + (f" @{p['head']}" if p.get("head") else "")


def state(c: dict | None) -> str:
    """`clean`, `2 mod 4 new`, plus `↑3 ↓1` against the upstream."""
    if c is None:
        return "-"
    parts = []
    if c["changed"]:
        parts.append(f"{c['changed']} mod")
    if c["untracked"]:
        parts.append(f"{c['untracked']} new")
    s = " ".join(parts) or "clean"
    if c["ahead"]:
        s += f" ↑{c['ahead']}"
    if c["behind"]:
        s += f" ↓{c['behind']}"
    return s
