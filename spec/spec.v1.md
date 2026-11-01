# py-screen: a terminal workspace of my own (spec v1)

> Working name: **py-screen**, CLI **`pys`**. Both are placeholders (see Open questions).
> Status: draft for discussion, 2026-11-01. Nothing is built yet.

## Version history

| Version | Date | What changed | Why |
|---|---|---|---|
| v1 | 2026-11-01 | First draft | The owner: "my own workspace first; not screen or tmux; for fun, more powerful, improved by daily use" |

---

## 1. Goal

A terminal multiplexer written in Python for **my own daily workspace**, first on the Linux
desk, later on the Windows and Mac rigs. It is **not** a clone of screen or tmux:

- **For fun**: a codebase I enjoy changing, in Python, with fast tests.
- **More powerful** than screen where my workflow hurts (section 3).
- **Improved by daily use**: I use it every day, every annoyance is logged in one keystroke,
  and a restart to load new code must never kill my shells (section 5).

Non-goals (for now):
- Compatibility with screen's 189 commands, `.screenrc`, or tmux's config.
- Other users, packaging for distros, a public release. (Keep the code free of private
  details anyway, so going public later stays possible; see section 10.)
- Beating screen on raw speed. It only has to *feel* as fast in daily work.

## 2. What it replaces in today's workflow

Taken from the live config (`~/.config/screen/screenrc`, read 2026-11-01). Milestone M1 is
done when every row here has an equivalent I use instead of screen.

| Today (screen) | What it does for me | py-screen equivalent |
|---|---|---|
| `C-a c` → `screen-here` | New window in the current dir | Built-in: new window inherits the focused window's cwd (read from the pty child, no helper script) |
| `C-a \|` / `C-a -` → `screen-here -v/-h` | Split + new window in the same dir | Built-in split |
| `C-a h/j/k/l`, `C-a r` resize mode, `C-a =` | Move between panes, resize | Same keys |
| `C-a x` / `C-a X` | Close pane / kill window | Same keys |
| `C-a P/A/D/B` → `prs`, `agents`, `dash`, `wts` in a window | Dashboards in their own windows | Same, and later as live widgets (section 3) |
| `M-0..9`, `C-a Space`, `C-a w`, `F2`, `C-a ,` | Window switching, list, rename | Same keys; the window list shows dir, git, last command |
| `hardstatus` with `backtick 1` (`sysusage`, every 5 s) and `backtick 2` (`screen-prsline`) | Status bar: session, windows, PR line, system usage, load, date | Status bar made of Python widgets (async functions), see 3.2 |
| `caption always` with `%h` | Per-pane title line with the hardstatus of the window | Same, fed by the shell's title escape (OSC 0/2) |
| `screen-all` (types into listed windows) | Drive many windows from a script | `pys send` over the RPC API (3.1), no keystroke injection |
| `altscreen on`, `defbce on`, `maptimeout 5`, `defnonblock 5`, mouse tracking | Correct TUIs (vim, less, claude), no Esc delay, a hung client never freezes the session | Required behaviour of the core, with tests |
| `logfile ~/screenlog.%n`, `C-a L` | Log a window to a file | Every window's output is always kept (3.3); `C-a L` exports it |
| `layout autosave` | Keep the split layout | Layout and windows survive a server restart and a reboot (3.4) |

## 3. More powerful: what screen can't do

Ordered by how much daily pain they remove. Each one ships on its own when ready.

### 3.1 A real API instead of typing into windows
The server answers JSON-RPC on its IPC channel. `pys` is a thin client:
`pys ls --json`, `pys send <win> "text"`, `pys new --cwd . -- cmd`, `pys capture <win>`,
`pys watch` (stream of events: window created, exited, bell, title changed).
screen's `-X stuff` and `-Q` are one-way and fragile; this makes `screen-all`, `agents` and
a phone companion's view of my terminal simple scripts.

### 3.2 Status bar as Python widgets
A widget is `async def widget(ctx) -> Segment`, with its own refresh interval and a timeout
so a slow one never blocks the bar. First widgets: windows, git line (from `gitctx`), PR
line, system usage, clock. A widget can also react to events (a window's bell, an agent
waiting), not only poll like `backtick`.

### 3.3 Scrollback as data
- Every window's raw output is kept on disk, per window, with a size cap. It survives
  restarts.
- With shell integration marks (OSC 133: prompt start, command start, output end), output
  is split into **command blocks**: jump to the previous prompt, copy the last command's
  output, search only outputs, see a command's exit code and duration.
- Search all windows at once ("which window printed `error: 1`?").

### 3.4 Survive restarts and reboots
- **Restart of the server (new code)**: shells keep running, nothing is lost (section 5).
- **Reboot**: shells are gone, but layout, windows, titles, cwds and scrollback come back,
  and each window shows what was running there.

### 3.5 Agent-aware windows
A window running Claude or Codex is recognised. Its state (working / waiting for me / done)
shows in the status bar and the window list, and a "waiting for me" rings once. This is what
`agents` does today by scanning processes; inside the multiplexer it sees the output
directly.

### 3.6 Later, once the above is daily-driven
- A view for the phone: `pys capture` feeds a snapshot to a phone companion (read-only;
  it never runs a second copy of anything that is already live).
- Floating popups (a quick `prs` or `git log` over the current window).
- The same workspace on the Windows and Mac rigs (M3).

## 4. Architecture

```
            ┌─────────────── client (pys attach) ───────────────┐
            │ raw keyboard in  ·  VT out  ·  resize events       │
            └────────────────────────┬───────────────────────────┘
                                     │ IPC: JSON-RPC + frame stream
┌──────────────────────── brain (server, Python) ─────────────────────────┐
│ model: sessions/windows/panes · VT emulator per window · renderer (diff) │
│ keymap · commands · widgets · plugins · RPC · persistence                │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │ pty fds / handles + raw byte ring buffers
┌──────────────────────── keeper (tiny, stable) ──────────────────────────┐
│ owns every pty master and child process · records raw output to disk     │
└──────────────────────────────────────────────────────────────────────────┘
```

Three processes:

| Process | Changes how often | Job | Size goal |
|---|---|---|---|
| **keeper** | Rarely (weeks) | Owns the pty masters and the children, copies bytes, appends raw output to the per-window log, hands pty handles to the brain | Few hundred lines, no dependencies beyond the platform layer |
| **brain** | Daily | Everything interesting: terminal emulation, layout, rendering, keys, widgets, API | Where all the fun happens |
| **client** | Rarely | Puts the terminal in raw mode, forwards keys and size, prints what the brain renders | Small |

### 4.1 Platform layer: the only place that knows the OS

| Interface | Linux / macOS | Windows | Library (🔎 to confirm in M0) |
|---|---|---|---|
| `Pty` (spawn, read, write, resize) | `os.openpty` + `fork`/`exec` (`pty` module) | ConPTY (`CreatePseudoConsole`) | stdlib / **pywinpty** |
| `Ipc` (listen, connect) | Unix socket in `$XDG_RUNTIME_DIR/pys/` | Named pipe | `multiprocessing.connection` covers both |
| `Daemon` (start detached) | double fork + `setsid` | `subprocess` with `DETACHED_PROCESS` | stdlib |
| `Handoff` (keeper → brain) | pass fds over the Unix socket (`SCM_RIGHTS`, `socket.send_fds`) | `DuplicateHandle` into the brain's process | stdlib / `pywin32` or `ctypes` |
| `Term` (raw mode, size, resize events) | termios, `SIGWINCH` | `SetConsoleMode` with VT input/output, console resize events | stdlib / `ctypes` |
| `ProcInfo` (cwd, command of a pane) | `/proc/<pid>/cwd` (Linux), `libproc` (macOS) | Shell integration (OSC 7) only | stdlib |

Rule: the core imports only these interfaces. A test runs the core on a fake platform
(in-memory pty), so most tests need no real terminal.

### 4.2 Core choices
- **Python 3.14**, a `uv` project, installed with `uv tool install -e .` so a `git pull`
  plus brain restart is the whole deploy.
- **asyncio** everywhere (selector loop on POSIX, Proactor on Windows).
- **Terminal emulator**: start with **pyte**; replace or fork it when it gets in the way
  (alt screen, OSC 133, wide CJK characters must be right: I read Chinese and Japanese
  in it every day).
- **Renderer**: writes VT sequences directly and only the cells that changed. No curses
  (it isn't on Windows, and it hides what we need to control).
- **Config is Python** (`~/.config/pys/config.py`): keys, widgets, colours. Reloaded on
  save without restarting anything.

## 5. Built for daily change

The main design rule: **a code change must cost me nothing in my running work.**

1. **Hot restart.** `pys reload` (or a crash) restarts only the brain. The new brain gets
   the pty handles from the keeper, replays each window's recent raw output into a fresh
   emulator, and redraws. Shells, vim and Claude never notice. Target: under 1 s.
2. **Crash = restart + report.** If the brain dies, the keeper starts it again and keeps a
   crash report (traceback, last events). The screen comes back by itself.
3. **Gripe key.** `C-a !` asks for one line and appends it to `~/.local/state/pys/gripes.md`
   with context (time, window, command, version). Once a week: read them, fix the top one.
4. **Every session is a test case.** The keeper's raw logs are recordings: any rendering
   bug I hit becomes a test by replaying that recording and comparing the screen to a
   snapshot (golden tests).
5. **screen stays as the fallback** until py-screen has been my only multiplexer for two
   weeks with no reason to go back. Nothing in `shell_env/` changes before then.

## 6. Milestones

Each milestone ends with something I can use, and is released on its own.

| # | Name | Done when | Rough size ⚠️ guess |
|---|---|---|---|
| **M0** | Spike: keeper + brain + client, one window, Linux | Attach, detach, reattach; hot restart keeps a running `vim`; throughput measured (8.1). Separate check on the Windows rig: pywinpty spawn + named pipe attach + handle handoff work | 1–2 weeks of evenings |
| **M1** | Daily driver on Linux | Every row of section 2 works; I use it for a full day without going back to screen | 3–5 weeks |
| **M2** | Powers, part 1 | API (3.1), widgets (3.2), scrollback as data (3.3), survive reboot (3.4) | each ships alone |
| **M3** | Other machines | Same workspace on the Windows rig, then the Mac rig | 2–4 weeks |
| **M4** | Powers, part 2 | Agent-aware windows (3.5), phone view, popups (3.6) | each ships alone |

## 7. Testing

- **Unit**: core on the fake platform: layout, keymap, commands, renderer diffs.
- **Emulator golden tests**: recorded byte streams → expected screen (text, colours,
  cursor). Include vim, less, `htop`, Claude's UI, CJK text, wide characters, alt screen.
- **Live tests**: a real shell in a real pty, driven through the API (`pys send`,
  `pys capture`).
- **Hot-restart test**: start `vim` and `sleep 1000`, restart the brain 20 times, both are
  still there and the screen is identical.
- **Speed tests** as a benchmark that runs on every change (8.1).
- CI on Linux first; Windows and macOS runners from M3.

## 8. Risks and how we find out early

### 8.1 Speed (the big one)
screen parses every byte in C; pyte parses in Python. A flood of output (a build log,
`cat` of a big file) might lag or eat CPU. In M0, measure, inside screen and inside
py-screen, on this machine:

| Test | Measures |
|---|---|
| `time seq 1 5000000` | Throughput of plain lines |
| `time cat` of a 50 MB log with colours | Throughput with escape sequences |
| Key → echo latency while another window floods | Does a busy window slow down typing? |
| CPU of the brain while idle with 10 windows | Cost of just being there |

If it's too slow: (1) when a window floods, parse only the tail (the screen can't show
more than one page anyway; the keeper still logs every byte), (2) render at most 60 frames
per second, (3) only then rewrite the parser's hot loop in Rust (pyo3) or Cython.

### 8.2 Windows
ConPTY rewrites output on its own (it re-renders the console), handle handoff between
processes is less common than fd passing, and named pipes behave differently from Unix
sockets. M0 has a Windows check for exactly this, before M1 builds on assumptions.
pymux stalled here (its issue #58: "the pty isn't the only thing to port").

### 8.3 Scope
"More powerful" can grow without end. Rule: a power is built only when a gripe (5.3) or
a real daily need asks for it, and it ships on its own.

## 9. Coexistence while it is being built
- py-screen will often run *inside* screen during development. Its default prefix stays
  `C-a` (my habit); inside screen I use a different dev prefix (open question 3).
- The dev build uses its own socket dir and state dir, so it never touches a daily-driven
  install.

## 10. License and privacy
- py-screen copies no code from screen (GPLv3) or tmux; it reimplements behaviour, so the
  license is our choice. Dependencies to check: pyte (🔎 LGPL), pywinpty (🔎 MIT).
- Private repo for now. No hostnames, home paths or private repo names in code, tests or
  recordings committed to the repo (recordings are made in a sandbox shell with a neutral
  prompt). The privacy-scan pre-push hook goes in from the first commit.

## 11. Open questions

1. **Name.** "py-screen" says what it was born from, not what it is. Keep it, or pick a
   name before the first commit?
2. **One server for all sessions, or one per session?** One server makes the API and
   cross-window search simple; one per session limits a crash to that session. With the
   keeper/brain split, a crash already loses no shells, so v1 proposes **one server**.
3. **Dev prefix** while running inside screen: `C-]`, `C-s`, or `C-b`?
4. **Where it lives.** A new private repo `py-screen`, with a `heads` file like the others?
5. **Python only, or allow Rust from the start** for the keeper (stable, tiny, fast)? v1
   proposes pure Python until M0's numbers say otherwise.

## 12. Next step
M0, the spike: about 300 lines for keeper, brain and client on Linux, the four speed tests
against screen, and the Windows check on the rig. Its numbers decide 8.1 and open question 5.

> Committed copies are lightly redacted for a public repo (no names of private projects); the originals are kept outside the repo.
