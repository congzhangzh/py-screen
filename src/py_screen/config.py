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
    BUTTONS = True                            # + ▾ ? after " pys "; False: the room
                                              # goes to the window tabs
    SLOGAN = True                             # the motto in the status line's blank space
    STATUS_LINES = 2                          # the window tabs wrap onto up to this many
                                              # status rows, only while one doesn't hold them
    ON_CHANGE = ["pys-sessions", "save", "--quiet"]   # run after windows change and
                                              # before `pys kill` / prefix Q ends them
    KILL_HOOK_TIMEOUT = 5                     # seconds a kill waits for ON_CHANGE
    SESSIONS_KEEP_ENV = ["MY_VAR"]            # pys-sessions: variables a resumed claude
                                              # gets back from the one that ran
"""

from __future__ import annotations

import asyncio
import os
import shutil
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


STATUS_LINES = 2       # rows the status line may take when the window tabs don't fit in one
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
    status_lines: int = STATUS_LINES   # at most this many status rows, more only if needed
    buttons: bool = True          # + ▾ ? after " pys " (with the mouse on)
    slogan: bool = True           # the motto, dim, in the status line's blank space
    origin: dict = field(default_factory=dict)   # panel key → the file that set it
    on_change: list[str] | None = None   # argv run after windows change and before a kill
    kill_hook_timeout: float = KILL_HOOK_TIMEOUT
    sessions_keep_env: list[str] = field(default_factory=list)   # for pys-sessions only
    caption: object | None = None   # CAPTION: text after each shown window's name; None: folder


def default() -> Config:
    return Config([load(), clock()], {}, None)


# pys's own commands, run with this pys's own Python: they need not be on PATH (uvx, a
# bundle whose bin/ is not on PATH) and can't be another install's, of another version
OWN = {"pys": "py_screen", "pys-agents": "py_screen.agents.listing",
       "pys-sessions": "py_screen.agents.sessions"}


def command(argv: list[str]) -> list[str]:
    """What runs a configured command (ON_CHANGE): one of pys's own as `python -m`, any
    other found on PATH as a shell finds it (shutil.which: on Windows also <name>.cmd,
    which CreateProcess alone never tries); else as it is, to fail with its own error."""
    if not argv:
        return []
    if argv[0] in OWN:
        return [sys.executable, "-m", OWN[argv[0]], *argv[1:]]
    exe = shutil.which(argv[0])
    return [exe, *argv[1:]] if exe else list(argv)


def path() -> Path:
    if p := os.environ.get("PYS_CONFIG"):
        return Path(p)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "pys" / "config.py"


def drop_in_dir() -> Path:
    """config.d beside the config (~/.config/pys/config.d): files of your own, or of a tool
    (`pys setup`), that add to or change config.py without editing it."""
    p = path()
    return p.with_name(p.stem + ".d")


def sources() -> list[Path]:
    """What makes the config, in the order it runs: config.py, then config.d/*.py by name."""
    d = drop_in_dir()
    extra = sorted(f for f in d.glob("*.py") if f.is_file()) if d.is_dir() else []
    return ([path()] if path().is_file() else []) + extra


def namespace(origin: dict | None = None) -> tuple[dict, list[tuple[Path, BaseException]], bool]:
    """Run the config's sources into one namespace: (names, [(file, its error)], usable).
    Each file in config.d sees, and may change, what came before it (WIDGETS.append(…),
    BIND[…] = …). A file that fails is left out (one level of its changes to lists and
    dicts is undone) and named; the others still count. A config.py that fails makes the
    whole config unusable, as before config.d: the brain then runs on its defaults.
    origin, if given, gets the file that last set each of the settings panel's keys."""
    from .settings import KEYS as panel_keys
    ns: dict = {"cmd": cmd, "load": load, "clock": clock}
    errors: list[tuple[Path, BaseException]] = []
    main = path()
    for f in sources():
        keep = {k: (v.copy() if isinstance(v, (list, dict)) else v) for k, v in ns.items()}
        try:
            exec(compile(f.read_bytes(), str(f), "exec"), ns)  # UTF-8 source, any locale
        except Exception as e:  # a broken config must not stop the brain
            errors.append((f, e))
            if f == main:
                return ns, errors, False
            ns.clear()
            ns.update(keep)
            continue
        if origin is not None:
            for k in panel_keys:
                if k in ns and (k not in keep or ns[k] is not keep[k]):
                    origin[k] = f.name
    return ns, errors, True


def load_config() -> Config:
    cfg = default()
    if not sources():
        return cfg
    ns, errors, usable = namespace(cfg.origin)
    for f, e in errors:
        print(f"pys: config {f}: {e!r}", file=sys.stderr)
    if errors:
        cfg.error = f"config error: {type(errors[0][1]).__name__}" + \
            (f" in {errors[0][0].name}" if errors[0][0] != path() else "")
    if not usable:
        return cfg
    if "WIDGETS" in ns:
        cfg.widgets = list(ns["WIDGETS"])
    cfg.bind = {k: (v[0], list(v[1])) for k, v in ns.get("BIND", {}).items()}
    cfg.prefix = ns.get("PREFIX")
    cfg.mouse = bool(ns.get("MOUSE", True))
    cfg.show_widgets = bool(ns.get("SHOW_WIDGETS", True))
    cfg.buttons = bool(ns.get("BUTTONS", True))
    cfg.slogan = bool(ns.get("SLOGAN", True))
    try:
        cfg.scrollback = max(0, int(ns.get("SCROLLBACK", SCROLLBACK)))
    except (TypeError, ValueError):
        cfg.error = "config error: SCROLLBACK"
    try:
        cfg.status_lines = max(1, int(ns.get("STATUS_LINES", STATUS_LINES)))
    except (TypeError, ValueError):
        cfg.error = "config error: STATUS_LINES"
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
