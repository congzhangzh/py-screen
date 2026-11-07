#!/usr/bin/env bash
# Record the README's demo (docs/demo.tape) with vhs: https://github.com/charmbracelet/vhs
# It needs vhs, ttyd and ffmpeg, and runs from the repo root with `pys` on PATH (by default the
# installed one). The environment is cleared, so no name, host or path of yours gets in: the
# tape runs a session of its own (PYS_DIR) as a made-up user in /tmp/pys-demo.
set -euo pipefail
cd "$(dirname "$0")/.."
pys=$(command -v pys) || { echo "record-demo: pys is not on PATH" >&2; exit 1; }
mkdir -p /tmp/pys-demo/bin
ln -sf "$pys" /tmp/pys-demo/bin/pys
exec env -i HOME="$HOME" TERM=xterm-256color LANG=C.UTF-8 \
    PATH=/tmp/pys-demo/bin:/usr/local/bin:/usr/bin:/bin vhs docs/demo.tape
