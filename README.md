# py-screen

**A terminal multiplexer in Python, in the family of GNU screen and tmux:** windows and
splits that keep running when you detach, and a server you can restart, with new code or a
new config, without losing a single shell.

![A window floods two million lines, a split runs a clock, the window list opens, and `pys reload` restarts the brain while the clock keeps ticking](https://raw.githubusercontent.com/congzhangzh/py-screen/main/docs/demo.v2.gif)

With AI agents: `pys-agents` shows which window runs which Claude Code session, and after
`pys kill` (or a crash) `pys-sessions restore` opens them all again, each with its session and
folder ([For AI agents](#for-ai-agents)):

![Two agents in pys windows; pys-agents lists them; pys kill ends everything; pys-sessions restore opens both again, each with its session and folder](https://raw.githubusercontent.com/congzhangzh/py-screen/main/docs/demo-agents.v3.gif)

A terminal workspace of my own, in Python: not a clone of screen or tmux, built for fun,
made more powerful and improved by daily use. The design is in [`spec/`](https://github.com/congzhangzh/py-screen/tree/main/spec/); the
current version is [`spec/spec.v3.md`](https://github.com/congzhangzh/py-screen/blob/main/spec/spec.v3.md).

```bash
uv tool install py-screen-mux     # the command is pys; uv fetches Python 3.14 if needed
pys                               # attach, starting the server on first use
```

No uv or Python? Each [release](https://github.com/congzhangzh/py-screen/releases) has a
ready-to-use bundle per system, with its own Python inside: unpack it and run `bin/pys`.

## Why py-screen

- **Restart without losing anything.** A tiny *keeper* owns every pty; the *brain* (screens,
  keys, rendering) is a separate process. `pys reload` (or `C-a R`) starts a new brain with
  new code or config, and your shells, editors and builds never notice.
- **Nothing is dropped under a flood.** Every byte a window writes is parsed, in order,
  shown or hidden, with backpressure instead of dropping. Apps that redraw by diff, such as
  AI coding agents, stay correct. With its libvterm engine, `seq 1 5000000` takes 1.6 s
  against GNU screen's 13.4 s.
- **Made to be driven by scripts and agents.** `pys ls --json`, `pys send`, `pys capture`
  and `pys title` (a window names itself, and a rename by hand still wins) make every window
  readable and writable from outside. An app's OSC 52 copy reaches your clipboard.
  For Claude Code there is more: `pys-agents` shows which window runs which session, and
  `pys-sessions` brings them all back after pys died (see [For AI agents](#for-ai-agents)).
- **Configured in Python.** `~/.config/pys/config.py` adds status widgets and key bindings
  (`pys sample-config` prints a starting point).
- **Linux, macOS and Windows** (ConPTY), with wheels for six systems, each one tested on
  its own system before a release.

## Quick start

| Do | Keys (prefix `C-a`) |
|---|---|
| new window / split side by side / split stacked | `c` / `\|` / `-` |
| next / previous / pick from a list | `n` / `p` / `w` |
| scroll back | mouse wheel, or `Shift-PgUp` |
| copy mode | `[` (search with `/`, select with `v`, copy with `y`) |
| detach, then attach again | `d`, then `pys` |
| every key | `?` |

**Status:** milestone M1, used every day by its author on Linux; macOS and Windows are
tested on CI. It is young: tmux and GNU screen do much more (sessions by name, many
clients with their own views, a huge set of options). See [Known gaps](#known-gaps-m1).

## Use

```bash
uv tool install -e .       # puts `pys` in ~/.local/bin, running this checkout (edits apply on reload)
pys                        # attach (starts the server on first use)
pys ls [--json]     # windows
pys new [--select] [-- cmd …]
pys send 0 'make test' --enter
pys capture 0       # the window's screen as text
pys title 'release · kit · #12 libfuse'   # name the window this runs in ($PYS_WINDOW)
pys title --wid 3 --clear                 # or another one; no text: print it
pys title --owner $PPID 'review · pys'    # dropped when that process ends
pys reload          # new brain, same shells
pys kill            # end everything
```

A window shows the first title that is set, in this order: one set by hand (prefix `,`, or
a `BIND` entry, saved with the layout), then one set by a program with `pys title` (also
kept across a reload), then the app's own (OSC 0/2), then the command's name. So an agent
can name its window, the app's spinner titles don't cover it, and a rename by hand still
wins; renaming to nothing shows the agent's title again. `pys ls --json` gives all three
per window (`title`, `agent_title`, `app_title`) next to the `name` it shows. A `pys
title` is one line of printable text, at most 200 characters.

An agent title outlives the agent that set it unless it names an owner: with `--owner PID`
the brain drops the title once that process is gone (checked every 2 s; on Linux by the
process's start time too, so a reused PID doesn't count). In Claude Code's Bash tool
`$PPID` is `claude`, so `--owner $PPID` ties the title to the session. Without `--owner`
it stays until cleared or the window closes. The owner is a PID as the brain sees it, so
it means nothing from inside a PID namespace (a sandbox with its own PIDs).

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
| `S` | hide / show the widgets on the right of the status line, so the window tabs get the whole line (kept across a reload; `SHOW_WIDGETS = False` in the config starts hidden) |
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
| wheel, touch drag | goes to the pane under the pointer, focused or not: an app that asked for the mouse gets it; a full-screen app that didn't (less, man) gets arrow keys; anything else (a shell, Claude Code) scrolls back through its history (see Scrollback) |
| Shift+drag | select text in your terminal (most terminals; the mouse is pys's otherwise) |

Every report is routed to the pane under the pointer and rewritten in that pane's
coordinates, in the encoding the app asked for (SGR 1006, or the old X10 form). A press
grabs its pane: the drag and the release go there even if the pointer leaves it.
`MOUSE = False` in the config gives plain drags back to the terminal; the mouse is then
pys's only while the focused app asks for it (still translated per pane).

### Scrollback

Each window keeps the rows that scroll off the top of its screen (`SCROLLBACK`, 5000 by
default; the alternate screen of full-screen apps keeps none, as in xterm). The wheel or a
touch drag (a phone's terminal sends it as wheel reports), or Shift-PgUp / Shift-PgDn,
scroll the pane back and forth through them; the caption shows `[rows back/rows kept]`.
The view is only a view: the app keeps running and drawing (Claude Code's input box
included), and output that arrives meanwhile doesn't move the rows you are reading. Any
key that goes to the app takes the pane back to live. `ESC [ 3 J` (what `clear` sends)
empties the history. `pys capture --history` prints it.

### Copy mode

Prefix `[` (or prefix `Esc`) puts a cursor on the focused pane, over its history and
screen, as GNU screen and tmux do; the caption says `[copy back/kept]` and the status line
lists the keys. The pane is frozen as it was at the start, as in tmux: the app keeps
running and its output is kept, but it shows only when copy mode ends, so an app that
redraws in place (a spinner, `top`, Claude Code's fullscreen mode) doesn't change the text
under the cursor. An app on the alternate screen (`alt` in `pys ls --json`) keeps no
history in pys: copy mode reaches only its one screen, and its own history is the app's
to scroll (Claude Code: PgUp / PgDn, the wheel).

| Keys | Do |
|------|----|
| `h j k l`, arrows, wheel | move (the view follows the cursor) |
| `0 ^ $`, `w b` | line start / first word / end, next / previous word |
| `C-u C-d`, `C-b C-f`, `PgUp PgDn` | half a page, a page |
| `g G` | the oldest row, the newest row |
| `/ ?`, `n N` | search down / up (lower case matches any case), again the same / other way; the match is marked yellow |
| `v` or `Space` | start or drop a selection |
| `y` or `Enter` | copy the selection (without one, the cursor's row) and leave |
| `Esc`, `q`, prefix | drop the selection; leave |

A copy is kept for prefix `]`, which types it into the focused window (as a bracketed
paste when the app asked for one), and is sent to your terminal as OSC 52, which puts it
on the system clipboard in terminals that allow it (kitty, ghostty, WezTerm, iTerm2,
Windows Terminal, foot; not GNOME Terminal, and not through a GNU screen or tmux around
pys). It goes through ssh too, since it is only bytes on the way to your terminal.

An app can copy the same way: an OSC 52 from any window, shown or not, is treated as a
copy, so what Claude Code or vim (over ssh, with a plugin) copies gets to the terminal
you are attached from. An app can't read the clipboard: pys doesn't answer the OSC 52
query. A copy over about 750 KB is dropped whole. `Shift+drag` is another path: your
terminal selects by itself and pys never sees it, so copy with your terminal's own key.

`pys ls --json` says per window whether it is in copy mode, how far it is
scrolled, how much history it keeps, and which mouse mode (`1000`/`1002`/`1003`, `0` =
none) and screen (`alt`) its app asked for: an app with a mouse mode gets the wheel itself.

A row costs about 36 bytes per cell up to its last visible one: a window full of
100-column lines holds ~18 MB at 5000 rows. Keeping rows has a price on floods too: about
0.2 µs a line (see `bench/README.md`).

### Config

`~/.config/pys/config.py` (plain Python, read on every brain start, so `C-a R` applies an
edit): status widgets, the caption line, extra key bindings. See `src/py_screen/config.py` for
the helpers.

```
pys doctor                 # where it is, and a check: it loads it as the brain does, runs each
                           # widget and the CAPTION once (text, time, error), and finds typos
                           # (WIDGET for WIDGETS: pys never reads it), an ON_CHANGE not on PATH
pys doctor --init          # write the sample there; never over yours
pys doctor --reset         # keep yours as config.py.<YYYYMMDD-HHMMSS>, then write the sample
```

**The caption line** (the line under each pane: its number and name, then its folder) takes
`CAPTION` in place of the folder: any object with `.every` and `async update(ctx) -> str`, run
for each window on screen (`ctx`: `cwd`, `wid`, `pid`), at most 5 s per update.
`caption()` from `py_screen.agents.widget` shows where the work is: the folder and its git
branch and changes, and for a window with an agent in it, the repo the agent last wrote in (✎),
not the folder it started in:

```python
from py_screen.agents.widget import caption
CAPTION = caption()        # " 3 build fix  ✎ ~/src/app-fix  fix-build wt 1 mod "
```

```python
WIDGETS = [cmd(["git", "-C", "{cwd}", "branch", "--show-current"], every=10), load(), clock()]
BIND = {"T": ("top", ["htop"])}   # C-a T opens a window running htop
MOUSE = True                      # False: plain drags select text in the terminal
SCROLLBACK = 5000                 # rows of history per window; 0 = none
```

`pys sample-config` prints a fuller sample to start from (`pys sample-config >
~/.config/pys/config.py`): CPU, memory and network (down/up in kB/s) widgets and a clock
that ticks on each second, written in the config itself, and `branch()` from
`py_screen.agents.widget`: the focused window's git branch, worktree and changes, and for a
window with an agent in it, the repo the agent last wrote in (✎, see
[For AI agents](#for-ai-agents)). A widget is any object with `.every`, `.text` and
`async update(ctx) -> str`, so new ones need no change to pys; `ctx` is the focused window:
`cwd`, `wid` and `pid` (its process).
CPU, memory, network and disk (read/write in kB/s) read `/proc` on Linux; on macOS and
Windows they use psutil (`uv tool install 'py-screen-mux[stats]'`) and show nothing without it.

The layout (splits, focus, renamed titles) is saved on every change, so a reload keeps it.

### What the windows were: ON_CHANGE and the server record

`pys kill` and prefix `Q` end every window and delete the layout. Two things keep what they
were, so a tool can bring them back:

- **`ON_CHANGE`** in the config, a command (an argv list), run about 2 s after a change (a
  window opens or exits, a title changes, the brain starts) with `PYS_EVENT=change`, and once
  before `pys kill` / `Q` with `PYS_EVENT=kill`, while every window still runs; the kill waits
  for it at most `KILL_HOOK_TIMEOUT` seconds (5), then ends as before. One run at a time
  (changes during a run give one more run after it); `PYS_DIR` is set, so `pys ls --json`
  inside it reaches this session; its output goes to `server.log`.
- **The server record**: on the same changes and at a kill, the brain writes
  `$XDG_STATE_HOME/pys/servers/<start YYYYMMDD-HHMMSS>-<server pid>.json` (default
  `~/.local/state`, a session with its own `PYS_DIR` too, since the runtime dir is gone at the
  next boot): the server's pid, `started`, `updated`, `ended` (set when it ends by a kill or
  `Q`, with the windows it ended; by its last window, with none; `null` while it runs or if
  it died), and each window's `wid`, `pid`,
  `cmd`, `cwd` (its shell's current directory), `name`, `title`, `agent_title`,
  `agent_owner`. One file per server life, rewritten whole (tmp + rename), never deleted.

```python
ON_CHANGE = ["pys-sessions", "save", "--quiet"]   # comes with pys (below), or a script of
                                                  # your own: an argv list
KILL_HOOK_TIMEOUT = 5
```

Sockets live in `$XDG_RUNTIME_DIR/pys/` (`PYS_DIR` overrides; keep it short: an AF_UNIX
path is limited to 108 bytes). Server log: `server.log` in that dir.

Each window gets `PYS_WINDOW` (its number) and `PYS_SESSION` (its server's dir). `pys`
typed in a window of the same session refuses to attach: the session would show inside
itself and the keys would loop between the window and the server. Another session
(`PYS_DIR=<dir> pys`) may be nested; give it its own prefix (`PYS_PREFIX`).

## For AI agents

Agents such as Claude Code often run in pys windows, several at a time. pys comes with two
commands for them and two skills that teach an agent to use pys well. They are built only on
the commands above, so the core knows nothing of them. Claude Code for now (Codex later);
Linux for now (they read `/proc`).

![Two agents in pys windows; pys-agents lists them; pys kill ends everything; pys-sessions restore opens both again, each with its session and folder](https://raw.githubusercontent.com/congzhangzh/py-screen/main/docs/demo-agents.v3.gif)

(The agents in the demo are stand-ins that write what Claude Code writes; it is recorded in a
container with `docs/record-demo-agents.sh`.)

**`pys-agents`**: every running claude, the window it runs in, and how it is doing:

```
$ pys-agents
WIN  TITLE              STATE  CTX   COMPACT  MODEL     NAME      FOLDER
0    review PR 42       idle   237k  0        opus-5-5  reviewer  ~/src/app
2    build fix          busy   844k  1        opus-5-5  builder   ~/src/app
-    -                  idle   12k   0        opus-5-5  -         ~/notes
```

CTX is what the last call held (input, cache read and written, from the transcript), COMPACT
the compactions so far; a claude outside pys shows window `-`. `--json` gives one object per
claude (`window`, `pid`, `session`, `name`, `state`, `cwd`, `context_tokens`, `model`,
`compactions`, and `titled_by_it` when it named its window with `pys title --owner`).

**`pys-agents where`**: every window, agent or not, with the git state of its folder:

```
$ pys-agents where
WIN  TITLE         FOLDER              BRANCH                       STATE        AGENT
0    review PR 42  ~/src/app           main @a96cd56                clean ↑2     claude reviewer (idle)
1    build fix     ✎ ~/src/app-fix     fix-build @1f0e2c1 (wt)      1 mod 3 new  claude builder (busy)
2    -             ~/notes             -                            -            -
✎ = the repo of the agent's latest writes (its transcript), not the folder it started in
(wt) = a linked worktree (git worktree add) of that repo
```

An agent's folder is not where it works: Claude Code keeps the folder it was started in, its
tools take absolute paths anywhere, and its Bash shell goes back to the start folder after
every command. So for an agent, `where` looks at what it did: the files it last wrote (Write,
Edit) and the `cd <dir> && …` its Bash commands start with, newest first, and shows the first
one inside a git repo (✎). A Read is not work, and a folder outside any repo is skipped.
`--json` keeps both: `cwd` (where it started) and `works_in`.

STATE is `git status` against the upstream (↑ ahead, ↓ behind, as of the last fetch); git runs
with `--no-optional-locks`, so it never takes the index lock from under you. `--json` gives
`git: {repo, top, common, worktree, branch, head, changed, untracked, ahead, behind}` per
window.

**`pys-sessions`**: keep what every window is, and open it all again after pys died (a
kill, a crash, a reboot):

```python
# ~/.config/pys/config.py
ON_CHANGE = ["pys-sessions", "save", "--quiet"]   # a snapshot at each change and before a kill
SESSIONS_KEEP_ENV = []    # variables of a claude's environment its resume gets back
```

| Command | What it does |
|---|---|
| `pys-sessions save` | snapshot each window: folder, titles, git repo / branch / HEAD / worktree, the Claude Code session in it with the flags it was started with, the last 3000 lines of its screen; for an agent, also where it worked (`works_in`, as in `where`); only when something changed, or every 2 h |
| `pys-sessions list` / `show [SNAP]` | the snapshots, what one holds |
| `pys-sessions restore [SNAP] [--only W,W] [--yes]` | a dry run, then each window again: `claude <its flags> --resume <id>`, or a shell in its folder; title `↺<old title>` until the agent sets its own. The dry run says which branch each window was on, and when a worktree's folder is gone since, the `git worktree add` that brings it back |
| `pys-sessions forensic [--write]` | no snapshot of the dead server? rebuild it from its server record (folders, titles) and from what Claude Code leaves behind (sockets, transcripts), with a `why` per window |
| `pys-sessions install` | a systemd user timer running `save` every 2 minutes, besides ON_CHANGE |

Snapshots go to `~/.local/state/pys-sessions/<YYYYMMDD-HHMMSS>/` (`PYS_SESSIONS_DIR`) and are
never deleted or overwritten.

**Skills.** `pys-agents skills --install` links them into `~/.claude/skills` (one of the same
name that is already there is left alone):

- `pys-drive`: name your window, find the other sessions, read a window, type into one only
  when it is idle (text, then Enter on its own: an Enter inside a paste is a new line), never
  `pys kill`, test pys on a second server.
- `pys-restore`: bring the windows back after pys died, step by step, showing the plan first.

Both commands read Claude Code's own files (`~/.claude/sessions/<pid>.json`, the transcripts
under `~/.claude/projects/`; `CLAUDE_CONFIG_DIR` moves them). Those are not a documented
interface, so the reads are defensive: after a Claude Code update a field may show `-`
until pys catches up.

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

A tag is a release. Pushing a tag `<YYYYMMDD>.<N>` on a commit of `main` publishes that
version on [PyPI as `py-screen-mux`](https://pypi.org/project/py-screen-mux/) and as a
[GitHub release](https://github.com/congzhangzh/py-screen/releases), once, never replaced:

```bash
git tag 20261106.1 <sha> && git push origin 20261106.1
```

Every push, on every branch, and every pull request runs the same tests, in
[`ci.yml`](https://github.com/congzhangzh/py-screen/blob/main/.github/workflows/ci.yml), and
publishes nothing. A release runs them again, then builds, in
[`release.yml`](https://github.com/congzhangzh/py-screen/blob/main/.github/workflows/release.yml),
and must pass:

- the suite on Linux and macOS, and the RPC smoke test on Windows, on both screen engines
  (vterm and pyte);
- the vterm engine built with ASan + UBSan and fuzzed against libvterm's own screen
  (a long run every night, in [`sanitize.yml`](https://github.com/congzhangzh/py-screen/blob/main/.github/workflows/sanitize.yml));
- a parse-speed check: vterm's lead over pyte must keep at least half of the newest
  recorded run's (`release/bench_gate.py`, against `bench/results/parse-*.json`);
- each wheel, built by cibuildwheel from the sdist, installed and smoke-tested on the vterm
  engine on its own system: Linux x86_64 and aarch64 (manylinux), macOS arm64 and x86_64,
  Windows x86_64 and ARM64;
- a ready-to-use bundle per system (below), built from that wheel, unpacked into another
  folder and smoke-tested there with no uv or Python on `PATH`.

Install with [uv](https://docs.astral.sh/uv/), which fetches Python 3.14 if needed and picks
the wheel for your system:

```bash
uv tool install py-screen-mux      # the command is pys
```

The package is `py-screen-mux` on PyPI because `py-screen` is too close to an existing
`pyscreen` there; the import is `py_screen` and the command `pys`.

Or take the **ready-to-use bundle** for your system from the
[release](https://github.com/congzhangzh/py-screen/releases): `pys-<version>-<os>-<arch>.tar.gz`
(`.zip` on Windows) holds its own Python 3.14, pys and its dependencies (pyte, psutil), so
there is nothing to install and no network needed. Unpack it anywhere and run `bin/pys`
(`pys.cmd` on Windows); the folder still works after you move it. `release/bundle.py` builds
it: python-build-standalone's CPython (the one uv installs) with the release's wheel
installed into it, a launcher in place of pip's scripts (those hold an absolute path), and
Tk, IDLE and the C headers left out (about 35 MB packed). On macOS, a browser download is
quarantined: `xattr -dr com.apple.quarantine pys-*` before the first start.

Elsewhere, the sdist builds with a C compiler, or runs the pure-Python pyte engine without one.

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

CI: every push runs the suite on Linux and macOS and `tests/test_rpc_smoke.py` on Windows.
`spike/win_check.py` runs too when the platform layer changes, or by hand (`platform`
workflow).

## Known gaps (M1)

- A mouse report split across two reads is passed on as keys (rare; the outer terminal
  writes each report at once).
- After a brain restart the screens are rebuilt from the keeper's last 2 MB per window; if
  that ring wrapped, older state is gone and full-screen apps get a `SIGWINCH` to repaint.
  Keeping the screens across a planned reload is open.
- Floods are bounded by pyte's speed (see "Floods").
- The scrollback isn't rewrapped on a resize; copy mode selects character-wise only
  (no line or block selection), and the paste buffer is lost on a brain reload.
- Windows: `pys attach` needs a real console (Windows Terminal or conhost, not mintty);
  a new window doesn't inherit the focused window's directory (it isn't read from the
  child yet), and the load widget is empty. The pty harness tests are POSIX-only, so
  Windows CI runs the RPC smoke test.
- Windows named pipes keep the default DACL (other local users may open them); instead
  every connection must first send a per-session key from the user-private runtime dir.
  A per-user DACL would close the pipe itself; still to do.

## Contributing

Issues with a way to reproduce them are welcome, and so are pull requests. To work on it:

```bash
git clone https://github.com/congzhangzh/py-screen && cd py-screen
uv tool install -e .     # pys runs this checkout; `pys reload` loads your edits
uv run pytest            # the suite, about a minute
```

Every push and pull request runs the suite on Linux and macOS and the RPC smoke test on Windows
([`ci.yml`](https://github.com/congzhangzh/py-screen/blob/main/.github/workflows/ci.yml)).
A commit's subject says what changed for a user; it is what the release notes list.
The demo above is recorded from [`docs/demo.tape`](https://github.com/congzhangzh/py-screen/blob/main/docs/demo.tape)
with [vhs](https://github.com/charmbracelet/vhs): `docs/record-demo.sh` runs it in a clean
environment, as a made-up user in a session of its own. A new take gets a new version
(`demo.v3.gif`) beside the old one.

## License

MIT ([`LICENSE`](https://github.com/congzhangzh/py-screen/blob/main/LICENSE)). The bundled
libvterm is MIT as well ([`deps/libvterm/LICENSE`](https://github.com/congzhangzh/py-screen/blob/main/deps/libvterm/LICENSE)).
