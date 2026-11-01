"""The native part: the _vterm extension, libvterm (deps/libvterm) compiled in.

Everything else about the package is in pyproject.toml. The extension is optional:
where it can't be built (no C compiler) the install still succeeds and py-screen
runs the pure-Python "pyte" engine. Release wheels must contain it; the release
smoke test runs with PYS_TERM=vterm, which fails without it.
"""

from glob import glob

from setuptools import Extension, setup

LIBVTERM = "deps/libvterm"

setup(
    ext_modules=[
        Extension(
            "py_screen._vterm",
            sources=["src/py_screen/_vterm.c", *sorted(glob(f"{LIBVTERM}/src/*.c"))],
            include_dirs=[f"{LIBVTERM}/include", f"{LIBVTERM}/src"],
            optional=True,
        )
    ],
)
