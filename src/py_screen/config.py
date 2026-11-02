"""User config: `~/.config/pys/config.py` (or $PYS_CONFIG), plain Python.

Read by every brain start, so `pys reload` (C-a R) applies an edit. Example:

    WIDGETS = [
        cmd(["uptime"], every=60),           # a command's first output line
        cmd(["git", "-C", "{cwd}", "branch", "--show-current"], every=10),  # {cwd}: focused dir
        load(),                              # 1-minute load average
        clock("%m-%d %H:%M"),
    ]
    BIND = {
        "T": ("top", ["htop"]),              # C-a T opens a window running htop
    }
    PREFIX = "C-a"                            # default: C-] inside GNU screen, else C-a
    MOUSE = True                              # click to focus, drag borders, wheel (default);
                                              # False: the mouse is pys's only while the
                                              # focused app asks for it, so plain drags
                                              # select text in the terminal again
    SCROLLBACK = 5000                         # rows of history per window (the wheel and
                                              # Shift-PgUp scroll it); 0 = none
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import platform

_CTRL = re.compile(r"[\x00-\x1f\x7f]|\x1b\[[0-9;?]*[A-Za-z]")


@dataclass
class Widget:
    every: float = 5.0
    text: str = ""

    async def update(self, ctx: dict) -> str:
        return ""


@dataclass
class Cmd(Widget):
    argv: list[str] = field(default_factory=list)
    timeout: float = 3.0

    async def update(self, ctx: dict) -> str:
        # "{cwd}" in an argument is the focused window's directory.
        argv = [a.replace("{cwd}", ctx.get("cwd") or "") for a in self.argv]
        if any("{cwd}" in a for a in self.argv) and not ctx.get("cwd"):
            return ""
        try:
            p = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL)
        except OSError:
            return "?"
        try:
            out, _ = await asyncio.wait_for(p.communicate(), self.timeout)
        except asyncio.TimeoutError:
            p.kill()                              # never leave a stuck helper behind
            await p.wait()
            return "?"
        line = out.decode(errors="replace").splitlines()[0] if out.strip() else ""
        return _CTRL.sub("", line).strip()


@dataclass
class Load(Widget):
    async def update(self, ctx: dict) -> str:
        la = platform.loadavg()                   # None on Windows: no load average
        return "" if la is None else f"{la:.2f}"


@dataclass
class Clock(Widget):
    fmt: str = "%m-%d %H:%M"

    async def update(self, ctx: dict) -> str:
        return time.strftime(self.fmt)


def cmd(argv: list[str] | str, every: float = 5.0, timeout: float = 3.0) -> Cmd:
    argv = [argv] if isinstance(argv, str) else list(argv)
    argv[0] = os.path.expanduser(argv[0])
    return Cmd(every=every, argv=argv, timeout=timeout)


def load(every: float = 5.0) -> Load:
    return Load(every=every)


def clock(fmt: str = "%m-%d %H:%M") -> Clock:
    return Clock(every=1.0, fmt=fmt)


SCROLLBACK = 5000      # rows a window keeps; a row costs ~36 bytes per cell it shows


@dataclass
class Config:
    widgets: list[Widget]
    bind: dict[str, tuple[str, list[str]]]
    prefix: str | None
    error: str | None = None
    mouse: bool = True
    scrollback: int = SCROLLBACK


def default() -> Config:
    return Config([load(), clock()], {}, None)


def path() -> Path:
    if p := os.environ.get("PYS_CONFIG"):
        return Path(p)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "pys" / "config.py"


def load_config() -> Config:
    p = path()
    cfg = default()
    if not p.is_file():
        return cfg
    ns: dict = {"cmd": cmd, "load": load, "clock": clock}
    try:
        exec(compile(p.read_text(), str(p), "exec"), ns)
    except Exception as e:  # a broken config must not stop the brain
        print(f"pys: config {p}: {e!r}", file=sys.stderr)
        cfg.error = f"config error: {type(e).__name__}"
        return cfg
    if "WIDGETS" in ns:
        cfg.widgets = list(ns["WIDGETS"])
    cfg.bind = {k: (v[0], list(v[1])) for k, v in ns.get("BIND", {}).items()}
    cfg.prefix = ns.get("PREFIX")
    cfg.mouse = bool(ns.get("MOUSE", True))
    try:
        cfg.scrollback = max(0, int(ns.get("SCROLLBACK", SCROLLBACK)))
    except (TypeError, ValueError):
        cfg.error = "config error: SCROLLBACK"
    return cfg


def prefix_byte(name: str) -> int:
    """'C-a' / '^A' -> 0x01; 'C-]' -> 0x1d."""
    name = name.strip()
    ch = name[-1]
    if name.lower().startswith(("c-", "^")):
        return ord(ch.upper()) & 0x1F if ch.isalpha() else ord(ch) & 0x1F
    return ord(ch)
