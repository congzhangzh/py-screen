"""The "vterm" screen engine: libvterm, through the _vterm C extension.

_vterm.Term does the work (see _vterm.c); this names the engine for term.py. An
ImportError here means the extension wasn't built, and term.py offers "pyte" only.
"""

from __future__ import annotations

from ._vterm import LIBVTERM_VERSION, Term


class VTermTerm(Term):
    engine = "vterm"


__all__ = ["LIBVTERM_VERSION", "VTermTerm"]
