"""pys-agents: which pys window runs which Claude Code session, and how it is doing.

  pys-agents [--json]                 every running claude: its window, title, state,
                                      context (tokens the last call held), compactions,
                                      model, name and folder
  pys-agents skills [--install [--dir DIR]]
                                      the agent skills that come with pys; --install links
                                      them into DIR (default ~/.claude/skills)

A claude belongs to a window when it runs under the window's shell; one outside pys shows
window `-`. Context and compactions come from its transcript, not from its screen, so they
need no status line of any kind.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import claude, procs

SKILLS = Path(__file__).parent / "skills"


def windows() -> list[dict]:
    try:
        return json.loads(procs.pys("ls", "--json")).get("windows", [])
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        return []           # no pys server: every claude is outside pys


def agents() -> list[dict]:
    wins = windows()
    kids = procs.children_map()
    where: dict[int, dict] = {}
    for w in wins:
        for p in [w["pid"], *procs.descendants(w["pid"], kids)]:   # `pys new claude`: itself
            where.setdefault(p, w)
    out = []
    for pid, s in sorted(claude.live_sessions().items()):
        w = where.get(pid)
        t = claude.transcript(s.get("sessionId", ""))
        ctx = claude.context(t)
        out.append({
            "window": w["wid"] if w else None,
            "title": (w.get("name") if w else None),
            "titled_by_it": bool(w and w.get("agent_owner") == pid),
            "pid": pid, "session": s.get("sessionId"), "name": s.get("name"),
            "state": s.get("status"), "cwd": s.get("cwd"),
            "context_tokens": ctx.get("tokens"), "model": ctx.get("model"),
            "compactions": claude.compactions(t),
        })
    out.sort(key=lambda a: (a["window"] is None, a["window"] or 0, a["pid"]))
    return out


def short_path(p: str | None) -> str:
    if not p:
        return "-"
    home = str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def kilo(n: int | None) -> str:
    if n is None:
        return "-"
    return f"{n / 1000:.0f}k" if n < 1_000_000 else f"{n / 1_000_000:.2f}M"


def cmd_list(a) -> int:
    rows = agents()
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    if not rows:
        print("no Claude Code session is running")
        return 0
    head = ("WIN", "TITLE", "STATE", "CTX", "COMPACT", "MODEL", "NAME", "FOLDER")
    table = [head] + [(
        "-" if r["window"] is None else str(r["window"]),
        (r["title"] or "-")[:24], r["state"] or "-", kilo(r["context_tokens"]),
        "-" if r["compactions"] is None else str(r["compactions"]),
        (r["model"] or "-").removeprefix("claude-"), (r["name"] or "-")[:20],
        short_path(r["cwd"])) for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(head) - 1)]
    for row in table:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)) + "  " + row[-1])
    return 0


def cmd_skills(a) -> int:
    names = sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))
    if not a.install:
        for n in names:
            print(f"{n:<14} {SKILLS / n}")
        print("install with: pys-agents skills --install   (links them into ~/.claude/skills)")
        return 0
    dest = Path(os.path.expanduser(a.dir))
    dest.mkdir(parents=True, exist_ok=True)
    for n in names:
        src, link = SKILLS / n, dest / n
        if link.is_symlink() and link.resolve() == src.resolve():
            print(f"  {n}: already linked")
        elif link.exists() or link.is_symlink():
            # never replace a skill of the same name: it may be the user's own version
            print(f"  {n}: {link} exists and is not ours; left as it is")
        else:
            link.symlink_to(src)
            print(f"  {n}: linked {link} → {src}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pys-agents", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="one object per claude")
    ap.set_defaults(f=cmd_list)
    sp = ap.add_subparsers(dest="cmd")
    p = sp.add_parser("ls", help="the default: list the agents")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_list)
    p = sp.add_parser("skills", help="the agent skills that come with pys")
    p.add_argument("--install", action="store_true")
    p.add_argument("--dir", default="~/.claude/skills")
    p.set_defaults(f=cmd_skills)
    a = ap.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
