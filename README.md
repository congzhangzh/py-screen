# py-screen

A terminal workspace of my own, in Python: not a clone of screen or tmux, built for fun,
made more powerful and improved by daily use. The design is in [`spec/`](https://github.com/congzhangzh/py-screen/tree/main/spec/); the
current version is [`spec/spec.v3.md`](https://github.com/congzhangzh/py-screen/blob/main/spec/spec.v3.md).

Status: **M1 in progress** (Linux; macOS and Windows on the platform layer, tested on CI). One server, many windows, attach/detach, hot reload, flood-safe typing.

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

Parsing is the slow part (pyte in Python; py-screen's fast paths for plain text, line
feeds and CSI sequences take it from 0.85 to 1.7 MB/s of scrolling text), so:

- **Backpressure, not dropping.** The keeper gives each window a **credit** of 256 KB
  un-acked output and stops reading that pty past it. The brain acks bytes only once it
  has parsed them, so a flooding app simply waits on its write, as under GNU screen. One
  window can't fill the shared stream, so another window's echo never waits behind it.
- **Small steps.** The brain parses in steps of 2 ms (2 KB per window per turn, shown
  windows first, the others round robin), then goes back to keys and frames.
- **Keys first.** For 100 ms after a key, only shown windows are parsed: nothing else
  stands between a key and its echo (about 2 ms median while a hidden window floods).

A flood runs at the parser's speed. With the vterm engine (libvterm, in C) that is faster
than GNU screen: `seq 1 5000000` takes 1.6 s against screen's 13.4 s, a 50 MB coloured log
0.9 s against 8.1 s ([`bench/README.md`](https://github.com/congzhangzh/py-screen/blob/main/bench/README.md) has the history).

**Screen engines.** Each window's screen is a `term.Term`; `PYS_TERM` picks the engine:

| Engine | What | When |
|---|---|---|
| `vterm` (default) | [libvterm](https://github.com/congzhangzh/py-screen/blob/main/deps/README.md)'s parser and state, compiled in, with py-screen's own screen store | When the C extension was built (needs a C compiler at install; release wheels have it) |
| `pyte` | [pyte](https://github.com/selectel/pyte) with pure-Python fast paths | Always there; about 20× slower on floods |

`PYS_TERM=vterm` makes a missing extension an error instead of a quiet fallback.

A sequence that pyte can't apply (an extra parameter, a `?` where none is defined, an
unknown erase value) is ignored, as terminals do; the bytes after it are still parsed.

## Releases

Rolling releases: every push to `main` that passes is published as `<YYYYMMDD>.<N>`, the
version and the tag alike, on [PyPI as `py-screen-mux`](https://pypi.org/project/py-screen-mux/)
and as a [GitHub release](https://github.com/congzhangzh/py-screen/releases), once, never
replaced. Pushes that change only docs, specs or bench results publish nothing. A release must pass, in [`release.yml`](https://github.com/congzhangzh/py-screen/blob/main/.github/workflows/release.yml):

- the suite on Linux and macOS, and the RPC smoke test on Windows, on both screen engines
  (vterm and pyte);
- the vterm engine built with ASan + UBSan and fuzzed against libvterm's own screen
  (a long run every night, in [`sanitize.yml`](https://github.com/congzhangzh/py-screen/blob/main/.github/workflows/sanitize.yml));
- a parse-speed check: vterm's lead over pyte must keep at least half of the newest
  recorded run's (`release/bench_gate.py`, against `bench/results/parse-*.json`);
- each wheel, built by cibuildwheel from the sdist, installed and smoke-tested on the vterm
  engine on its own system: Linux x86_64 and aarch64 (manylinux), macOS arm64 and x86_64,
  Windows x86_64.

Install with [uv](https://docs.astral.sh/uv/), which fetches Python 3.14 if needed and picks
the wheel for your system:

```bash
uv tool install py-screen-mux      # the command is pys
```

The package is `py-screen-mux` on PyPI because `py-screen` is too close to an existing
`pyscreen` there; the import is `py_screen` and the command `pys`.

Elsewhere, the sdist builds with a C compiler, or runs the pure-Python pyte engine without one.

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
| `?` | help: the windows (a digit or a click selects one), these keys, your config's bindings; Space pages when it does not fit |
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

Results of each run are kept in [`bench/results/`](https://github.com/congzhangzh/py-screen/tree/main/bench/results/), one file per run, and [`bench/README.md`](https://github.com/congzhangzh/py-screen/blob/main/bench/README.md) tells how the speed evolved, one row per run.

## Platforms

Everything that differs between systems lives in
[`src/py_screen/platform/`](src/py_screen/platform/) (spec §5.2); the keeper, brain and
client import nothing else that is OS-specific.

| | Linux / macOS | Windows |
|--|--|--|
| ptys | `pty.fork` | ConPTY via [pywinpty](https://github.com/andfoy/pywinpty) |
| keeper ↔ brain ↔ client | Unix sockets (0600) in `$XDG_RUNTIME_DIR/pys/` | named pipes `\\.\pipe\pys-<user>-<dir hash>-*` |
| background server | `setsid` | hidden console (`CREATE_NO_WINDOW`) |
| attached terminal | raw tty, SIGWINCH | console VT mode, size polled every 250 ms |
| default shell | `$SHELL` | `%PYS_SHELL%`, else `%COMSPEC%` |

CI: Linux runs the full suite on every push; macOS (full suite) and Windows
(`tests/test_rpc_smoke.py`, plus `spike/win_check.py`) run when the platform layer
changes, or by hand (`platform` workflow).

## Known gaps (M1)

- A mouse report split across two reads is passed on as keys (rare; the outer terminal
  writes each report at once).
- After a brain restart the screens are rebuilt from the keeper's last 2 MB per window; if
  that ring wrapped, older state is gone and full-screen apps get a `SIGWINCH` to repaint.
  Keeping the screens across a planned reload is open.
- Floods are bounded by pyte's speed (see "Floods").
- No scrollback view yet (M2).
- Windows: `pys attach` needs a real console (Windows Terminal or conhost, not mintty);
  a new window doesn't inherit the focused window's directory (it isn't read from the
  child yet), and the load widget is empty. The pty harness tests are POSIX-only, so
  Windows CI runs the RPC smoke test.
- Windows named pipes keep the default DACL (other local users may open them); instead
  every connection must first send a per-session key from the user-private runtime dir.
  A per-user DACL would close the pipe itself; still to do.
