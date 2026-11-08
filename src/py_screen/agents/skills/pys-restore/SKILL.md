---
name: pys-restore
description: Bring back the pys (py-screen) windows that were open before pys died (killed, crashed, `pys kill`, a reboot) - each window's folder, title and the Claude Code session in it, resumed with its own flags; plain shells reopen in their folder with their last screen kept. Use for "restore my pys", "restore the last session", "I lost my pys windows", "pys died", "bring back my sessions". Also sets up the snapshots that make it possible (`pys-sessions save` from ON_CHANGE, or a 2-minute timer).
---

# pys-restore: the windows come back with their sessions

pys keeps its windows in memory. What outlives it:

- the **server record** that pys writes itself, `~/.local/state/pys/servers/<start>-<pid>.json`:
  each window's command, folder and titles;
- the **snapshots** of `pys-sessions save` (comes with pys), `~/.local/state/pys-sessions/<YYYYMMDD-HHMMSS>/`:
  also the Claude Code session in each window, with the flags it was started with, and the
  last 3000 lines of each screen. Never deleted or overwritten.

| Command | What it does |
|---|---|
| `pys-sessions save` | snapshot every window, only when something changed (or every 2 h) |
| `pys-sessions list` / `show [SNAP]` | the snapshots; what one holds (`why` lines for a forensic one) |
| `pys-sessions restore [SNAP] [--only W,W] [--yes]` | a dry run, then opens each window again: `claude <its flags> --resume <id>`, or a shell in the folder; title `↺<old title>` until the agent sets its own; skips a session that already runs |
| `pys-sessions forensic [--write]` | no snapshot of the dead server? rebuild it from its server record and from the evidence Claude Code leaves |
| `pys-sessions install` | a systemd user timer running `save` every 2 minutes (Linux; elsewhere ON_CHANGE) |

## Steps

1. Show what is open now and what was lost: `pys ls`, `pys-sessions list`.
2. Pick the snapshot. With no argument, `show` / `restore` take the last snapshot of an
   *earlier* pys server. No snapshot of the dead server, or one older than the loss? Run
   `pys-sessions forensic` and read every `why` line before you trust it; `--write` saves it.
3. `pys-sessions restore <SNAP>` (the dry run) → show the user the table: old window → folder,
   title, session, and the git branch it was on. A `⚠ folder gone` line means a worktree was
   removed since: it prints the `git worktree add` that brings it back; ask before running it. Say for each row whether it comes from a snapshot (sure) or forensic (a
   guess with evidence).
4. On the user's go (or when they asked for the restore in so many words), add `--yes`;
   `--only` leaves out windows they don't want.
5. Check: `pys ls` (or `pys-agents`) shows the new windows. A resumed claude waits, idle, until
   spoken to: a resume sends no prompt. Tell the user where each window's last screen is
   (`zless <snapshot>/screen-N.txt.gz`).
6. Make sure the next loss is covered: `ON_CHANGE = ["pys-sessions", "save", "--quiet"]` in
   `~/.config/pys/config.py` (a snapshot at each change and right before a kill), and/or the
   timer (`systemctl --user list-timers pys-sessions.timer`, else `pys-sessions install`).

## How forensic works

- pys's `server.log` (in its runtime dir) lists `spawned <wid> <shell pid>` and `exited <wid>`,
  times only. The windows open when the last server died = spawned minus exited between its
  `listening … pid N` line and the next. Its server record (`…-N.json`) gives their folders and
  titles for sure.
- A claude killed with its window leaves `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`; its mtime is
  when that claude started. Its window: the newest window open then whose shell pid is
  smaller (pids grow).
- Its transcript (`~/.claude/projects/*/<id>.jsonl`, never a running session's): one born from
  1 min before to 5 min after that start; if none, a resume of the session the window ran
  first, when that transcript has entries within 2 min of the start.
- Flags: the longest `<pid> claude …` line in any transcript (a `ps` listing), else the
  transcript's last permission mode and custom title → `--permission-mode`, `--name`.
- These are Claude Code's own files, not a documented interface: after a Claude Code update,
  check the result by hand.

## Notes

- Never `pys kill` to test this: it ends every window. Test on a second server with
  `PYS_DIR=<dir> XDG_STATE_HOME=<dir>/state PYS_SESSIONS_DIR=<dir>/snaps`; the timer only
  snapshots the default server.
- Variables a resumed claude should get back from the one that ran (only these: an
  environment holds secrets): `SESSIONS_KEEP_ENV = ["MY_VAR"]` in the pys config.
- Codex sessions are saved and resumed too (`codex resume`), but untested.
