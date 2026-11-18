# The settings demo: prefix o (or a click on " pys ") opens the panel; each change applies at
# once and is kept in config.d/50-settings.py. Here: the product lines off and on, the status
# rows stepped (− n +), and the caption line under a window taken away.
# Re-record with:  docs/record-demo-agents.sh docs/demo-settings.tape
# It runs in a container (docs/demo-agents/Dockerfile) as user `demo` on host `demo`, and the
# agents are stand-ins (docs/demo-agents/claude) that write what Claude Code writes.
Output demo-settings.v2.gif
Set Shell bash
Set FontSize 16
Set Width 1240
Set Height 660
Set Padding 16
Set TypingSpeed 35ms
Set Theme "Builtin Dark"

# Set up, unseen: five agents of two products and a shell.
Hide
Type "cd ~ && clear && pys"
Enter
Sleep 1.2s
Type "pys group product-a && cd ~/src/app && claude --name po-product-a-g1"
Enter
Sleep 1s
Ctrl+A
Type "c"
Sleep 400ms
Type "pys group product-a && cd ~/src/app && claude --name rel-product-a-g1"
Enter
Sleep 1s
Ctrl+A
Type "c"
Sleep 400ms
Type "pys group product-b && cd ~/src/api && claude --name po-product-b-g1"
Enter
Sleep 1s
Ctrl+A
Type "c"
Sleep 400ms
Type "pys group product-b && cd ~/src/api && claude --name comp-product-b-g1"
Enter
Sleep 1s
Ctrl+A
Type "c"
Sleep 400ms
Type "pys group shared && claude --name edu-all-g1"
Enter
Sleep 1.5s
Show
Sleep 1.5s

# prefix o: the panel; the status line below it stays live.
Ctrl+A
Type "o"
Sleep 2.5s

# The product lines off: the plain flow of tabs, on at most STATUS_LINES rows.
Down 5
Sleep 600ms
Enter
Sleep 2.5s

# STATUS_LINES, stepped: − to one row (the tabs that don't fit go short), + to three.
Up 4
Sleep 600ms
Type "-"
Sleep 2.5s
Type "+"
Sleep 800ms
Type "+"
Sleep 2s

# The product lines on again; the caption line under a window off.
Down 4
Sleep 600ms
Enter
Sleep 2s
Down
Sleep 600ms
Enter
Sleep 2s

# q closes: the agent's window has the caption's row too.
Type "q"
Sleep 3s
Screenshot demo-settings.v2.png
Sleep 300ms

Hide
Type "pys kill"
Enter
Sleep 500ms
