"""`pys doctor`: where the config is, and a check of it; `--init` / `--reset`: a first one.

- --init writes the sample (the same text as `pys sample-config`) where pys reads its config,
  and never over one: --reset first renames the current one to config.py.<YYYYMMDD-HHMMSS>
  (kept, never deleted), then writes the sample.
- `pys doctor` loads the config as the brain does, then runs what the brain would run: every
  widget's first update and the CAPTION's, each with a timeout, and checks what the brain
  can't: names that look like a typo of a key pys reads (a typo is silently ignored), the
  ON_CHANGE command on PATH, the prefix, psutil off Linux, the agent skills. Exit 1 on an
  error (✗), 0 when there are only notes (⚠) or nothing.
"""
from __future__ import annotations

import asyncio
import difflib
import os
import shutil
import sys
import time
from pathlib import Path

from . import config

KEYS = ["WIDGETS", "BIND", "PREFIX", "MOUSE", "SCROLLBACK", "SHOW_WIDGETS", "ON_CHANGE",
        "KILL_HOOK_TIMEOUT", "SESSIONS_KEEP_ENV", "CAPTION"]
UPDATE_TIMEOUT = 5.0
SAMPLE = Path(__file__).parent / "sample_config.py"


def cmd_init(reset: bool) -> int:
    p = config.path()
    if p.exists() and not reset:
        print(f"pys: {p} exists; `pys doctor --reset` keeps it as {p.name}.<time> and "
              "writes the sample", file=sys.stderr)
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        keep, n = p.with_name(f"{p.name}.{time.strftime('%Y%m%d-%H%M%S')}"), 1
        while keep.exists():                       # never over an older one
            n += 1
            keep = p.with_name(f"{p.name}.{time.strftime('%Y%m%d-%H%M%S')}.v{n}")
        p.rename(keep)
        print(f"kept {keep}")
    p.write_text(SAMPLE.read_text())
    print(f"wrote {p} (the sample); C-a R (or `pys reload`) applies it")
    return 0


class Report:
    def __init__(self) -> None:
        self.errors = 0

    def ok(self, msg: str) -> None:
        print(f"✓ {msg}")

    def note(self, msg: str) -> None:
        print(f"⚠ {msg}")

    def bad(self, msg: str) -> None:
        self.errors += 1
        print(f"✗ {msg}")


async def try_update(obj, ctx: dict) -> tuple[str, float]:
    t = time.monotonic()
    text = await asyncio.wait_for(obj.update(ctx), UPDATE_TIMEOUT)
    return config.clean(str(text or "")), time.monotonic() - t


def check_updates(r: Report, cfg: config.Config) -> None:
    ctx = {"cwd": os.getcwd(), "wid": None, "pid": None}

    async def run() -> None:
        items = [(f"widget {i} {type(w).__name__}", w) for i, w in enumerate(cfg.widgets)]
        if cfg.caption is not None:
            items.append((f"CAPTION {type(cfg.caption).__name__}", cfg.caption))
        for name, obj in items:
            try:
                text, took = await try_update(obj, ctx)
            except asyncio.TimeoutError:
                r.bad(f"{name}: no answer in {UPDATE_TIMEOUT:.0f} s (the brain shows its old text)")
                continue
            except Exception as e:
                r.bad(f"{name}: {type(e).__name__}: {e} (the status line shows !{type(e).__name__})")
                continue
            slow = f", slow: it runs every {obj.every} s" if took > 1.0 and obj.every < 2 * took else ""
            (r.note if slow else r.ok)(f"{name}: {text!r} in {took * 1000:.0f} ms{slow}")

    asyncio.run(run())


def cmd_check() -> int:
    r = Report()
    p = config.path()
    if not p.is_file():
        r.ok(f"no config at {p}: pys runs on its defaults; `pys doctor --init` writes the sample")
        return 0
    r.ok(f"config {p}")
    ns: dict = {"cmd": config.cmd, "load": config.load, "clock": config.clock}
    try:
        exec(compile(p.read_text(), str(p), "exec"), ns)
    except Exception as e:
        tb = e.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        where = f" (line {tb.tb_lineno})" if tb and tb.tb_frame.f_code.co_filename == str(p) else ""
        r.bad(f"it does not run{where}: {type(e).__name__}: {e}; the brain falls back to its defaults")
        return 1
    cfg = config.load_config()
    if cfg.error:
        r.bad(cfg.error)
    for name in sorted(k for k in ns if k.isupper() and k not in KEYS):
        if close := difflib.get_close_matches(name, KEYS, n=1, cutoff=0.75):
            r.note(f"{name} is not read by pys: did you mean {close[0]}?")
    if cfg.prefix is not None:
        try:
            b = config.prefix_byte(cfg.prefix)
            r.ok(f"PREFIX {cfg.prefix!r} = byte {b:#04x}")
        except (IndexError, TypeError, AttributeError):
            r.bad(f"PREFIX {cfg.prefix!r} is not a key such as 'C-a'")
    check_updates(r, cfg)
    if cfg.on_change:
        exe = cfg.on_change[0]
        found = shutil.which(exe) or (os.path.isfile(exe) and os.access(exe, os.X_OK) and exe)
        if found:
            r.ok(f"ON_CHANGE runs {found}; a kill waits for it {cfg.kill_hook_timeout:.0f} s at most")
        else:
            r.bad(f"ON_CHANGE: {exe!r} is not on PATH: every change and kill logs a failure")
    if not sys.platform.startswith("linux"):
        try:
            import psutil  # noqa: F401
        except ImportError:
            r.note("psutil is not installed: the sample's cpu/mem/net/disk widgets show "
                   "nothing (uv tool install 'py-screen-mux[stats]')")
    skills = Path.home() / ".claude" / "skills"
    if sys.platform.startswith("linux") and skills.parent.is_dir():
        missing = [n for n in ("pys-drive", "pys-restore") if not (skills / n).exists()]
        if missing:
            r.note(f"Claude Code is here but not the skills {', '.join(missing)}: "
                   "`pys-agents skills --install`")
        else:
            r.ok("agent skills pys-drive and pys-restore are installed")
    print(f"{r.errors} error(s)" if r.errors else "no errors; C-a R (or `pys reload`) applies a change")
    return 1 if r.errors else 0
