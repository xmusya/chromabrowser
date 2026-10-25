"""Turn images into colored half-block cells (works in any truecolor terminal)."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from PIL import Image, ImageOps, UnidentifiedImageError

from . import theme
from .theme import Style
from .net import BrowserSession, FetchError

Cell = tuple[str, Style]

# A terminal cell is roughly 8x16 pixels; two half-block pixels per cell are
# ~square, so we resize images to (cells_x, cells_y * 2).
CELL_PX_W = 8.0
MAX_CELLS_H = 56


@dataclass
class ImageBlock:
    rows: list[list[Cell]] = field(default_factory=list)
    width: int = 0
    height: int = 0
    error: str | None = None
    source_width: int = 0
    source_height: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.rows)


class ImageLoader:
    def __init__(
        self,
        session: BrowserSession,
        *,
        max_width: int = 96,
        unicode: bool = True,
        matte: tuple[int, int, int] = theme.IMAGE_MATTE,
    ):
        self.session = session
        self.max_width = max(8, max_width)
        self.unicode = unicode
        self.matte = matte
        self._mem: dict[tuple[str, int, int], ImageBlock] = {}
        self._mem_order: list[tuple[str, int, int]] = []

    # ------------------------------------------------------------------ api ---
    def render(
        self,
        url: str,
        avail: int,
        *,
        zoom: float = 1.0,
        referer: str | None = None,
    ) -> ImageBlock:
        avail = max(1, min(avail, self.max_width if self.max_width else avail))
        key = (url, avail, int(round(zoom * 100)))
        hit = self._mem.get(key)
        if hit is not None:
            return hit

        block = self._render_uncached(url, avail, zoom, referer)
        self._mem[key] = block
        self._mem_order.append(key)
        if len(self._mem_order) > 160:
            old = self._mem_order.pop(0)
            self._mem.pop(old, None)
        return block

    # -------------------------------------------------------------- internal ---
    def _render_uncached(self, url: str, avail: int, zoom: float, referer: str | None) -> ImageBlock:
        try:
            data = self.session.fetch_image(url, referer=referer)
        except FetchError as exc:
            return ImageBlock(error=exc.message)
        except Exception as exc:  # pragma: no cover - defensive
            return ImageBlock(error=str(exc) or exc.__class__.__name__)

        try:
            img = Image.open(io.BytesIO(data))
            img.seek(0)
        except (UnidentifiedImageError, OSError, ValueError):
            return ImageBlock(error="unsupported image format")

        if (img.format or "").upper() == "SVG":
            return ImageBlock(error="SVG images are not supported")

        src_w, src_h = img.size
        if src_w < 2 or src_h < 2:
            return ImageBlock(error="image is too small", source_width=src_w, source_height=src_h)

        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        natural = min(src_w / CELL_PX_W, float(self.max_width))
        target_w = int(round(natural * max(0.2, zoom)))
        target_w = max(2, min(target_w, avail, self.max_width))
        target_h = max(1, int(round(target_w * src_h / src_w / 2)))
        if target_h > MAX_CELLS_H:
            target_h = MAX_CELLS_H
            target_w = max(2, int(round(target_h * 2 * src_w / src_h)))
            target_w = min(target_w, avail, self.max_width)

        try:
            if hasattr(img, "is_animated") and img.is_animated:
                img.seek(0)
            img = img.convert("RGBA")
            img = img.resize((target_w, target_h * 2), Image.Resampling.LANCZOS)
            matte = Image.new("RGBA", img.size, (*self.matte, 255))
            img = Image.alpha_composite(matte, img).convert("RGB")
        except Exception as exc:  # pragma: no cover - defensive
            return ImageBlock(error=f"could not decode: {exc}")

        pixels = img.load()
        top_char = "▀" if self.unicode else " "
        block_char = "█" if self.unicode else " "
        rows: list[list[Cell]] = []
        for cy in range(target_h):
            row: list[Cell] = []
            for cx in range(target_w):
                top = pixels[cx, cy * 2]
                bottom = pixels[cx, cy * 2 + 1]
                top_rgb = (top[0], top[1], top[2])
                bot_rgb = (bottom[0], bottom[1], bottom[2])
                if top_rgb == bot_rgb:
                    row.append((block_char, Style(fg=top_rgb, bg=top_rgb)))
                else:
                    row.append((top_char, Style(fg=top_rgb, bg=bot_rgb)))
            rows.append(row)

        return ImageBlock(
            rows=rows,
            width=target_w,
            height=target_h,
            source_width=src_w,
            source_height=src_h,
        )


def terminal_supports_unicode() -> bool:
    encoding = (os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") or os.environ.get("LANG") or "")
    term = os.environ.get("TERM", "")
    if "UTF-8" not in encoding.upper() and "UTF8" not in encoding.upper():
        return False
    if term == "linux":
        return False
    return True
