"""Colors, styles and the ChromaBrowser look & feel."""

from __future__ import annotations

from dataclasses import dataclass, replace
RGB = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Style:
    """A terminal cell style. ``None`` colors mean "use the theme default"."""

    fg: RGB | None = None
    bg: RGB | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False
    strike: bool = False

    def merge(self, **kwargs) -> "Style":
        return replace(self, **kwargs)

    def without_color(self) -> "Style":
        return replace(self, fg=None, bg=None)


# ---------------------------------------------------------------- palette ---
# Deep "neon on ink" palette. Everything is RGB; the canvas converts to the
# xterm-256 palette automatically when the terminal lacks truecolor support.

BG: RGB = (14, 16, 22)
BG_ALT: RGB = (21, 24, 32)
FG: RGB = (220, 224, 234)
DIM: RGB = (132, 140, 160)
FAINT: RGB = (84, 92, 110)

ACCENT: RGB = (255, 110, 199)       # magenta
ACCENT2: RGB = (120, 235, 255)      # cyan
ACCENT3: RGB = (255, 206, 110)      # amber
VIOLET: RGB = (168, 140, 255)
GREEN: RGB = (120, 230, 160)
RED: RGB = (255, 115, 125)

LINK: RGB = (108, 198, 255)
LINK_ACTIVE: RGB = (255, 255, 255)
HEADING_COLORS: tuple[RGB, ...] = (
    (255, 122, 202),
    (135, 219, 255),
    (255, 208, 120),
    (172, 255, 196),
    (196, 160, 255),
    (160, 230, 230),
)

CODE_FG: RGB = (255, 214, 153)
CODE_BG: RGB = (35, 39, 51)
QUOTE_FG: RGB = (154, 164, 190)
BORDER: RGB = (58, 65, 84)
RULE: RGB = (52, 58, 76)
HEADER_BG: RGB = (19, 22, 30)
STATUS_BG: RGB = (17, 20, 27)
STATUS_FG: RGB = (168, 176, 196)
SELECT_BG: RGB = (44, 52, 74)
SEARCH_BG: RGB = (110, 88, 20)
SEARCH_FG: RGB = (255, 245, 190)
HINT_BG: RGB = (255, 198, 60)
HINT_FG: RGB = (16, 14, 10)
ERROR_BG: RGB = (60, 22, 30)

IMAGE_MATTE: RGB = BG

GRADIENT_STOPS: tuple[RGB, ...] = (
    (255, 105, 180),   # pink
    (186, 130, 255),   # violet
    (104, 170, 255),   # blue
    (92, 232, 240),    # cyan
    (126, 240, 165),   # green
    (255, 220, 120),   # yellow
)

# ------------------------------------------------------------------ styles ---

BASE = Style(fg=FG)
DIM_STYLE = Style(fg=DIM)
FAINT_STYLE = Style(fg=FAINT)
BOLD = Style(fg=FG, bold=True)
ITALIC = Style(fg=FG, italic=True)
CODE = Style(fg=CODE_FG, bg=CODE_BG)
LINK_STYLE = Style(fg=LINK, underline=True)
HEADER = Style(fg=FG, bg=HEADER_BG)
STATUS = Style(fg=STATUS_FG, bg=STATUS_BG)
ERROR = Style(fg=RED, bold=True)


def lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def gradient_rgb(t: float, stops: tuple[RGB, ...] = GRADIENT_STOPS) -> RGB:
    """Sample a multi-stop gradient at ``t`` in [0, 1]."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    if len(stops) == 1:
        return stops[0]
    pos = t * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    frac = pos - i
    a, b = stops[i], stops[i + 1]
    return (lerp(a[0], b[0], frac), lerp(a[1], b[1], frac), lerp(a[2], b[2], frac))


def gradient_cells(
    text: str, width: int, *, bg: RGB | None = None, bold: bool = True
) -> list[tuple[str, Style]]:
    """Render ``text`` as a horizontally sampled gradient."""
    cells: list[tuple[str, Style]] = []
    n = max(1, len(text) - 1)
    for i, ch in enumerate(text):
        color = gradient_rgb(i / n)
        cells.append((ch, Style(fg=color, bg=bg, bold=bold)))
    return cells


def tint(color: RGB, amount: float, base: RGB = BG) -> RGB:
    """Mix ``color`` towards ``base``. amount=0 keeps color, 1 gives base."""
    return (
        lerp(color[0], base[0], amount),
        lerp(color[1], base[1], amount),
        lerp(color[2], base[2], amount),
    )
