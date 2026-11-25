"""The instructions every Claude Code session in a pys window gets, from a SessionStart hook.

A skill is read when its description matches the task; these few lines are in the context of
every session that starts (or resumes, or is cleared or compacted) inside pys, and of none
outside it: the hook prints nothing when $PYS_WINDOW is not set. `pys setup` registers it in
Claude Code's settings.json, with the Python of the install that ran setup.

    python -m py_screen.agents.instructions          the hook (reads the event on stdin)
    python -m py_screen.agents.instructions --show   print the text, as a session gets it

The text names the commands as this install runs them: `pys` when that is on PATH, else this
install's own Python (`… -m py_screen`), so it is right for uvx and a bundle off PATH too.

The same rules are also a file you can read, Claude Code's rules/pys.md, which `pys setup`
writes and every session loads (rule_text). While that file is this install's own, the hook
only adds the window's number and points at it, so a session doesn't get the rules twice.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

MARK = "py_screen.agents.instructions"     # how setup knows the hook is ours
RULE = "pys.md"                            # in Claude Code's rules folder
RULE_MARK = "<!-- written by `pys setup`"   # how setup knows the rule file is ours


def _py() -> str:
    p = Path(sys.executable)
    return p.as_posix() if os.name == "nt" else str(p)   # C:/…: fine in Git Bash and cmd


def command_for(name: str, module: str) -> str:
    """How an agent's shell runs one of pys's commands from here."""
    if shutil.which(name):
        return name
    return f'"{_py()}" -m {module}'


def hook_command() -> str:
    """What settings.json runs: this install's Python, whatever is on PATH."""
    return f'"{_py()}" -m {MARK}'


def rules() -> list[str]:
    """The rules, one line each: the hook's text and the rule file share them."""
    pys = command_for("pys", "py_screen")
    agents = command_for("pys-agents", "py_screen.agents.listing")
    return [
        f'Name your window at the start and when your work changes, short (about 24 characters): {pys} title --agent "<your name> <what you work on>" (a team member is named <context>-<role>-g<N>, e.g. "pys-po-g1 groups busy"). --agent makes this session its owner (pys finds it), so the title ends with it.',
        f"Put your window in its product's group when you start: {pys} group <product> (`{pys} group --list` shows the groups; a member that serves every product: {pys} group shared). A window opened from yours gets its group.",
        f"{agents} lists the agents and their windows; `{agents} where` each window's folder and git branch. Look before you work in a repo another window works in.",
        f'To tell other agents something: `{agents} to <name>` (or `--group <product>`, or `--all`: every agent but you) gives each one\'s address; send it with SendMessage, which waits while one is busy. One that is not running: `{agents} hold <name> "<text>"`, and it gets it when it names its window. Never type a message into a window.',
        f'`{pys} capture <wid>` reads a window. Type into one only with the user\'s word and only when it is idle: the text first, then Enter on its own (`{pys} send --enter <wid> ""`).',
        f"Never `{pys} kill`, prefix Q, or a pkill that could match pys: they end every window, the user's and other agents' too.",
        "The pys-drive skill has the details; pys-restore brings windows back after pys died.",
    ]


def text(wid: str) -> str:
    return (f"You run inside py-screen (pys), window {wid}, a terminal workspace the user shares "
            "with other windows and often other agents.\n" + "\n".join(f"- {r}" for r in rules()))


def rule_path() -> Path:
    from .claude import home
    return home() / "rules" / RULE


def rule_text() -> str:
    """Claude Code's rules/pys.md, as `pys setup` writes it."""
    return (f"{RULE_MARK} (py-screen); `pys setup --yes` brings it up to date. Delete it and "
            "the SessionStart hook pys setup installed says the same in each pys window. -->\n"
            "\n# py-screen (pys): when you run in one of its windows\n\n"
            "py-screen (pys) is a terminal multiplexer: a terminal workspace the user shares with "
            "other windows and often other agents. You run in one of its windows when "
            "`$PYS_WINDOW` is set (its number); otherwise none of this applies.\n\n"
            + "".join(f"- {r}\n" for r in rules()))


def rule_state() -> str:
    """The rule file: "ok" (this install's, current), "old" (pys's, not current), "mine"
    (there, not pys's) or "none"."""
    try:
        have = rule_path().read_text(encoding="utf-8")
    except OSError:
        return "none"
    if have == rule_text():
        return "ok"
    return "old" if have.startswith(RULE_MARK) else "mine"


def window_group(wid: str) -> str | None:
    """The window's product group, "" for none, None when pys can't say (no server, an
    old one): asked of the session the window belongs to, in a second at most."""
    import asyncio

    from .. import proto
    if s := os.environ.get("PYS_SESSION"):
        os.environ["PYS_DIR"] = s                 # the window's own session, not a default
    # a hook must never fail or make anything: no server socket, no question (runtime_dir()
    # would make the dir, and a path too long for AF_UNIX raises SystemExit there)
    if os.name == "posix" and not (proto.runtime_dir_path() / "brain.sock").exists():
        return None
    try:
        r = asyncio.run(proto.rpc({"cmd": "group", "wid": int(wid)}, timeout=1.0))
    except (Exception, SystemExit):
        return None
    return (r.get("group") or "") if r.get("ok") else None


def hook_text(wid: str) -> str:
    """What the hook adds: the rules, or only the window when the rule file has them; and,
    for a window in no product group, that it is in none."""
    if rule_state() == "ok":
        out = (f"You run inside py-screen (pys), window {wid}; the pys rule ({RULE} in your "
               "rules) says how to work in it.")
    else:
        out = text(wid)
    if window_group(wid) == "":
        pys = command_for("pys", "py_screen")
        out += (f"\nYour window is in no product group: join your product's now ({pys} group "
                f"<product>; {pys} group --list shows them).")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from .. import utf8_pipes
    utf8_pipes()
    wid = os.environ.get("PYS_WINDOW")
    if "--show" in argv:
        print(text(wid or "<N>"))
        return 0
    if not wid:                       # not in pys: nothing to add
        return 0
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        event = {}
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event.get("hook_event_name", "SessionStart"),
        "additionalContext": hook_text(wid)}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
