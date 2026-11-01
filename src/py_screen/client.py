"""The client: raw terminal in, rendered frames out. Survives a brain reload by
reconnecting, so `pys reload` looks like a redraw, not a detach."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import termios
import tty

from . import proto

RESET = ("\x1b[?2026l\x1b[0m\x1b[?1l\x1b[?2004l\x1b[?1000l\x1b[?1002l\x1b[?1003l"
         "\x1b[?1006l\x1b[?25h\x1b[?1049l")
RECONNECT_FOR = 10.0


async def connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_unix_connection(str(proto.brain_sock()), limit=1 << 22)


async def attach() -> str:
    """Attach until detached or the session ends; return the reason."""
    fd_in, fd_out = sys.stdin.fileno(), sys.stdout.fileno()
    saved = termios.tcgetattr(fd_in)
    loop = asyncio.get_running_loop()
    writer: asyncio.StreamWriter | None = None

    def size() -> tuple[int, int]:
        s = shutil.get_terminal_size()
        return s.columns, s.lines

    def on_keys() -> None:
        data = os.read(fd_in, 65536)
        if data and writer and not writer.is_closing():
            writer.write(proto.pack(proto.DATA, 0, data))

    def on_winch() -> None:
        if writer and not writer.is_closing():
            cols, rows = size()
            writer.write(proto.pack_ctl({"op": "resize", "cols": cols, "rows": rows}))

    tty.setraw(fd_in)
    os.write(fd_out, b"\x1b[?1049h")
    loop.add_reader(fd_in, on_keys)
    loop.add_signal_handler(signal.SIGWINCH, on_winch)
    reason = "lost"
    try:
        while True:
            try:
                reader, writer = await connect()
            except OSError:
                reason = "lost"
                break
            cols, rows = size()
            writer.write(proto.pack_ctl({"op": "attach", "cols": cols, "rows": rows,
                                         "cwd": os.getcwd(),
                                         "prefix": os.environ.get("PYS_PREFIX"),
                                         "in_screen": bool(os.environ.get("STY"))}))
            reason = "lost"
            try:
                while True:
                    kind, _, body = await proto.read_frame(reader)
                    if kind == proto.DATA:
                        os.write(fd_out, body)  # type: ignore[arg-type]
                    elif body.get("op") in ("detached", "bye", "reloading"):  # type: ignore[union-attr]
                        reason = body["op"]  # type: ignore[index]
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            writer.close()
            if reason not in ("reloading", "lost"):
                break
            if not await wait_for_brain():
                break
    finally:
        loop.remove_reader(fd_in)
        loop.remove_signal_handler(signal.SIGWINCH)
        os.write(fd_out, RESET.encode())
        termios.tcsetattr(fd_in, termios.TCSADRAIN, saved)
    return reason


async def wait_for_brain() -> bool:
    """A brain reload or crash: the keeper starts a new one within a second or so."""
    deadline = asyncio.get_running_loop().time() + RECONNECT_FOR
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
        try:
            _, w = await connect()
            w.close()
            return True
        except OSError:
            continue
    return False
