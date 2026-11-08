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
    ON_CHANGE = ["pys-sessions", "save", "--quiet"]   # run after windows change and
                                              # before `pys kill` / prefix Q ends them
    KILL_HOOK_TIMEOUT = 5                     # seconds a kill waits for ON_CHANGE
    SESSIONS_KEEP_ENV = ["MY_VAR"]            # pys-sessions: variables a resumed claude
                                              # gets back from the one that ran
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

# an escape sequence first: else the bare ESC alone matches and leaves "[31m" behind
_CTRL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[\x00-\x1f\x7f]")


def home_path(p: str | None) -> str:
    """A folder as shown on screen: `~/x` below the home folder, else as it is ("" for None).
    Only for showing: `pys ls --json` and every hook keep the real path."""
    if not p:
        return ""
    home = os.path.expanduser("~")
    if home in ("", "/", os.sep) or not (p == home or p.startswith(home.rstrip(os.sep) + os.sep)):
        return p
    return "~" + p[len(home.rstrip(os.sep)):]


def clean(text: str) -> str:
    """One line of printable text: control characters and escape sequences dropped."""
    return _CTRL.sub("", text.splitlines()[0] if text else "").strip()


@dataclass
class Widget:
    """A status-line item: `update(ctx)` is awaited every `every` seconds and returns its
    text. ctx is the focused window: `cwd` (its folder), `wid`, `pid` (its process); all None
    when no window has focus."""
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
KILL_HOOK_TIMEOUT = 5.0   # seconds `pys kill` / prefix Q wait for ON_CHANGE before ending


@dataclass
class Config:
    widgets: list[Widget]
    bind: dict[str, tuple[str, list[str]]]
    prefix: str | None
    error: str | None = None
    mouse: bool = True
    scrollback: int = SCROLLBACK
    show_widgets: bool = True     # the status line's right side; prefix S toggles it
    on_change: list[str] | None = None   # argv run after windows change and before a kill
    kill_hook_timeout: float = KILL_HOOK_TIMEOUT
    sessions_keep_env: list[str] = field(default_factory=list)   # for pys-sessions only
    caption: object | None = None   # CAPTION: text after each shown window's name; None: folder


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
        exec(compile(p.read_bytes(), str(p), "exec"), ns)  # UTF-8 source, any locale
    except Exception as e:  # a broken config must not stop the brain
        print(f"pys: config {p}: {e!r}", file=sys.stderr)
        cfg.error = f"config error: {type(e).__name__}"
        return cfg
    if "WIDGETS" in ns:
        cfg.widgets = list(ns["WIDGETS"])
    cfg.bind = {k: (v[0], list(v[1])) for k, v in ns.get("BIND", {}).items()}
    cfg.prefix = ns.get("PREFIX")
    cfg.mouse = bool(ns.get("MOUSE", True))
    cfg.show_widgets = bool(ns.get("SHOW_WIDGETS", True))
    try:
        cfg.scrollback = max(0, int(ns.get("SCROLLBACK", SCROLLBACK)))
    except (TypeError, ValueError):
        cfg.error = "config error: SCROLLBACK"
    hook = ns.get("ON_CHANGE")
    if isinstance(hook, str):
        hook = [hook]
    if hook is not None:
        if isinstance(hook, (list, tuple)) and hook and all(isinstance(a, str) for a in hook):
            cfg.on_change = [os.path.expanduser(hook[0]), *hook[1:]]
        else:
            cfg.error = "config error: ON_CHANGE"
    try:
        cfg.kill_hook_timeout = max(0.0, float(ns.get("KILL_HOOK_TIMEOUT", KILL_HOOK_TIMEOUT)))
    except (TypeError, ValueError):
        cfg.error = "config error: KILL_HOOK_TIMEOUT"
    cap = ns.get("CAPTION")
    if cap is not None:
        if callable(getattr(cap, "update", None)) and isinstance(getattr(cap, "every", None), (int, float)):
            cfg.caption = cap
        else:
            cfg.error = "config error: CAPTION"
    keep = ns.get("SESSIONS_KEEP_ENV", [])
    if isinstance(keep, (list, tuple)) and all(isinstance(k, str) for k in keep):
        cfg.sessions_keep_env = list(keep)
    else:
        cfg.error = "config error: SESSIONS_KEEP_ENV"
    return cfg


def prefix_byte(name: str) -> int:
    """'C-a' / '^A' -> 0x01; 'C-]' -> 0x1d."""
    name = name.strip()
    ch = name[-1]
    if name.lower().startswith(("c-", "^")):
        return ord(ch.upper()) & 0x1F if ch.isalpha() else ord(ch) & 0x1F
    return ord(ch)
