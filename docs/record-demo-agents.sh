#!/usr/bin/env bash
# Record a demo that has agents in it (by default docs/demo-agents.tape; also demo-teams.tape
# and demo-settings.tape) in a container, so no name, host, path or session of the recording
# machine gets in: user `demo` on host `demo`, pys built from this checkout's sdist, stand-in
# agents. Needs docker. Output: the tape's `Output` (docs/<name>.v<N>.gif) and its .png;
# a version that exists is never overwritten (bump `Output` in the tape for a new one).
#   docs/record-demo-agents.sh [docs/<tape>]
set -euo pipefail
cd "$(dirname "$0")/.."
tape=${1:-docs/demo-agents.tape}
out=$(sed -n 's#^Output ##p' "$tape")
[ ! -e "docs/$out" ] || { echo "record-demo-agents: docs/$out exists; bump the version in the tape" >&2; exit 1; }
work=${PYS_DEMO_WORK:-$HOME/scratch/pys-demo-agents}
mkdir -p "$work/ctx" "$work/out"
chmod a+rwx "$work/out"     # the image's user `demo` has a uid of its own
# the sdist as release.yml stamps it (a dry run's version, <day>.0, and the commit), so the
# version line in the help and the settings says what was recorded: this checkout's files,
# committed or not, at the commit it is on
rm -rf "$work/src" && mkdir -p "$work/src"
git ls-files -co --exclude-standard -z | tar -c --null -T - | tar -x -C "$work/src"
(cd "$work/src" && uv version --frozen "$(date -u +%Y%m%d).0" >/dev/null &&
    printf 'COMMIT = "%s"\n' "$(git -C "$OLDPWD" rev-parse HEAD)" > src/py_screen/_build.py)
uv build --sdist -o "$work/sdist" "$work/src" >/dev/null
cp "$(ls -t "$work"/sdist/*.tar.gz | head -1)" "$work/ctx/py_screen.tar.gz"
cp docs/demo-agents/Dockerfile docs/demo-agents/claude "$work/ctx/"
docker build -q -t pys-demo-agents "$work/ctx"
docker run --rm --init --hostname demo -w /out -v "$PWD/$tape:/tape/demo.tape:ro" \
    -v "$work/out:/out" pys-demo-agents /tape/demo.tape
cp -n "$work/out/${out}" "$work/out/${out%.gif}.png" docs/
echo "recorded docs/$out"
