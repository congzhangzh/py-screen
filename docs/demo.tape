# The README's demo. Re-record with:  docs/record-demo.sh  (it runs vhs in a clean environment)
# A session of its own (PYS_DIR), as a made-up user in /tmp/pys-demo, so nothing of yours shows.
Output docs/demo.v2.gif
Set Shell bash
Set FontSize 16
Set Width 1240
Set Height 660
Set Padding 16
Set TypingSpeed 45ms
Set Theme "Builtin Dark"

Hide
Type `mkdir -p /tmp/pys-demo/home && printf '%s\n' "PS1='demo:~$ '" HISTFILE=/dev/null > /tmp/pys-demo/home/.bashrc`
Enter
Type "export HOME=/tmp/pys-demo/home PYS_DIR=/tmp/pys-demo/run SHELL=/bin/bash PS1='demo:~$ ' && cd ~ && clear"
Enter
Sleep 300ms
Show

# A window names itself, then floods: every line goes through pys, none dropped.
Type "pys"
Sleep 400ms
Enter
Sleep 1.2s
Type "pys title build"
Enter
Sleep 500ms
Type "time seq 1 2000000"
Sleep 300ms
Enter
Sleep 4s

# Split side by side; the new window runs a clock.
Ctrl+A
Type "|"
Sleep 1s
Type "pys title clock"
Enter
Sleep 400ms
Type "while sleep 1; do date +%T; done"
Enter
Sleep 3s

# The window list.
Ctrl+A
Type "w"
Sleep 2.5s
Escape
Sleep 800ms

# Restart the brain from the left pane: the clock never stops.
Ctrl+A
Type "h"
Sleep 300ms
Type "pys reload"
Sleep 300ms
Enter
Sleep 3.5s
Screenshot docs/demo.v2.png
Sleep 300ms

Hide
Type "pys kill"
Enter
Sleep 500ms
