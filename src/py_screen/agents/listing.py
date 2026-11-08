"""pys-agents: which pys window runs which Claude Code session, and how it is doing.

  pys-agents [--json]                 every running claude: its window, title, state,
                                      context (tokens the last call held), compactions,
                                      model, name and folder
  pys-agents where [--json]           every window: its folder, the git branch @HEAD there
                                      (and whether it is a linked worktree), its changes
                                      against the upstream, and the agent in it if any
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

from concurrent.futures import ThreadPoolExecutor

from . import claude, gitinfo, procs

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


def where() -> list[dict]:
    """Every window with its folder and git state; a claude's own folder when one runs in
    it (its shell may sit elsewhere). The git calls run side by side: a slow repo costs its
    own timeout, not the sum."""
    wins = windows()
    kids = procs.children_map()
    live = claude.live_sessions()
    rows = []
    for w in wins:
        tree = [w["pid"], *procs.descendants(w["pid"], kids)]
        pid = next((p for p in tree if p in live), None)
        s = live.get(pid) if pid else None
        cwd = (s or {}).get("cwd") or procs.cwd(w["pid"]) or w.get("cwd")
        rows.append({"window": w["wid"], "title": w.get("name"), "cwd": cwd, "session": s,
                     "agent": {"kind": "claude", "pid": pid, "name": s.get("name"),
                               "session": s.get("sessionId"), "state": s.get("status")}
                     if s else None})
    def works_in(r: dict) -> str:
        """An agent works where it last wrote inside a repo (its own words: the transcript);
        else, and for a plain window, its folder."""
        pl = claude.works_in(s) if (s := r.pop("session")) else None
        r["works_in"] = pl and pl["top"]
        return r["works_in"] or r["cwd"]

    with ThreadPoolExecutor(max_workers=8) as ex:
        folders = list(ex.map(works_in, rows))
        places = list(ex.map(gitinfo.place, folders))
        changes = list(ex.map(gitinfo.changes, folders))
    for r, pl, ch in zip(rows, places, changes):
        r["git"] = pl and {**pl, **(ch or {})}
    return rows


def table(head: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    """Columns padded to their widest cell; the last one is left as it is."""
    t = [head, *rows]
    widths = [max(len(r[i]) for r in t) for i in range(len(head) - 1)]
    for r in t:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1])


def cmd_where(a) -> int:
    rows = where()
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    if not rows:
        print("no pys server, or no window")
        return 0
    out = []
    for r in rows:
        g, ag = r["git"], r["agent"]
        if g:
            br = (g["branch"] or "detached") + (f" @{g['head']}" if g.get("head") else "")
            br += " (wt)" if g["worktree"] else ""
        folder = ("✎ " + short_path(r["works_in"])) if r.get("works_in") else short_path(r["cwd"])
        out.append((str(r["window"]), (r["title"] or "-")[:24], folder,
                    br if g else "-", gitinfo.state(g if g and "changed" in g else None),
                    f"claude {ag['name'] or ag['session'][:8]} ({ag['state'] or '-'})" if ag else "-"))
    table(("WIN", "TITLE", "FOLDER", "BRANCH", "STATE", "AGENT"), out)
    if any(r.get("works_in") for r in rows):
        print("✎ = the repo of the agent's latest writes (its transcript), not the folder it started in")
    if any(r["git"] and r["git"]["worktree"] for r in rows):
        print("(wt) = a linked worktree (git worktree add) of that repo")
    return 0


def short_path(p: str | None) -> str:
    from ..config import home_path
    return home_path(p) or "-"


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
    table(("WIN", "TITLE", "STATE", "CTX", "COMPACT", "MODEL", "NAME", "FOLDER"), [(
        "-" if r["window"] is None else str(r["window"]),
        (r["title"] or "-")[:24], r["state"] or "-", kilo(r["context_tokens"]),
        "-" if r["compactions"] is None else str(r["compactions"]),
        (r["model"] or "-").removeprefix("claude-"), (r["name"] or "-")[:20],
        short_path(r["cwd"])) for r in rows])
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
        if is_link(link) and os.path.realpath(link) == os.path.realpath(src):
            print(f"  {n}: already linked")
        elif link.exists() or is_link(link):
            # never replace a skill of the same name: it may be the user's own version
            print(f"  {n}: {link} exists and is not ours; left as it is")
        else:
            how = make_link(src, link)
            print(f"  {n}: linked {link} → {src}{how}")
    return 0


def is_link(p: Path) -> bool:
    return p.is_symlink() or p.is_junction()


def make_link(src: Path, link: Path) -> str:
    """A symlink; on Windows, where one needs Developer Mode or an administrator, a directory
    junction when it is refused (any user may make one, and it reads the same)."""
    try:
        link.symlink_to(src, target_is_directory=True)
        return ""
    except OSError:
        if sys.platform != "win32":
            raise
    import _winapi
    _winapi.CreateJunction(str(src), str(link))
    return " (a junction)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pys-agents", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="one object per claude")
    ap.set_defaults(f=cmd_list)
    sp = ap.add_subparsers(dest="cmd")
    p = sp.add_parser("ls", help="the default: list the agents")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_list)
    p = sp.add_parser("where", help="every window: folder, git branch, worktree, changes, agent")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_where)
    p = sp.add_parser("skills", help="the agent skills that come with pys")
    p.add_argument("--install", action="store_true")
    p.add_argument("--dir", default="~/.claude/skills")
    p.set_defaults(f=cmd_skills)
    a = ap.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
