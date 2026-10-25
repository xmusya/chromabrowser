"""Small text helpers shared by the renderer and the UI."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Emoji / pictographs that virtually all terminals render double-width.
_WIDE_RANGES = (
    (0x1F300, 0x1FAFF),
    (0x1F000, 0x1F2FF),
    (0x1F900, 0x1F9FF),
    (0x20000, 0x3FFFD),
)
_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\ufeff", "\u2060"}


def char_width(ch: str) -> int:
    if not ch:
        return 0
    if ch in _ZERO_WIDTH:
        return 0
    code = ord(ch)
    if code < 32:
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    for lo, hi in _WIDE_RANGES:
        if lo <= code <= hi:
            return 2
    return 1


def text_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def sanitize(text: str, *, collapse: bool = True) -> str:
    """Normalize a piece of page text for terminal output."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")
    text = _CONTROL_RE.sub("", text)
    if collapse:
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
    return text


def expand_tabs(text: str, tabsize: int = 4) -> str:
    return text.expandtabs(tabsize)


def clip_text(text: str, width: int, ellipsis: str = "…") -> str:
    """Truncate to ``width`` terminal cells."""
    if width <= 0:
        return ""
    out: list[str] = []
    used = 0
    ell_w = text_width(ellipsis)
    for ch in text:
        w = char_width(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
    if used < text_width(text) and width >= ell_w:
        while out and used + ell_w > width:
            used -= char_width(out.pop())
        out.append(ellipsis)
    return "".join(out)


def clip_middle(text: str, width: int, ellipsis: str = "…") -> str:
    """Truncate from the middle, keeping the start and end (good for URLs)."""
    if text_width(text) <= width:
        return text
    if width <= 0:
        return ""
    ew = text_width(ellipsis)
    if width <= ew + 2:
        return clip_text(text, width, ellipsis)
    left = (width - ew) // 2
    right = width - ew - left
    head = clip_text(text, left, "")
    tail = ""
    used = 0
    for ch in reversed(text):
        w = char_width(ch)
        if used + w > right:
            break
        tail = ch + tail
        used += w
    return head + ellipsis + tail


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} B"


def split_words(text: str) -> Iterable[str]:
    for token in re.findall(r"\s+|\S+", text):
        yield token


def split_by_width(text: str, width: int) -> list[str]:
    """Split a string into chunks that each fit into ``width`` cells."""
    if width <= 0:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > width and current:
            chunks.append("".join(current))
            current = []
            used = 0
        if w == 0 and not current:
            continue
        current.append(ch)
        used += w
    if current:
        chunks.append("".join(current))
    return chunks or [""]


def wrap_plain_text(text: str, width: int) -> list[str]:
    """Word-wrap plain text (used for --dump and text/plain pages)."""
    out: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        line = ""
        for word in re.findall(r"\S+|\s+", paragraph):
            if word.isspace():
                if line and not line.endswith(" "):
                    line += " "
                continue
            if text_width(line) + text_width(word) <= width:
                line += word
            else:
                if line.strip():
                    out.append(line.rstrip())
                for chunk in split_by_width(word, width):
                    if text_width(chunk) > width:
                        chunk = chunk[:width]
                    line = chunk
                    if text_width(chunk) == width:
                        out.append(line)
                        line = ""
        if line.strip():
            out.append(line.rstrip())
    return out
