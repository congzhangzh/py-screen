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
    c = sub.add_parser("capture", help="print a window's screen")
    c.add_argument("wid", type=int, nargs="?")
    n = sub.add_parser("new", help="new window, optionally running a command")
    n.add_argument("--select", action="store_true")
    n.add_argument("argv", nargs=argparse.REMAINDER)
    sel = sub.add_parser("select", help="show a window")
    sel.add_argument("wid", type=int)
    sub.add_parser("reload", help="restart the brain; shells keep running")
    sub.add_parser("kill", help="end the session and every window")
    sub.add_parser("_keeper")
    sub.add_parser("_brain")
    a = p.parse_args()

    if a.cmd == "_keeper":
        from . import keeper
        keeper.main()
    elif a.cmd == "_brain":
        from . import brain
        brain.main()
    elif a.cmd in (None, "attach"):
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
        r = {
            "ls": lambda: rpc(cmd="ls"),
            "send": lambda: rpc(cmd="send", wid=a.wid, text=a.text + ("\r" if a.enter else "")),
            "capture": lambda: rpc(cmd="capture", **({} if a.wid is None else {"wid": a.wid})),
            "new": lambda: rpc(cmd="new", argv=[x for x in a.argv if x != "--"] or None,
                               cwd=os.getcwd(), select=a.select),
            "select": lambda: rpc(cmd="select", wid=a.wid),
            "reload": lambda: rpc(cmd="reload"),
            "kill": lambda: rpc(cmd="kill"),
        }[a.cmd]()
        if a.cmd == "ls" and not a.json:
            for w in r["windows"]:
                mark = "*" if w["wid"] == r["current"] else " "
                print(f"{mark}{w['wid']:>3}  {w['name']:<20} {w['cwd'] or ''}")
        elif a.cmd == "capture":
            print("\n".join(line.rstrip() for line in r["lines"]))
        elif a.cmd in ("ls", "new"):
            print(json.dumps(r))
