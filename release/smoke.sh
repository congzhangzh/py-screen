#!/usr/bin/env bash
# Smoke-test an installed `pys` (the release's wheel, not the checkout): start a server in a
# private socket dir, open a window, type into it, read its screen back, end the session.
# No outer terminal is needed: everything goes over the RPC socket.
#
# Usage: release/smoke.sh [pys command]     (default: pys from PATH)
set -euo pipefail
pys=${1:-pys}
command -v "$pys" > /dev/null || { echo "smoke: no $pys on PATH" >&2; exit 1; }

# A private server: its own socket dir (short, for AF_UNIX's sun_path), no user config,
# a plain shell. It never touches a session already running on this machine.
PYS_DIR=$(mktemp -d /tmp/pys-smoke.XXXXXX)
export PYS_DIR SHELL=/bin/sh PS1='$ ' TERM=xterm-256color PYS_CONFIG=/nonexistent
trap '"$pys" kill > /dev/null 2>&1 || true; rm -rf "$PYS_DIR"' EXIT

echo "== $(command -v "$pys"), socket dir $PYS_DIR"
wid=$("$pys" new -- /bin/sh | python3 -c 'import json, sys; print(json.load(sys.stdin)["wid"])')
echo "window $wid"
# The shell computes the answer, so the text on the screen is its output, not the echo
# of what was typed.
# shellcheck disable=SC2016 # the shell in the window expands it, not this one
"$pys" send "$wid" 'echo smoke-$((6 * 7))' --enter
for _ in $(seq 50); do
	if "$pys" capture "$wid" | grep -q '^smoke-42$'; then
		"$pys" ls
		echo "smoke: ok"
		exit 0
	fi
	sleep 0.2
done
echo "smoke: the window never printed smoke-42; its screen:" >&2
"$pys" capture "$wid" >&2 || true
exit 1
