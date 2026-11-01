"""Splits: a tree of panes. Each pane shows one window (or nothing).

Geometry: a vertical split puts children side by side with a one-column separator
between them; a horizontal split stacks them. Every pane ends with a caption line
(window number and title), which also separates stacked panes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_W, MIN_H = 4, 2      # a pane needs at least 1 text row plus its caption


@dataclass(eq=False)
class Pane:
    wid: int | None = None
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    @property
    def text_h(self) -> int:
        return max(1, self.h - 1)


@dataclass(eq=False)
class Split:
    vertical: bool                       # True: side by side
    children: list = field(default_factory=list)
    weights: list[float] = field(default_factory=list)


Node = Pane | Split


def distribute(total: int, weights: list[float], minimum: int) -> list[int]:
    s = sum(weights) or 1
    sizes = [max(minimum, int(total * w / s)) for w in weights]
    sizes[-1] = max(minimum, total - sum(sizes[:-1]))
    return sizes


class Layout:
    def __init__(self) -> None:
        self.root: Node = Pane()
        self.focus: Pane = self.root  # type: ignore[assignment]
        self.separators: list[tuple[int, int, int]] = []   # (x, y, height)

    def panes(self, node: Node | None = None) -> list[Pane]:
        node = self.root if node is None else node
        if isinstance(node, Pane):
            return [node]
        return [p for c in node.children for p in self.panes(c)]

    def place(self, w: int, h: int) -> None:
        self.separators = []
        self._place(self.root, 0, 0, w, h)

    def _place(self, n: Node, x: int, y: int, w: int, h: int) -> None:
        if isinstance(n, Pane):
            n.x, n.y, n.w, n.h = x, y, w, h
            return
        k = len(n.children)
        if n.vertical:
            sizes = distribute(w - (k - 1), n.weights, MIN_W)
            cx = x
            for i, (c, s) in enumerate(zip(n.children, sizes)):
                self._place(c, cx, y, s, h)
                cx += s
                if i < k - 1:
                    self.separators.append((cx, y, h))
                    cx += 1
        else:
            sizes = distribute(h, n.weights, MIN_H)
            cy = y
            for c, s in zip(n.children, sizes):
                self._place(c, x, cy, w, s)
                cy += s

    def parent(self, target: Node, node: Node | None = None) -> Split | None:
        node = self.root if node is None else node
        if isinstance(node, Pane):
            return None
        for c in node.children:
            if c is target:
                return node
            p = self.parent(target, c)
            if p:
                return p
        return None

    def split(self, vertical: bool, wid: int | None) -> Pane:
        """Split the focused pane; the new pane (showing wid) gets the focus."""
        old, new = self.focus, Pane(wid)
        par = self.parent(old)
        if par and par.vertical == vertical:
            i = par.children.index(old)
            half = par.weights[i] / 2
            par.weights[i] = half
            par.children.insert(i + 1, new)
            par.weights.insert(i + 1, half)
        else:
            sp = Split(vertical, [old, new], [1.0, 1.0])
            if par is None:
                self.root = sp
            else:
                i = par.children.index(old)
                par.children[i] = sp
        self.focus = new
        return new

    def remove(self, pane: Pane) -> bool:
        par = self.parent(pane)
        if par is None:
            return False                       # the last pane stays
        i = par.children.index(pane)
        del par.children[i]
        del par.weights[i]
        if len(par.children) == 1:
            only = par.children[0]
            gp = self.parent(par)
            if gp is None:
                self.root = only
            else:
                gp.children[gp.children.index(par)] = only
        if self.focus is pane:
            self.focus = self.panes()[min(i, len(self.panes()) - 1)]
        return True

    def move_focus(self, d: str) -> None:
        f = self.focus
        best, best_key = None, None
        for p in self.panes():
            if p is f:
                continue
            if d in "hl":
                overlap = min(f.y + f.h, p.y + p.h) - max(f.y, p.y)
                gap = (f.x - (p.x + p.w)) if d == "h" else (p.x - (f.x + f.w))
            else:
                overlap = min(f.x + f.w, p.x + p.w) - max(f.x, p.x)
                gap = (f.y - (p.y + p.h)) if d == "k" else (p.y - (f.y + f.h))
            if overlap <= 0 or gap < 0:
                continue
            key = (gap, -overlap)
            if best_key is None or key < best_key:
                best, best_key = p, key
        if best:
            self.focus = best

    def resize(self, horizontal: bool, delta: int) -> None:
        """Grow the focused pane by delta columns (horizontal) or rows."""
        node: Node = self.focus
        par = self.parent(node)
        while par is not None and par.vertical != horizontal:
            node, par = par, self.parent(par)
        if par is None:
            return
        sizes = [self._extent(c, horizontal) for c in par.children]
        i = par.children.index(node)
        j = i + 1 if i + 1 < len(sizes) else i - 1
        mn = MIN_W if horizontal else MIN_H
        delta = max(min(delta, sizes[j] - mn), mn - sizes[i])
        sizes[i] += delta
        sizes[j] -= delta
        par.weights = [float(s) for s in sizes]

    def _extent(self, n: Node, horizontal: bool) -> int:
        ps = self.panes(n)
        if horizontal:
            return max(p.x + p.w for p in ps) - min(p.x for p in ps)
        return max(p.y + p.h for p in ps) - min(p.y for p in ps)

    def equalize(self, node: Node | None = None) -> None:
        node = self.root if node is None else node
        if isinstance(node, Split):
            node.weights = [1.0] * len(node.children)
            for c in node.children:
                self.equalize(c)

    def pane_of(self, wid: int) -> Pane | None:
        return next((p for p in self.panes() if p.wid == wid), None)
