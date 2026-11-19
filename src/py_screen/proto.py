"""Wire format shared by keeper, brain and clients.

One frame = header + payload. Header is network order: payload length (u32),
frame type (u8), window id (u32). DATA carries raw bytes (pty output, keys,
rendered VT); CTL carries one JSON object.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
from pathlib import Path

from . import platform

HEADER = struct.Struct("!IBI")
DATA = 1
CTL = 2
NO_WINDOW = 0xFFFFFFFF

RELOAD_EXIT = 75  # brain exit code that asks the keeper to start it again at once


def utf8_env(env: dict | None = None) -> dict:
    """The environment for a Python child of pys (the brain, the ON_CHANGE hook): UTF-8 mode,
    PYTHONUTF8=1, so its files, pipes and stdio are UTF-8 whatever the locale (GBK on a
    Chinese Windows with Python 3.14; 3.15 is UTF-8 already, PEP 686). Not for the keeper:
    the shells in the windows inherit its environment, and they are the user's."""
    return dict(os.environ if env is None else env, PYTHONUTF8="1")


def runtime_dir() -> Path:
    """Where sockets (or the names of named pipes) and logs live. PYS_DIR overrides
    (dev builds, tests)."""
    if d := os.environ.get("PYS_DIR"):
        p = Path(d)
    else:
        p = platform.runtime_base() / "pys"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    platform.check_path(p)
    return p


def runtime_dir_path() -> Path:
    """The runtime dir's path, without making it or checking it (runtime_dir() does both)."""
    d = os.environ.get("PYS_DIR")
    return Path(d) if d else platform.runtime_base() / "pys"


def keeper_sock_path() -> Path:
    """keeper.sock's path, without making the runtime dir."""
    return runtime_dir_path() / "keeper.sock"


def keeper_sock() -> Path:
    return runtime_dir() / "keeper.sock"


def brain_sock() -> Path:
    return runtime_dir() / "brain.sock"


def pack(kind: int, wid: int, payload: bytes) -> bytes:
    return HEADER.pack(len(payload), kind, wid) + payload


def pack_ctl(msg: dict, wid: int = NO_WINDOW) -> bytes:
    return pack(CTL, wid, json.dumps(msg, separators=(",", ":")).encode())


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, int, bytes | dict]:
    """Return (kind, wid, payload); CTL payloads are decoded. Raises IncompleteReadError at EOF."""
    head = await reader.readexactly(HEADER.size)
    n, kind, wid = HEADER.unpack(head)
    body = await reader.readexactly(n) if n else b""
    if kind == CTL:
        return kind, wid, json.loads(body)
    return kind, wid, body


async def rpc(msg: dict, timeout: float = 5.0) -> dict:
    """One-shot request to the brain: send a CTL, return its CTL reply."""
    reader, writer = await asyncio.wait_for(platform.connect(brain_sock()), timeout)
    try:
        writer.write(pack_ctl({"op": "rpc", **msg}))
        await writer.drain()
        _, _, reply = await asyncio.wait_for(read_frame(reader), timeout)
        assert isinstance(reply, dict)
        while reply.get("op") == "wait":         # the brain needs longer: a kill runs
            t = timeout + float(reply.get("for", 0))   # ON_CHANGE first
            _, _, reply = await asyncio.wait_for(read_frame(reader), t)
            assert isinstance(reply, dict)
        return reply
    finally:
        writer.close()
