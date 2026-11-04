#!/usr/bin/env python3
"""Build a ready-to-use py-screen for this OS: one archive with its own Python, pys and its
dependencies, nothing to install. Unpack it anywhere and run bin/pys (pys.cmd on Windows).

Usage: release/bundle.py --wheels DIR --version V [--out DIR]

  1. A standalone CPython 3.14 (python-build-standalone, the build uv itself installs:
     relocatable, so the folder works wherever it is unpacked).
  2. py-screen-mux[stats]==V from the wheel in --wheels (the release's own wheel for this
     OS and architecture), with pyte, psutil (and pywinpty on Windows) from PyPI,
     byte-compiled so the first start doesn't write into the folder.
  3. A launcher that runs `python -m py_screen` from wherever the folder is. pip's own
     console scripts hold an absolute path, so they would break once the folder moves.
  4. Parts pys never uses are left out (Tk, IDLE, C headers, ensurepip); the interpreter
     and the standard library are kept whole.

Writes pys-<V>-<os>-<arch>.tar.gz (.zip on Windows) into --out and prints its path.
Needs uv on PATH.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

WIN = sys.platform == "win32"

LAUNCHER_SH = """#!/bin/sh
# py-screen launcher: runs this folder's own Python, wherever the folder is (a symlink
# to this file from a bin dir on PATH works too).
self=$0
while [ -L "$self" ]; do
    link=$(readlink "$self")
    case $link in /*) self=$link ;; *) self=$(dirname "$self")/$link ;; esac
done
here=$(cd "$(dirname "$self")/.." && pwd)
exec "$here/python/bin/python3" -m py_screen "$@"
"""

LAUNCHER_CMD = """@"%~dp0python\\python.exe" -m py_screen %*\r\n"""

README = """py-screen {version} for {plat}, ready to use
==================================================

Nothing to install: this folder holds its own Python {py}, pys, and its dependencies
(pyte, psutil{extra}). Unpack it anywhere, then:

    {run}                         start or attach (C-a ? lists the keys)
    {run} sample-config           a sample config with cpu, mem, net, disk and a clock

To run it as `pys`, put the folder's {bindir} on PATH{link}.

The folder works wherever it is, so moving it is fine. To add a Python package for your
own config's widgets, install it into this Python:

    {pip} install <package>

Licences: py-screen and libvterm (MIT) in python/{site}/py_screen_mux-{version}.dist-info/;
Python's own in python/{pylicense}. Source: https://github.com/congzhangzh/py-screen
"""

# Left out: not used by pys, and only the parts whose loss can't break an import pys or
# its dependencies make. Paths are relative to the Python folder, per layout.
DROP_POSIX = ["include", "share/man", "lib/python3.14/idlelib", "lib/python3.14/tkinter",
              "lib/python3.14/turtledemo", "lib/python3.14/ensurepip", "lib/tcl9.0",
              "lib/tk9.0", "lib/itcl4.3", "lib/thread3.0", "lib/libtcl9tk9.0.so",
              "lib/libtcl9.0.so", "lib/libtcl9tk9.0.dylib", "lib/libtcl9.0.dylib",
              "bin/idle3", "bin/idle3.14", "bin/pip", "bin/pip3", "bin/pip3.14",
              "bin/pydoc3", "bin/pydoc3.14"]
DROP_WIN = ["include", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/ensurepip",
            "tcl", "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll",
            "DLLs/tcl90.dll", "DLLs/tcl9tk90.dll", "Scripts"]


def plat() -> str:
    os_name = {"linux": "linux", "darwin": "macos", "win32": "windows"}[sys.platform]
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64", "aarch64": "aarch64"}.get(arch, arch)
    if os_name == "macos" and arch == "aarch64":
        arch = "arm64"
    if os_name == "linux" and arch == "arm64":
        arch = "aarch64"
    return f"{os_name}-{arch}"


def run(*cmd: str | Path) -> str:
    print("+", " ".join(map(str, cmd)), flush=True)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"bundle: exit {r.returncode}\n{r.stdout}{r.stderr}")
    return r.stdout


def standalone_python(tmp: Path) -> Path:
    """uv installs CPython 3.14 into tmp; return the versioned folder (not its symlink)."""
    run("uv", "python", "install", "3.14", "--install-dir", tmp)
    dirs = [d for d in tmp.iterdir() if d.is_dir() and not d.is_symlink()
            and d.name.startswith("cpython-3.14.")]
    if len(dirs) != 1:
        sys.exit(f"bundle: expected one CPython 3.14 in {tmp}, found {[d.name for d in dirs]}")
    return dirs[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheels", required=True, type=Path)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=Path("bundles"), type=Path)
    a = ap.parse_args()

    name = f"pys-{a.version}-{plat()}"
    archive = a.out / (name + (".zip" if WIN else ".tar.gz"))
    if archive.exists():
        sys.exit(f"bundle: {archive} exists; a version is built once")
    a.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pys-bundle.") as t:
        tmp = Path(t)
        src = standalone_python(tmp / "uv")
        root = tmp / name
        py_dir = root / "python"
        shutil.copytree(src, py_dir, symlinks=True)
        # uv writes the folder it installed into (here: this build's temp dir, a home path
        # on a desk build) into sysconfig's build-time data, where python-build-standalone
        # has its placeholder /install. Put the placeholder back: the path is wrong once
        # the folder moves, and it must not ship. Text files only (no NUL byte), since a
        # string of another length would break a binary.
        old = str(src).encode()
        for f in py_dir.rglob("*"):
            if f.is_file() and not f.is_symlink():
                b = f.read_bytes()
                if old in b and b"\0" not in b:
                    f.write_bytes(b.replace(old, b"/install"))
        py = py_dir / ("python.exe" if WIN else "bin/python3")
        ver = run(py, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])").strip()

        # uv marks its Pythons externally managed; this one belongs to pys alone, so a
        # user may add packages to it for their own widgets
        for marker in py_dir.rglob("EXTERNALLY-MANAGED"):
            marker.unlink()
        for rel in DROP_WIN if WIN else DROP_POSIX:
            p = py_dir / rel
            if p.is_symlink() or p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)

        run("uv", "pip", "install", "--python", py, "--find-links", a.wheels,
            "--compile-bytecode", f"py-screen-mux[stats]=={a.version}")
        # pip's console scripts (python/bin/pys, python/Scripts/pys.exe) hold this temp
        # folder's absolute path: drop them, the launcher below is the way in
        for script in [py_dir / "bin" / "pys", py_dir / "Scripts"]:
            if script.is_dir():
                shutil.rmtree(script)
            elif script.exists():
                script.unlink()
        # the wheel must be the release's own, never a same-numbered one from an index
        shown = run(py, "-c", "import py_screen, importlib.metadata as m;"
                    "print(m.version('py-screen-mux'))").strip()
        if shown != a.version:
            sys.exit(f"bundle: installed py-screen-mux {shown}, wanted {a.version}")

        if WIN:
            (root / "pys.cmd").write_bytes(LAUNCHER_CMD.encode())
            run_cmd, bindir, link = r"pys.cmd", "folder itself", ""
            pip = r"python\python.exe -m pip"
            site, pylicense = "Lib/site-packages", "LICENSE.txt"
        else:
            (root / "bin").mkdir()
            launcher = root / "bin" / "pys"
            launcher.write_text(LAUNCHER_SH)
            launcher.chmod(0o755)
            run_cmd, bindir = "bin/pys", "bin/"
            link = ", or link it: ln -s \"$PWD/bin/pys\" ~/.local/bin/pys"
            pip = "python/bin/python3 -m pip"
            site, pylicense = "lib/python3.14/site-packages", "lib/python3.14/LICENSE.txt"
        (root / "README.txt").write_text(README.format(
            version=a.version, plat=plat(), py=ver, extra=", pywinpty" if WIN else "",
            run=run_cmd, bindir=bindir, link=link, pip=pip, site=site, pylicense=pylicense))

        if WIN:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                for f in sorted(root.rglob("*")):
                    z.write(f, f.relative_to(tmp))
        else:
            with tarfile.open(archive, "w:gz", compresslevel=9) as tf:
                tf.add(root, arcname=name)          # keeps symlinks and the exec bits
    size = archive.stat().st_size / 1e6
    print(f"bundle: {archive} ({size:.1f} MB, Python {ver})")


if __name__ == "__main__":
    main()
