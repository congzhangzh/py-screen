"""The platform layer (spec §5.2): everything that differs between Linux, macOS and Windows.

The rest of py-screen imports only the names below, never pty/termios/fcntl/AF_UNIX/
signal-handling or ConPTY directly, so one core runs on all three systems.

| Name                         | POSIX (Linux, macOS)              | Windows                          |
|------------------------------|-----------------------------------|----------------------------------|
| `spawn_pty(...) -> Pty`      | `pty.fork` + `add_reader`         | ConPTY via pywinpty + a reader thread |
| `serve` / `connect` / `alive`| Unix socket (mode 0600)           | named pipe (asyncio Proactor)    |
| `start_detached(argv, log)`  | `setsid` (start_new_session)      | `DETACHED_PROCESS`               |
| `RawTerm`                    | `tty.setraw` + SIGWINCH           | console VT mode + size polling   |
| `on_stop(loop, cb)`          | SIGTERM/SIGINT, SIGHUP ignored    | SIGINT/SIGBREAK                  |
| `proc_cwd(pid)`              | /proc (Linux), libproc (macOS)    | None (not readable without PEB tricks) |
| `default_shell()`            | `$SHELL`, else /bin/sh            | `$PYS_SHELL`, else `%COMSPEC%`   |
| `loadavg()`                  | `os.getloadavg()[0]`              | None                             |
| `runtime_base()`             | `$XDG_RUNTIME_DIR` or /tmp/pys-UID| `%LOCALAPPDATA%`                 |

`Pty` is the one object with behaviour; see `posix.PosixPty` for the contract.
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    from .windows import (RawTerm, alive, check_path, cleanup, connect, default_shell, loadavg,
                          on_stop, proc_cwd, runtime_base, serve, spawn_pty,
                          start_detached)
else:
    from .posix import (RawTerm, alive, check_path, cleanup, connect, default_shell, loadavg,
                        on_stop, proc_cwd, runtime_base, serve, spawn_pty,
                        start_detached)

__all__ = ["RawTerm", "alive", "check_path", "cleanup", "connect", "default_shell", "loadavg",
           "on_stop", "proc_cwd", "runtime_base", "serve", "spawn_pty", "start_detached"]
