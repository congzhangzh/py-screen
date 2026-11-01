"""The client: raw terminal in, rendered frames out. Survives a brain reload by
reconnecting, so `pys reload` looks like a redraw, not a detach."""

from __future__ import annotations

import asyncio
import os

from . import platform, proto

RESET = ("\x1b[?2026l\x1b[0m\x1b[?7h\x1b[?1l\x1b[?2004l\x1b[?1000l\x1b[?1002l\x1b[?1003l"
         "\x1b[?1006l\x1b[?25h\x1b[?1049l")
RECONNECT_FOR = 10.0


async def connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await platform.connect(proto.brain_sock(), limit=1 << 22)


async def attach() -> str:
    """Attach until detached or the session ends; return the reason."""
    term = platform.RawTerm()
    size = term.size
    writer: asyncio.StreamWriter | None = None

    def on_keys(data: bytes) -> None:
        if writer and not writer.is_closing():
            writer.write(proto.pack(proto.DATA, 0, data))

    def on_resize() -> None:
        if writer and not writer.is_closing():
            cols, rows = size()
            writer.write(proto.pack_ctl({"op": "resize", "cols": cols, "rows": rows}))

    term.enter()
    term.write(b"\x1b[?1049h")
    term.start(on_keys, on_resize)
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
                        term.write(body)  # type: ignore[arg-type]
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
        term.write(RESET.encode())
        term.leave()
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
