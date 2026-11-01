#!/bin/sh
# Build the vterm engine with AddressSanitizer + UndefinedBehaviorSanitizer into a
# copy of the package, for tools/vterm_fuzz.py. The installed package is not touched.
#
#   tools/vterm_asan.sh <new dir>
#   ASAN_OPTIONS=detect_leaks=0:exitcode=3 UBSAN_OPTIONS=halt_on_error=1:exitcode=4 \
#     LD_PRELOAD="$(${CC:-gcc} -print-file-name=libasan.so)" \
#     PYTHONPATH=<new dir> python tools/vterm_fuzz.py --seeds 5000
#
# PYTHON picks the interpreter whose headers and extension suffix are used (default
# python3); run the fuzz with that same interpreter. CC defaults to gcc.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <new dir>" >&2; exit 2; }
out=$1
[ -e "$out/py_screen" ] && { echo "$out/py_screen exists; give a new dir" >&2; exit 2; }
root=$(cd "$(dirname "$0")/.." && pwd)
py=${PYTHON:-python3}
inc=$("$py" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')
ext=$("$py" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')
mkdir -p "$out/py_screen"
cp "$root"/src/py_screen/*.py "$out/py_screen/"
${CC:-gcc} -shared -fPIC -g -O1 -fno-omit-frame-pointer \
    -fsanitize=address,undefined -fno-sanitize-recover=undefined \
    -I"$inc" -I"$root/deps/libvterm/include" -I"$root/deps/libvterm/src" \
    "$root/src/py_screen/_vterm.c" "$root"/deps/libvterm/src/*.c \
    -o "$out/py_screen/_vterm$ext"
echo "built $out/py_screen/_vterm$ext"
