#!/usr/bin/env bash
# Record the agents demo (docs/demo-agents.tape) in a container, so no name, host, path or
# session of the recording machine gets in: user `demo` on host `demo`, pys built from this
# checkout's sdist, stand-in agents. Needs docker. Output: docs/demo-agents.v<N>.gif / .png;
# a version that exists is never overwritten (bump `Output` in the tape for a new one).
set -euo pipefail
cd "$(dirname "$0")/.."
out=$(sed -n 's#^Output ##p' docs/demo-agents.tape)
[ ! -e "docs/$out" ] || { echo "record-demo-agents: docs/$out exists; bump the version in the tape" >&2; exit 1; }
work=${PYS_DEMO_WORK:-$HOME/scratch/pys-demo-agents}
mkdir -p "$work/ctx" "$work/out"
chmod a+rwx "$work/out"     # the image's user `demo` has a uid of its own
uv build --sdist -o "$work/sdist" >/dev/null
cp "$(ls -t "$work"/sdist/*.tar.gz | head -1)" "$work/ctx/py_screen.tar.gz"
cp docs/demo-agents/Dockerfile docs/demo-agents/claude "$work/ctx/"
docker build -q -t pys-demo-agents "$work/ctx"
docker run --rm --init --hostname demo -w /out -v "$PWD/docs/demo-agents.tape:/tape/demo.tape:ro" \
    -v "$work/out:/out" pys-demo-agents /tape/demo.tape
cp -n "$work/out/${out}" "$work/out/${out%.gif}.png" docs/
echo "recorded docs/$out"
