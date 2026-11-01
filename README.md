# py-screen

A terminal workspace of my own, in Python: not a clone of screen or tmux, built for fun,
made more powerful and improved by daily use. The design is in [`spec/`](spec/); the
current version is [`spec/spec.v3.md`](spec/spec.v3.md).

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

### Floods: the screen model is lossless

Every byte a window writes is parsed, in order, whether the window is shown or hidden,
attached or not. That matters most for apps that draw by diff, like Claude Code: they
write only the cells they think changed and never rewrite a cell they think is right, so
a single lost byte leaves wrong cells for good (and a lost mode switch, such as the
alternate screen or bracketed paste, breaks input too). Until 2026-11-01 py-screen parsed
only the tail of a flood and dropped a hidden window's output past 128 KB; that is what
left stray characters in Claude's status line.

Parsing (pyte, about 0.75 MB/s of scrolling text) is the slow part, so:

- **Backpressure, not dropping.** The keeper gives each window a **credit** of 256 KB
  un-acked output and stops reading that pty past it. The brain acks bytes only once it
  has parsed them, so a flooding app simply waits on its write, as under GNU screen. One
  window can't fill the shared stream, so another window's echo never waits behind it.
- **Small steps.** The brain parses in steps of 2 ms (2 KB per window per turn, shown
  windows first, the others round robin), then goes back to keys and frames.
- **Keys first.** For 100 ms after a key, only shown windows are parsed: nothing else
  stands between a key and its echo (about 2 ms median while a hidden window floods).

Trade-off: a flood runs at the parser's speed, slower than GNU screen's C parser (numbers
in [`bench/results/`](bench/results/)). A faster parser is the next step (spec §8.1).

## Use

```bash
uv tool install -e .       # puts `pys` in ~/.local/bin, running this checkout (edits apply on reload)
pys                        # attach (starts the server on first use)
pys ls [--json]     # windows
pys new [--select] [-- cmd …]
pys send 0 'make test' --enter
pys capture 0       # the window's screen as text
pys reload          # new brain, same shells
pys kill            # end everything
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
| `?` | this list, plus your config's bindings |
| `a` | send a literal prefix |
| `d` · `C-l` · `R` · `Q` | detach · redraw · reload the brain · quit everything |

Without the prefix: `Alt-0`…`Alt-9` select, `F2` opens the list, **`Alt-Tab` /
`Alt-Shift-Tab`** go to the next / previous window. Desktops usually keep Alt-Tab for
themselves (GNOME: `org.gnome.desktop.wm.keybindings switch-applications`), so it reaches
py-screen only where the desktop lets it through, e.g. over ssh from another machine.
Inside GNU screen, screen's own `bindkey` lines may also take the Alt keys first.

### Mouse

On by default, like tmux's `mouse on`:

| Mouse | Does |
|-------|------|
| click a pane | focus it; its app gets the click too if it asked for the mouse |
| click a window in the status line | select it |
| drag a `│` separator or a caption line | resize the panes on both sides |
| wheel | goes to the pane under the pointer, focused or not; a full-screen app that didn't ask for the mouse (less, man) gets arrow keys |
| Shift+drag | select text in your terminal (most terminals; the mouse is pys's otherwise) |

Every report is routed to the pane under the pointer and rewritten in that pane's
coordinates, in the encoding the app asked for (SGR 1006, or the old X10 form). A press
grabs its pane: the drag and the release go there even if the pointer leaves it.
`MOUSE = False` in the config gives plain drags back to the terminal; the mouse is then
pys's only while the focused app asks for it (still translated per pane).

### Config

`~/.config/pys/config.py` (plain Python, read on every brain start, so `C-a R` applies an
edit): status widgets and extra key bindings. See `src/py_screen/config.py` for the helpers.

```python
WIDGETS = [cmd(["git", "-C", "{cwd}", "branch", "--show-current"], every=10), load(), clock()]
BIND = {"T": ("top", ["htop"])}   # C-a T opens a window running htop
MOUSE = True                      # False: plain drags select text in the terminal
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

- A mouse report split across two reads is passed on as keys (rare; the outer terminal
  writes each report at once).
- After a brain restart the screens are rebuilt from the keeper's last 2 MB per window; if
  that ring wrapped, older state is gone and full-screen apps get a `SIGWINCH` to repaint.
  Keeping the screens across a planned reload is open.
- Floods are bounded by pyte's speed (see "Floods").
- No scrollback view yet (M2).
- Linux (and macOS) only for now; the Windows pieces are checked by the manual CI run.
