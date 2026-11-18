# The agent teams demo: agents of two products and one that serves them all, each window in
# its product group (`pys group`); the status line puts each group on a line of its own once
# one row no longer holds them, and `pys-agents team` answers "who is in my team".
# Re-record with:  docs/record-demo-agents.sh docs/demo-teams.tape
# It runs in a container (docs/demo-agents/Dockerfile) as user `demo` on host `demo`, and the
# agents are stand-ins (docs/demo-agents/claude) that write what Claude Code writes.
Output demo-teams.v1.gif
Set Shell bash
Set FontSize 16
Set Width 1240
Set Height 660
Set Padding 16
Set TypingSpeed 35ms
Set Theme "Builtin Dark"

Hide
Type "cd ~ && clear"
Enter
Sleep 300ms
Show

# Product a's owner and release manager: each window joins its group, then runs its agent;
# while one row holds every tab, each group is led by its name, the rest by others.
Type "pys"
Enter
Sleep 1.2s
Type "pys group product-a && cd ~/src/app && claude --name po-product-a-g1"
Enter
Sleep 1.5s
Ctrl+A
Type "c"
Sleep 500ms
Type "pys group product-a && cd ~/src/app && claude --name rel-product-a-g1"
Enter
Sleep 1.5s

# Product b's owner: the row is full, so each product gets a line of its own, pys's own row
# last (its buttons, the motto, the widgets).
Ctrl+A
Type "c"
Sleep 500ms
Type "pys group product-b && cd ~/src/api && claude --name po-product-b-g1"
Enter
Sleep 2.5s

# Product b's component developer joins its line.
Ctrl+A
Type "c"
Sleep 500ms
Type "pys group product-b && cd ~/src/api && claude --name comp-product-b-g1"
Enter
Sleep 2.5s

# A teacher for every product: the group shared, on the line of the windows in no group.
Ctrl+A
Type "c"
Sleep 500ms
Type "pys group shared && claude --name edu-all-g1"
Enter
Sleep 2.5s

# A plain shell in product b: who is my team, and in which window?
Ctrl+A
Type "c"
Sleep 500ms
Type "pys group product-b && pys-agents team"
Enter
Sleep 4s
Type "pys group --list"
Enter
Sleep 4s
Screenshot demo-teams.v1.png
Sleep 300ms

Hide
Type "pys kill"
Enter
Sleep 500ms
