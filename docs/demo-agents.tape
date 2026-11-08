# The agents demo: two agents in pys windows, `pys-agents`, a `pys kill`, and
# `pys-sessions restore` bringing both back. Re-record with:  docs/record-demo-agents.sh
# It runs in a container (docs/demo-agents/Dockerfile) as user `demo` on host `demo`, and the
# agents are stand-ins (docs/demo-agents/claude) that write what Claude Code writes.
Output demo-agents.v3.gif
Set Shell bash
Set FontSize 16
Set Width 1240
Set Height 660
Set Padding 16
Set TypingSpeed 40ms
Set Theme "Builtin Dark"

Hide
Type "cd ~ && clear"
Enter
Sleep 300ms
Show

# Two agents, each in its own window; each names its window.
Type "pys"
Enter
Sleep 1.2s
Type "cd ~/src/app && claude --name reviewer"
Enter
Sleep 1.5s
Ctrl+A
Type "c"
Sleep 600ms
Type "cd ~/src/api && claude --name builder"
Enter
Sleep 1.5s

# Which window runs which session, and how full its context is.
Ctrl+A
Type "c"
Sleep 600ms
Type "pys-agents"
Enter
Sleep 3.5s

# pys ends, every window with it; ON_CHANGE took a snapshot first.
Type "pys kill"
Enter
Sleep 1.5s
Type "pys-sessions restore"
Enter
Sleep 3s
Type "pys-sessions restore --yes && pys"
Enter
Sleep 6s

# Both are back, each with its session and folder.
Ctrl+A
Type "c"
Sleep 600ms
Type "pys-agents"
Enter
Sleep 4s
Screenshot demo-agents.v3.png
Sleep 300ms

Hide
Type "pys kill"
Enter
Sleep 500ms
