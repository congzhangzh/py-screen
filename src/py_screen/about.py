"""What this pys is and who made it: the quiet line at the top right of the help and of
the settings panel.

The version and commit are this running code's: a release has its version in the package
metadata and its commit in _build.py, which release.yml writes into the sdist (so every
wheel built from it has it); a checkout (an editable install, a worktree) asks git, so
it says `dev` and its commit, never the placeholder version pyproject.toml carries.
Read once per process: a brain shows the code it runs, even after the checkout moved.
"""

from __future__ import annotations

import functools
import importlib.metadata
import subprocess
from pathlib import Path

MOTTO = "many windows, one Brahman"
SLOGAN = f"{MOTTO} · by CZ, an engineer near the magnet"


def _git(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


@functools.cache
def version() -> str:
    """`20261113.3 · 1a2b3c4` for a release, or a clean checkout of its tag; `dev · 1a2b3c4`
    from any other checkout (+ `, changed` when it has uncommitted changes, never with a
    release's name); `dev` when even git can't say."""
    repo = Path(__file__).resolve().parents[2]
    if (repo / ".git").exists():                  # a checkout: a dir, or a worktree's file
        sha = _git(repo, "rev-parse", "--short=7", "HEAD")
        if sha:
            dirty = bool(_git(repo, "status", "--porcelain", "--untracked-files=no"))
            # a release's name only for its exact, unchanged code: edits on a tag are dev
            tag = "" if dirty else _git(repo, "describe", "--tags", "--exact-match", "HEAD")
            return f"{tag or 'dev'} · {sha}" + (", changed" if dirty else "")
        return "dev"
    try:
        v = importlib.metadata.version("py-screen")
    except importlib.metadata.PackageNotFoundError:
        v = "dev"
    try:
        from ._build import COMMIT                # written by release.yml
    except ImportError:
        return v
    return f"{v} · {COMMIT[:7]}"


def line() -> str:
    """For beside a "py-screen …" title: the version and the slogan."""
    return f"{version()} — {SLOGAN}"
