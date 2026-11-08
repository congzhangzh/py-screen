"""py-screen: a terminal workspace of my own. CLI entry point `pys`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from . import platform, proto
from .platform import alive


def ensure_server(timeout: float = 5.0) -> None:
    """Start the keeper (which starts the brain) unless a brain already answers."""
    if alive(proto.brain_sock()):
        return
    if not alive(proto.keeper_sock()):
        platform.start_detached([sys.executable, "-m", "py_screen", "_keeper"],
                                proto.runtime_dir() / "server.log")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if alive(proto.brain_sock()):
            return
        time.sleep(0.02)
    sys.exit(f"pys: server did not start; see {proto.runtime_dir() / 'server.log'}")


def nested_in() -> str | None:
    """The window this process runs in, if attaching would show that window's own session
    inside it: keys typed there go to the brain, back to this window, to the inner client
    and to the brain again, so the window stops answering. PYS_WINDOW and PYS_SESSION come
    from the keeper (keeper.spawn). A keeper started before PYS_SESSION existed sets only
    PYS_WINDOW; that window's own session is then the one PYS_DIR picks, so it counts too.
    Another session (another PYS_DIR) may be nested; only its prefix key needs care."""
    wid = os.environ.get("PYS_WINDOW")
    if wid is None:
        return None
    mine = os.environ.get("PYS_SESSION")
    if mine is None or os.path.realpath(mine) == os.path.realpath(proto.runtime_dir()):
        return wid
    return None


def utf8_pipes() -> None:
    """What the pys commands print into a pipe or a file is UTF-8, on every system and every
    Python: `pys ls --json` read by a script, `pys capture` by an agent. Python 3.14 would
    use the locale's code page on Windows (GBK on a Chinese one), which garbles what the
    reader decodes as UTF-8 and fails on a title it has no character for; 3.15 is UTF-8
    already (PEP 686). A console is left alone: Python writes to it in UTF-16."""
    for s in (sys.stdout, sys.stderr):
        try:
            if not s.isatty() and s.encoding.lower().replace("-", "") != "utf8":
                s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


def rpc(**msg) -> dict:
    reply = asyncio.run(proto.rpc(msg))
    if not reply.get("ok"):
        sys.exit(f"pys: {reply.get('error')}")
    return reply


def main() -> None:
    p = argparse.ArgumentParser(prog="pys", description="py-screen: a terminal workspace")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("attach", help="attach, starting the server if needed (default)")
    ls = sub.add_parser("ls", help="list windows")
    ls.add_argument("--json", action="store_true")
    s = sub.add_parser("send", help="type text into a window")
    s.add_argument("wid", type=int)
    s.add_argument("text")
    s.add_argument("--enter", action="store_true", help="append a carriage return")
    t = sub.add_parser("title", help="name a window (a title set by hand with prefix , wins)",
                       description="Set, clear or print a window's agent title: it is shown "
                       "instead of the app's own title, and a title set by hand (prefix ,) "
                       "is shown instead of it. With no text and no --clear, print it.")
    t.add_argument("text", nargs="?")
    t.add_argument("--wid", type=int, help="the window (default: the one this runs in)")
    t.add_argument("--clear", action="store_true")
    t.add_argument("--owner", type=int, metavar="PID",
                   help="drop the title when this process ends (e.g. $PPID in an agent's "
                   "tool shell); without it, the title stays until cleared")
    c = sub.add_parser("capture", help="print a window's screen")
    c.add_argument("wid", type=int, nargs="?")
    c.add_argument("--history", action="store_true", help="the scrollback first, then the screen")
    c.add_argument("--json", action="store_true")
    n = sub.add_parser("new", help="new window, optionally running a command")
    n.add_argument("--select", action="store_true")
    n.add_argument("argv", nargs=argparse.REMAINDER)
    sel = sub.add_parser("select", help="show a window")
    sel.add_argument("wid", type=int)
    sub.add_parser("reload", help="restart the brain; shells keep running")
    sub.add_parser("kill", help="end the session and every window")
    sub.add_parser("sample-config", help="print a sample ~/.config/pys/config.py")
    dr = sub.add_parser("doctor", help="where the config is, and a check of it: it loads as the "
                        "brain loads it, each widget runs once, typos are named")
    dm = dr.add_mutually_exclusive_group()
    dm.add_argument("--init", action="store_true", help="write the sample config (never over yours)")
    dm.add_argument("--reset", action="store_true",
                    help="keep yours as config.py.<time>, then write the sample")
    sub.add_parser("_keeper")
    sub.add_parser("_brain")
    a = p.parse_args()
    utf8_pipes()     # the server log too: the keeper and brain write it as stderr

    if a.cmd == "doctor":
        from . import doctor
        sys.exit(doctor.cmd_init(a.reset) if a.init or a.reset else doctor.cmd_check())
    if a.cmd == "sample-config":                   # the file ships in the wheel, next to us
        from pathlib import Path
        sys.stdout.write((Path(__file__).parent / "sample_config.py").read_text(encoding="utf-8"))
    elif a.cmd == "_keeper":
        from . import keeper
        keeper.main()
    elif a.cmd == "_brain":
        from . import brain
        brain.main()
    elif a.cmd in (None, "attach"):
        if (wid := nested_in()) is not None:
            sys.exit(f"pys: already inside this session (window {wid}); attaching here would "
                     "show it inside itself. Use prefix c for a new window, or another "
                     "session with PYS_DIR=<dir> pys.")
        if not os.isatty(0):
            sys.exit("pys: attach needs a terminal")
        ensure_server()
        from . import client
        reason = asyncio.run(client.attach())
        print({"detached": "[detached]", "bye": "[session ended]"}.get(
            reason, f"[connection {reason}]"))
    else:
        if a.cmd == "new":
            ensure_server()
        if a.cmd == "title":
            if a.clear and a.text is not None:
                sys.exit("pys: title takes text or --clear, not both")
            if a.owner is not None and a.text is None:
                sys.exit("pys: --owner goes with a title's text")
            if a.wid is None:
                if (here := nested_in()) is None:
                    sys.exit("pys: not inside a window of this session; pass --wid")
                a.wid = int(here)
        r = {
            "ls": lambda: rpc(cmd="ls"),
            "send": lambda: rpc(cmd="send", wid=a.wid, text=a.text + ("\r" if a.enter else "")),
            "capture": lambda: rpc(cmd="capture", history=a.history,
                                   **({} if a.wid is None else {"wid": a.wid})),
            "new": lambda: rpc(cmd="new", argv=[x for x in a.argv if x != "--"] or None,
                               cwd=os.getcwd(), select=a.select),
            "select": lambda: rpc(cmd="select", wid=a.wid),
            "title": lambda: rpc(cmd="title", wid=a.wid,
                                 **({"text": None} if a.clear else
                                    {} if a.text is None else {"text": a.text,
                                                               "owner": a.owner})),
            "reload": lambda: rpc(cmd="reload"),
            "kill": lambda: rpc(cmd="kill"),
        }[a.cmd]()
        if a.cmd == "ls" and not a.json:
            for w in r["windows"]:
                mark = "*" if w["wid"] == r["current"] else " "
                print(f"{mark}{w['wid']:>3}  {w['name']:<20} {w['cwd'] or ''}")
        elif a.cmd == "capture" and a.json:
            print(json.dumps(r))
        elif a.cmd == "capture":
            print("\n".join(line.rstrip() for line in (r.get("history") or []) + r["lines"]))
        elif a.cmd == "title" and a.text is None and not a.clear:
            print(r["agent_title"] or "")
        elif a.cmd == "title" and r["agent_title"] and r["title"]:
            print(f"pys: window {r['wid']} has a title set by hand, {r['title']!r}; "
                  "it is shown instead", file=sys.stderr)
        elif a.cmd in ("ls", "new"):
            print(json.dumps(r))
