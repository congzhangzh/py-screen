---
name: pys-drive
description: Work inside py-screen (pys), the terminal multiplexer, as an agent - name your own window so the user sees what it is for, find which window runs which Claude Code session (`pys-agents`), read another window's screen, type into one without breaking what the user or another agent is doing, and test pys itself on a second server so the user's live session is never touched. Use when you run in a pys window ($PYS_WINDOW is set), when asked to look at, name, watch or type into a pys window, or to check on other agent sessions.
---

# pys-drive: an agent's manners in a pys session

You are in pys when `$PYS_WINDOW` (your window's number) and `$PYS_SESSION` (its server's
folder) are set. The user's live session holds their work, often other agents too: everything
below reads freely and changes carefully.

## Name your window

```bash
pys title --owner $PPID "<role> <what you work on>"     # e.g. "review PR 42", "build fix"
```

In Claude Code's Bash tool `$PPID` is `claude` itself, so the title belongs to this session
and goes away when it ends (pys checks the owner's pid and start time). Keep it short, about
24 characters: the status line shows every window's whole title. Set it again when the work
changes. A title the user set by hand (prefix `,`) wins over yours; leave it alone.

## See what runs where

| Command | Gives |
|---|---|
| `pys-agents` | every running claude: window, title, state (busy / idle), context size, compactions, model, name, folder |
| `pys-agents --json` | the same as JSON |
| `pys ls --json` | every window: `wid`, `pid` (its shell), `cmd`, `cwd`, `name` (what shows), `title`, `agent_title`, `app_title`, `agent_owner` |
| `pys capture <wid>` | the window's screen now, as text; `--history` adds the scrollback, `--json` the cursor too |

## Type into a window: only when it is safe

`pys send <wid> -- "<text>"` types into a window as if the user did. Before you do:

1. **Is it yours to type into?** Only with the user's word, or when the window runs a tool
   you started. Never into the user's own shell or editor on a guess.
2. **Is it idle?** For a claude: `pys-agents` must not show it `busy`, and in
   `pys capture --json <wid>` the cursor (`cursor: [col, row]`) must sit right after the `❯`
   of its input box with nothing typed before it (`lines[row][:col].strip() == "❯"`). Ghost
   text after the cursor ("Try …") is a placeholder, not a draft. Never type into a dialog,
   a running turn or the user's draft.
3. **One line, then Enter on its own:** `pys send <wid> -- "<text>"`, wait about 0.4 s, then
   `pys send --enter <wid> ""`. Claude Code reads a fast burst of keys as a paste, and an
   Enter inside a paste is a new line, not a submit. Strip control characters from the text.
4. Read the screen again to see it arrived.

## Never

- `pys kill`, or prefix `Q`: they end **every** window, the user's and other agents' too.
- `pys reload` without being asked: it restarts the brain (the windows live on), but a user in
  the middle of something sees the screen redrawn.
- `pkill -f pys` or anything like it: the pattern matches your own shell's command line too.

## Test pys on its own server

```bash
export PYS_DIR=$HOME/scratch/pys-test XDG_STATE_HOME=$HOME/scratch/pys-test/state
pys new sh            # starts a second server, apart from the user's
pys ls --json
pys kill              # ends only this one: PYS_DIR points at it
```

Give a second session its own prefix (`PYS_PREFIX=C-]`) when you attach to it from inside pys.

## After a crash

If pys died and the windows are gone, use the `pys-restore` skill.
