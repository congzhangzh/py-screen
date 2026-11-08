"""Claude Code's own files, read defensively (they are not a documented interface).

- `<home>/sessions/<pid>.json`: one per running claude: `sessionId`, `cwd`, `name`, `status`
- `<home>/projects/<folder>/<sessionId>.jsonl`: the transcript; an assistant line carries
  `message.usage` (the tokens the model saw on that call) and `message.model`; a compaction
  writes a `{"type": "system", "subtype": "compact_boundary"}` line

`<home>` is `$CLAUDE_CONFIG_DIR`, else `~/.claude`.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from . import procs

TAIL = 512 * 1024          # bytes read from a transcript's end to find the last usage


def home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def session_of(pid: int) -> dict | None:
    """The session file of the claude with this pid, or None."""
    try:
        d = json.loads((home() / "sessions" / f"{pid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("sessionId") else None


def live_sessions() -> dict[int, dict]:
    """pid → session file, for every claude still running."""
    out = {}
    for f in (home() / "sessions").glob("*.json"):
        try:
            pid = int(f.stem)
        except ValueError:
            continue
        if procs.alive(pid) and (d := session_of(pid)):
            out[pid] = d
    return out


def transcript(session_id: str) -> Path | None:
    if not session_id:
        return None
    hits = sorted((home() / "projects").glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def context(path: Path | None) -> dict:
    """The last call's context: {"tokens", "model"}, from the newest usage in the tail.
    tokens = input + cache read + cache written: what the model held, not what it cost."""
    if path is None:
        return {}
    try:
        with path.open("rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - TAIL))
            lines = f.read().splitlines()
    except OSError:
        return {}
    for raw in reversed(lines):
        if b'"usage"' not in raw:
            continue
        try:
            msg = json.loads(raw).get("message") or {}
            u = msg["usage"]
            n = sum(int(u.get(k) or 0) for k in
                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        return {"tokens": n, "model": msg.get("model")}
    return {}


def compactions(path: Path | None) -> int | None:
    if path is None:
        return None
    n = 0
    try:
        with path.open("rb") as f:
            for raw in f:
                if b'"compact_boundary"' in raw and b'"subtype"' in raw:
                    n += 1
    except OSError:
        return None
    return n


WRITERS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
CD = re.compile(r"^\s*cd\s+(\S+)\s*(?:&&|;|$)")


def recent_dirs(path: Path | None, cwd: str | None = None) -> list[str]:
    """Where it has been working, newest first, from the transcript's tail: the folder of each
    file it wrote (Write, Edit, NotebookEdit: their `file_path`) and of each Bash command that
    starts with `cd <dir>`. The process's own folder is only where it started: its tools take
    absolute paths anywhere, and its Bash shell goes back there after every command."""
    if path is None:
        return []
    try:
        with path.open("rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - TAIL))
            lines = f.read().splitlines()
    except OSError:
        return []
    out: list[str] = []
    for raw in reversed(lines):
        if b'"tool_use"' not in raw:
            continue
        try:
            content = (json.loads(raw).get("message") or {}).get("content") or []
        except ValueError:
            continue
        for c in reversed(content if isinstance(content, list) else []):
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            inp = c.get("input") or {}
            d = None
            if c.get("name") in WRITERS and isinstance(inp.get("file_path"), str):
                d = os.path.dirname(inp["file_path"])
            elif c.get("name") == "Bash" and (m := CD.match(str(inp.get("command", "")))):
                try:
                    d = shlex.split(m[1])[0]
                except ValueError:
                    d = None
                if d:
                    d = os.path.expanduser(d)
                    if not os.path.isabs(d) and cwd:
                        d = os.path.join(cwd, d)
            if d and d not in out:
                out.append(os.path.normpath(d))
    return out


def works_in(session: dict) -> dict | None:
    """The git place (gitinfo.place) of a claude's latest writes inside a repo, newest first;
    None when it wrote nowhere in a repo yet. One rule for `where`, `save` and the widget."""
    from . import gitinfo
    for d in recent_dirs(transcript(session.get("sessionId") or ""), session.get("cwd")):
        if (pl := gitinfo.place(d)) is not None:
            return pl
    return None
