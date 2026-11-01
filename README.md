# py-screen

A terminal workspace of my own, in Python: not a clone of screen or tmux, built for fun,
made more powerful and improved by daily use. The design is in [`spec/`](spec/); the
current version is [`spec/spec.v2.md`](spec/spec.v2.md).

Status: **M1 in progress** (Linux; Windows pieces checked on CI). One server, many windows, attach/detach, hot reload, flood-safe typing.

## How it works

```
client (pys attach) ──► brain (Python, restartable) ──► keeper (tiny, stable) ──► ptys
```

- **keeper** owns every pty and child process and is the only reader of their output
  (a byte pump with a ring buffer per window). It restarts the brain when it exits.
- **brain** holds a pyte screen per window, renders the current one plus a status line,
  handles keys and answers RPCs. `pys reload` restarts it; shells never notice, because
  the ptys live in the keeper. The new brain replays the ring buffers, then nudges each pty
  (SIGWINCH) so full-screen apps repaint.
- **client** puts the terminal in raw mode, forwards keys and size, prints frames, and
  reconnects by itself across a brain reload.

### Floods (M1)

Parsing (pyte, about 1 MB/s) is the slow part, so a flood must never cost typing:

- The keeper gives each window a **credit** of 256 KB un-acked output; past it, it stops
  reading that pty until the brain acks. One window can't fill the shared stream, so the
  echo of another never waits behind megabytes of it.
- The brain **yields after every frame** (`readexactly()` doesn't suspend when its buffer
  already holds the bytes, so a flood would otherwise starve the client's keys).
- Output is kept raw and **parsed only when it can be seen**. When more than 256 KB waits
  at a frame, only the last 32 KB is parsed, from a line start. A hidden window keeps its
  last 128 KB, is parsed when shown, and gets a `SIGWINCH` nudge so TUIs repaint.
  `pys ls --json` reports `skipped` bytes per window.

Trade-off: bytes skipped during a flood are not in the brain's screen history. The keeper's
2 MB ring buffer still holds them; scrollback (M2) will read from there.

## Use

```bash
uv sync
uv run pys                 # attach (starts the server on first use)
uv run pys ls [--json]     # windows
uv run pys new [--select] [-- cmd …]
uv run pys send 0 'make test' --enter
uv run pys capture 0       # the window's screen as text
uv run pys reload          # new brain, same shells
uv run pys kill            # end everything
```

Keys: the prefix is **`C-a`**, or **`C-]`** when you run `pys` inside GNU screen (`$STY`
set), so the two don't fight. Override with `PREFIX` in the config or `PYS_PREFIX`.

| Keys | Action |
|---|---|
| `c` / `C-c` | new window in the focused window's directory |
| `\|` / `-` | split side by side / stacked, with a new window in the same dir |
| `\` / `_` | empty split side by side / stacked |
| `h` `j` `k` `l` | move the focus between panes |
| `r` then `h/l/j/k`, `=` | resize mode (any other key ends it) · `=` equalise panes |
| `x` / `X` | close the pane (window keeps running) / kill the window |
| `n` `p` `0`–`9`, `Alt-0`–`Alt-9` | next, previous, select |
| `Space` or the prefix twice | toggle to the last window |
| `w` `"` `F2` | window list (`j/k`, arrows, digits, Enter, Esc) |
| `,` | rename the window |
| `a` | send a literal prefix |
| `d` · `C-l` · `R` · `Q` | detach · redraw · reload the brain · quit everything |

### Config

`~/.config/pys/config.py` (plain Python, read on every brain start, so `C-a R` applies an
edit): status widgets and extra key bindings. See `src/py_screen/config.py` for the helpers.

```python
WIDGETS = [cmd(["git", "-C", "{cwd}", "branch", "--show-current"], every=10), load(), clock()]
BIND = {"T": ("top", ["htop"])}   # C-a T opens a window running htop
```

The layout (splits, focus, renamed titles) is saved on every change, so a reload keeps it.

Sockets live in `$XDG_RUNTIME_DIR/pys/` (`PYS_DIR` overrides; keep it short: an AF_UNIX
path is limited to 108 bytes). Server log: `server.log` in that dir.

## Test and measure

```bash
uv run pytest -s                                      # M0 done-criteria (spec §6)
uv run python bench/m0_speed.py --work <scratch dir>  # speed vs GNU screen (spec §8.1)
```

Results of each run are kept in [`bench/results/`](bench/results/), one file per run.

## Known gaps (M1)

- Mouse reporting is passed through only while the focused pane is the top-left one
  (coordinates aren't translated yet).
- A hidden window's title in the status line updates only when the window is shown
  (its output isn't parsed until then).
- No scrollback view yet (M2).
- Linux (and macOS) only for now; the Windows pieces are checked by the manual CI run.
