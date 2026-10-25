"""Small state containers shared by the app loop and the renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Prompt:
    label: str
    text: str = ""
    cursor: int = 0
    kind: str = "url"
    on_submit: Callable[[str], None] | None = None
    on_cancel: Callable[[], None] | None = None
    on_change: Callable[[str], None] | None = None


@dataclass
class HintEntry:
    label: str
    link: int
    url: str
    line: int
    x: int


@dataclass
class HintState:
    entries: list[HintEntry] = field(default_factory=list)
    action: str = "open"  # open | download
    prefix: str = ""


@dataclass
class Overlay:
    kind: str  # bookmarks | history
    title: str
    items: list[dict] = field(default_factory=list)
    index: int = 0
    offset: int = 0


@dataclass
class SearchState:
    query: str
    matches: list[tuple[int, int, int]] = field(default_factory=list)
    index: int = 0
    saved_scroll: int = 0
