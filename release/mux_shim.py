#!/usr/bin/env python3
"""Build py-screen-mux V: the old name of py-screen, kept so that an install by that name
(`uv tool install py-screen-mux`, a requirement somewhere) keeps working and upgrading. It
holds no code: it requires py-screen==V and gives the same commands from it (a uv tool only
exposes the commands of the package it was installed by, so they are declared here too).

Usage: release/mux_shim.py --version V --out DIR      (needs uv on PATH)

release.yml publishes it next to py-screen at every release. Writes
py_screen_mux-V-py3-none-any.whl into DIR and prints its path.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

COMMANDS = {"pys": "py_screen:main",
            "pys-agents": "py_screen.agents.listing:main",
            "pys-sessions": "py_screen.agents.sessions:main"}

README = """# py-screen-mux is now py-screen

This is the old name of [py-screen](https://pypi.org/project/py-screen/), a terminal
workspace with the `pys` command. It installs py-screen at the same version and gives the
same commands, so nothing breaks. To move to the new name:

    uv tool uninstall py-screen-mux && uv tool install py-screen
    pys setup --yes      # points the agent skills and the session hook at the new install
"""


def pyproject(version: str) -> str:
    scripts = "\n".join(f'{k} = "{v}"' for k, v in COMMANDS.items())
    return f'''[project]
name = "py-screen-mux"
version = "{version}"
description = "The old name of py-screen (the pys terminal workspace): installs py-screen"
readme = "README.md"
license = "MIT"
requires-python = ">=3.14"
dependencies = ["py-screen=={version}"]

[project.urls]
Homepage = "https://github.com/congzhangzh/py-screen"
"New name" = "https://pypi.org/project/py-screen/"

[project.scripts]
{scripts}

[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = []
'''


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mux-shim-") as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text(pyproject(a.version), encoding="utf-8")
        (root / "README.md").write_text(README, encoding="utf-8")
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(a.out.resolve()), str(root)],
                       check=True)
    whl = a.out / f"py_screen_mux-{a.version}-py3-none-any.whl"
    if not whl.exists():
        sys.exit(f"mux_shim: no {whl.name} in {a.out}")
    print(whl)


if __name__ == "__main__":
    main()
