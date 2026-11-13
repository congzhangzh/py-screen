"""The settings panel's file: what you change in the panel (prefix o, or a click on " pys ")
is kept in config.d/50-settings.py, a drop-in pys owns. Your own config.py is never
touched. config.d runs after config.py, so a value here wins over the same key there; the
panel says where each value comes from.

The file holds plain assignments (`BUTTONS = False`). pys's config loader runs it as Python,
like every drop-in, so a hand edit counts there; the panel reads it back with ast (only its
own keys, with a value it offers) and rewrites it whole, so its next change keeps only those.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from . import config

FILE = "50-settings.py"

# What the panel shows, in order: (config key, Config field, what it is, the values).
ROWS: list[tuple[str, str, str, tuple]] = [
    ("BUTTONS", "buttons", 'the + ▾ ? buttons after " pys "', (True, False)),
    ("STATUS_LINES", "status_lines", "status rows the window tabs may take", (1, 2, 3)),
    ("SHOW_WIDGETS", "show_widgets", "the widgets on the right of the status line",
     (True, False)),
    ("MOUSE", "mouse", "the mouse: clicks, drags, the wheel", (True, False)),
]
KEYS = [r[0] for r in ROWS]

HEADER = '''\
# Written by pys's settings panel (prefix o, or a click on " pys "): what you set there.
# It runs after config.py, so a value here wins over the same key in config.py. Edit it
# by hand if you like (plain KEY = value lines), or delete it to go back to config.py.
'''


def path() -> Path:
    return config.drop_in_dir() / FILE


def read(p: Path | None = None) -> dict:
    """The panel's values in the file: {key: value}, only the keys the panel knows, with a
    value it offers. A missing or broken file is {}."""
    p = p or path()
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    allowed = {r[0]: r[3] for r in ROWS}
    out = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)):
            key, value = node.targets[0].id, node.value.value
            if key in allowed and any(value == v and type(value) is type(v) for v in allowed[key]):
                out[key] = value
    return out


def write(values: dict, p: Path | None = None) -> Path:
    """Write the panel's values, in the panel's order, whole and atomically (a brain that
    starts while it is written reads the old file or the new one, never half)."""
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k} = {values[k]!r}\n" for k in KEYS if k in values)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(HEADER + body, encoding="utf-8")
    os.replace(tmp, p)
    return p


def step(key: str, value) -> object:
    """The next value of key after value, as a click or Enter on its row gives it."""
    values = next(r[3] for r in ROWS if r[0] == key)
    i = values.index(value) if value in values else -1
    return values[(i + 1) % len(values)]


def files() -> list[tuple[str, str, str, str]]:
    """What pys has on this machine, for the panel's "Files pys uses": (state, kind, where,
    what it is), state ✓ there, ✗ missing, ⚠ there but another install's. The same tests
    `pys setup` uses (its HOOKS, hook_command, pys_skill_target), so the two never disagree.
    Read when the panel opens, not on each frame."""
    import json
    import sys

    from .agents import claude, listing, setup
    home = config.home_path
    out: list[tuple[str, str, str, str]] = []
    p = config.path()
    out.append(("✓" if p.is_file() else "✗", "config", home(str(p)),
                "yours; pys never edits it" if p.is_file() else
                "none: pys runs on its defaults (pys doctor --init writes the sample)"))
    d = config.drop_in_dir()
    for f in sorted(d.glob("*.py")) if d.is_dir() else []:
        by = ("written by pys setup (ON_CHANGE)" if f.name == setup.DROP_IN else
              "written by this panel" if f.name == FILE else "a drop-in of yours")
        out.append(("✓", "drop-in", home(str(f)), by))
    dest = claude.home() / "skills"
    for name in sorted(q.parent.name for q in listing.SKILLS.glob("*/SKILL.md")):
        link, src = dest / name, listing.SKILLS / name
        if listing.is_link(link) and os.path.realpath(link) == os.path.realpath(src):
            out.append(("✓", "skill", f"{home(str(link))} → {home(str(src))}", "linked by pys setup"))
        elif other := listing.pys_skill_target(link, name):
            out.append(("⚠", "skill", f"{home(str(link))} → {home(other)}", "another pys install's"))
        elif link.exists():
            out.append(("⚠", "skill", home(str(link)), "yours, not pys's: left as it is"))
        else:
            out.append(("✗", "skill", home(str(link)), "not linked: pys setup links it"))
    sp = claude.home() / "settings.json"
    try:
        s = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
    except (OSError, ValueError):
        s = None
    if s is None:
        out.append(("⚠", "hooks", home(str(sp)), "not valid JSON: pys setup leaves it alone"))
    else:
        hooks = s.get("hooks", {}) if isinstance(s, dict) else {}
        mine = other = 0
        for event, mark, _ in setup.HOOKS:
            cmds = [str(h.get("command", "")) for g in hooks.get(event, []) if isinstance(g, dict)
                    for h in g.get("hooks", []) if isinstance(h, dict)]
            if setup.hook_command(mark) in cmds:
                mine += 1
            elif any(mark in c for c in cmds):
                other += 1
        n = len(setup.HOOKS)
        state = "✓" if mine == n else "⚠" if mine + other == n else "✗"
        what = (f"{mine}/{n} pys hooks run this pys" +
                (f", {other} another pys" if other else "") +
                ("" if mine + other == n else "; pys setup adds them"))
        out.append((state, "hooks", home(str(sp)), what))
    pkg = Path(__file__).resolve().parent
    out.append(("✓", "program", home(str(pkg)), "this pys's code"))
    out.append(("✓", "python", home(sys.executable), "runs it"))
    return out
