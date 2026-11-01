"""pyte.Screen plus what the brain needs and pyte lacks: the alternate screen.

Full-screen apps (vim, less, Claude) switch to the alternate screen with private
mode 1049 (or 47 / 1047) and switch back on exit, expecting the shell's screen to
come back as it was. pyte ignores those modes, so vim's last frame used to stay.
"""

from __future__ import annotations

import copy

import pyte

ALT_MODES = {47, 1047, 1049}


class Screen(pyte.Screen):
    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines)
        self._saved: tuple[dict, pyte.screens.Cursor] | None = None

    @property
    def in_alt(self) -> bool:
        return self._saved is not None

    def set_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and ALT_MODES & set(modes) and self._saved is None:
            lines = {y: dict(line) for y, line in self.buffer.items()}
            self._saved = (lines, copy.copy(self.cursor))
            self.buffer.clear()
            self.dirty.update(range(self.lines))
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and ALT_MODES & set(modes) and self._saved is not None:
            lines, cursor = self._saved
            self._saved = None
            self.buffer.clear()
            for y, line in lines.items():
                if y < self.lines:
                    self.buffer[y].update({x: c for x, c in line.items() if x < self.columns})
            self.cursor = cursor
            self.cursor.x = min(self.cursor.x, self.columns - 1)
            self.cursor.y = min(self.cursor.y, self.lines - 1)
            self.dirty.update(range(self.lines))
        super().reset_mode(*modes, **kwargs)

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        super().resize(lines, columns)
        self.dirty.update(range(self.lines))
