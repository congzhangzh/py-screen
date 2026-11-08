"""Tools for AI coding agents that work in pys windows (Claude Code for now).

Two commands, built only on pys's public interface (`pys ls --json`, `capture`, `new`,
`title --owner`, the ON_CHANGE hook and the server record), so the brain and the keeper never
import anything from here:

  pys-agents     which window runs which Claude Code session: its state, context size,
                 compactions, folder; `pys-agents skills` installs the agent skills
  pys-sessions   keep a record of every window (folder, title, the session in it, its last
                 screen) and open them again after pys died

They read Claude Code's own files (`~/.claude/sessions/<pid>.json`, the transcripts). Those
are not a documented interface: a Claude Code update may change them, so every read here is
defensive and a field that is missing shows as `-`. Linux only for now (they read /proc).
"""
