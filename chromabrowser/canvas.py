"""A tiny cell canvas with a diffing ANSI renderer."""

from __future__ import annotations

from typing import Sequence

from . import theme
from .theme import RGB, Style
from .util import char_width

Cell = tuple[str, Style]

_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)
_ANSI_CACHE: dict[RGB, int] = {}
_TRUECOLOR: dict[RGB, str] = {}


def _cube_index(value: int) -> int:
    """Index into the 6x6x6 cube (levels 0, 95, 135, 175, 215, 255)."""
    if value < 48:
        return 0
    if value < 114:
        return 1
    return min(5, (value - 35) // 40)


def rgb_to_xterm256(rgb: RGB) -> int:
    """Map an RGB color to the closest xterm-256 palette index."""
    cached = _ANSI_CACHE.get(rgb)
    if cached is not None:
        return cached
    r, g, b = rgb
    # Candidate 1: 6x6x6 color cube (indices 16..231)
    qr, qg, qb = _cube_index(r), _cube_index(g), _cube_index(b)
    cr, cg, cb = _CUBE_LEVELS[qr], _CUBE_LEVELS[qg], _CUBE_LEVELS[qb]
    best = 16 + 36 * qr + 6 * qg + qb
    best_dist = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2

    # Candidate 2: grayscale ramp (indices 232..255)
    gray = (r * 299 + g * 587 + b * 114) // 1000
    gi = min(23, max(0, (gray - 8 + 5) // 10))
    gv = 8 + 10 * gi
    dist = (r - gv) ** 2 + (g - gv) ** 2 + (b - gv) ** 2
    if dist < best_dist:
        best = 232 + gi
    _ANSI_CACHE[rgb] = best
    return best


def fg_params(rgb: RGB, truecolor: bool) -> str:
    if truecolor:
        seq = _TRUECOLOR.get(rgb)
        if seq is None:
            seq = f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}"
            _TRUECOLOR[rgb] = seq
        return seq
    return f"38;5;{rgb_to_xterm256(rgb)}"


def bg_params(rgb: RGB, truecolor: bool) -> str:
    if truecolor:
        seq = _TRUECOLOR.get(rgb)
        if seq is None:
            seq = f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}"
            _TRUECOLOR[rgb] = seq
        return seq
    return f"48;5;{rgb_to_xterm256(rgb)}"


def sgr(style: Style, truecolor: bool = True) -> str:
    params = ["0"]
    if style.bold:
        params.append("1")
    if style.italic:
        params.append("3")
    if style.underline:
        params.append("4")
    if style.reverse:
        params.append("7")
    if style.strike:
        params.append("9")
    if style.fg is not None:
        params.append(fg_params(style.fg, truecolor))
    if style.bg is not None:
        params.append(bg_params(style.bg, truecolor))
    return "\x1b[" + ";".join(params) + "m"


class Canvas:
    """A grid of styled characters with a diff-based ANSI renderer."""

    def __init__(self, width: int, height: int, *, fg: RGB | None = None, bg: RGB | None = None):
        self.width = max(1, width)
        self.height = max(1, height)
        self.fg = fg if fg is not None else theme.FG
        self.bg = bg if bg is not None else theme.BG
        self.grid: list[list[Cell]] = [
            [(" ", Style(fg=self.fg, bg=self.bg)) for _ in range(self.width)]
            for _ in range(self.height)
        ]

    # ------------------------------------------------------------- drawing ---
    def resolve(self, style: Style) -> Style:
        if style.fg is None or style.bg is None:
            return style.merge(
                fg=style.fg if style.fg is not None else self.fg,
                bg=style.bg if style.bg is not None else self.bg,
            )
        return style

    def put(self, x: int, y: int, text: str, style: Style | None = None) -> int:
        """Draw ``text`` at (x, y). Returns the x position after the text."""
        if y < 0 or y >= self.height or not text:
            return x
        st = self.resolve(style or Style())
        row = self.grid[y]
        for ch in text:
            width = char_width(ch)
            if width == 0:
                # Attach combining marks to the previous cell when possible.
                if x > 0:
                    px = x - 1
                    if px >= 0:
                        prev_ch, prev_style = row[px]
                        row[px] = (prev_ch + ch, prev_style)
                continue
            if x + width > self.width:
                break
            if x >= 0:
                row[x] = (ch, st)
                if width == 2:
                    row[x + 1] = ("", st)
            x += width
        return x

    def put_cells(self, x: int, y: int, cells: Sequence[Cell], max_width: int | None = None) -> None:
        if y < 0 or y >= self.height:
            return
        row = self.grid[y]
        limit = self.width if max_width is None else min(self.width, x + max_width)
        for ch, style in cells:
            w = char_width(ch) if ch else 1
            if x + w > limit:
                break
            if x < 0:
                x += w
                continue
            row[x] = (ch, self.resolve(style))
            if w == 2 and x + 1 < self.width:
                row[x + 1] = ("", self.resolve(style))
            x += w

    def fill_rect(self, x: int, y: int, w: int, h: int, style: Style) -> None:
        st = self.resolve(style)
        for yy in range(max(0, y), min(self.height, y + h)):
            row = self.grid[yy]
            for xx in range(max(0, x), min(self.width, x + w)):
                row[xx] = (" ", st)

    def hline(self, x: int, y: int, w: int, ch: str, style: Style) -> None:
        if 0 <= y < self.height:
            self.put(x, y, ch * max(0, min(w, self.width - x)), style)

    def box(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        style: Style,
        *,
        title: str | None = None,
        title_style: Style | None = None,
        fill: Style | None = None,
    ) -> None:
        if w < 2 or h < 2:
            return
        if fill is not None:
            self.fill_rect(x, y, w, h, fill)
        st = self.resolve(style)
        self.put(x, y, "╭" + "─" * (w - 2) + "╮", st)
        for yy in range(y + 1, y + h - 1):
            self.put(x, yy, "│", st)
            self.put(x + w - 1, yy, "│", st)
        self.put(x, y + h - 1, "╰" + "─" * (w - 2) + "╯", st)
        if title:
            label = f" {title} "
            self.put(x + 2, y, label[: max(0, w - 4)], title_style or st)

    def clear(self, style: Style | None = None) -> None:
        st = self.resolve(style or Style())
        for row in self.grid:
            for i in range(self.width):
                row[i] = (" ", st)

    def paint_span(
        self,
        x: int,
        y: int,
        w: int,
        *,
        fg: RGB | None = None,
        bg: RGB | None = None,
        bold: bool = False,
        underline: bool | None = None,
    ) -> None:
        """Recolor existing glyphs without destroying them."""
        if not (0 <= y < self.height):
            return
        row = self.grid[y]
        for xx in range(max(0, x), min(self.width, x + w)):
            ch, st = row[xx]
            row[xx] = (
                ch,
                Style(
                    fg=fg if fg is not None else st.fg,
                    bg=bg if bg is not None else st.bg,
                    bold=bold or st.bold,
                    italic=st.italic,
                    underline=st.underline if underline is None else underline,
                    reverse=False,
                    strike=st.strike,
                ),
            )

    # ------------------------------------------------------------ rendering ---
    def render(self, previous: list[list[Cell]] | None, truecolor: bool = True) -> str:
        """Return the ANSI escape stream that turns ``previous`` into ``self``."""
        out: list[str] = []
        width = self.width
        for y in range(self.height):
            row = self.grid[y]
            prow = previous[y] if previous is not None and y < len(previous) else None
            x = 0
            while x < width:
                cell = row[x]
                if prow is not None and x < len(prow) and prow[x] == cell:
                    x += 1
                    continue
                style = cell[1]
                start = x
                chunk: list[str] = []
                while x < width:
                    c = row[x]
                    pc = prow[x] if prow is not None and x < len(prow) else None
                    if pc == c or c[1] != style:
                        break
                    if c[0]:
                        chunk.append(c[0])
                    x += 1
                # Placeholder cells ('' = right half of a wide glyph) are skipped,
                # but the cursor must still advance if they start the run.
                out.append(f"\x1b[{y + 1};{start + 1}H")
                out.append(sgr(style, truecolor))
                out.append("".join(chunk))
        out.append("\x1b[0m")
        return "".join(out)

    # ---------------------------------------------------------------- misc ---
    def snapshot(self) -> list[list[Cell]]:
        return [row[:] for row in self.grid]

    def text_at(self, x: int, y: int, width: int) -> str:
        if not (0 <= y < self.height):
            return ""
        return "".join(ch for ch, _ in self.grid[y][x : x + width])

    def lines(self) -> list[str]:
        return ["".join(ch for ch, _ in row) for row in self.grid]
