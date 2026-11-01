# py-screen

A terminal workspace of my own, in Python: not a clone of screen or tmux, built for fun,
made more powerful and improved by daily use. The design is in [`spec/`](spec/); the
current version is [`spec/spec.v2.md`](spec/spec.v2.md).

Status: **M0 spike** (Linux). One server, many windows, attach/detach, hot reload.

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

Keys (prefix **`C-]`**): `c` new window in the current dir · `n`/Space next · `p`
previous · `0`–`9` select · `d` detach · `X` kill window · `l` redraw · `R` reload brain ·
`Q` quit everything · `C-]` a literal `C-]`.

Sockets live in `$XDG_RUNTIME_DIR/pys/` (`PYS_DIR` overrides; keep it short: an AF_UNIX
path is limited to 108 bytes). Server log: `server.log` in that dir.

## Test and measure

```bash
uv run pytest -s                                      # M0 done-criteria (spec §6)
uv run python bench/m0_speed.py --work <scratch dir>  # speed vs GNU screen (spec §8.1)
```

Results of each run are kept in [`bench/results/`](bench/results/), one file per run.

## Known gaps (M0)

- pyte has no alternate screen: after vim exits, its last screen stays until the shell
  draws over it.
- No scrollback view, splits, config file or widgets yet (M1/M2).
- Linux only; the Windows check (pywinpty + named pipes) waits for the rig.
