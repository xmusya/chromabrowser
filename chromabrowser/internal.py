"""Internal pages: start screen, help, history, bookmarks, errors, text."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from . import theme
from .htmlrender import (
    Line,
    LinkInfo,
    RenderedPage,
    Run,
    collect_segments,
    wrap_runs,
)
from .theme import Style
from .util import format_bytes, sanitize, split_by_width, text_width

LINK = Style(fg=theme.LINK, underline=True)
DIM = Style(fg=theme.DIM)
FAINT = Style(fg=theme.FAINT)
ACCENT = Style(fg=theme.ACCENT, bold=True)
ACCENT2 = Style(fg=theme.ACCENT2, bold=True)
TITLE = Style(fg=theme.FG, bold=True)
CODE = theme.CODE


class PageBuilder:
    def __init__(self, width: int, base_url: str = "", title: str = ""):
        self.width = max(20, width)
        self.base_url = base_url
        self.title = title
        self.lines: list[Line] = []
        self.links: list[LinkInfo] = []
        self._ids: dict[str, int] = {}

    # -------------------------------------------------------------- basics ---
    def link(self, url: str, text: str, style: Style | None = None) -> Run:
        if url not in self._ids:
            link_id = len(self.links)
            self._ids[url] = link_id
            self.links.append(LinkInfo(link_id, url))
        return Run(text, style or LINK, self._ids[url])

    def add(self, runs: Sequence[Run] | str, style: Style | None = None) -> None:
        if isinstance(runs, str):
            runs = [Run(runs, style or theme.BASE)]
        wrapped = wrap_runs(list(runs), self.width)
        for line in wrapped:
            self.lines.append(Line(line))

    def raw(self, *runs: Run) -> None:
        self.lines.append(Line(list(runs)))

    def blank(self) -> None:
        if self.lines and not self.lines[-1].is_blank:
            self.lines.append(Line([]))

    def rule(self, char: str = "─", style: Style | None = None) -> None:
        self.lines.append(Line([Run(char * self.width, style or Style(fg=theme.RULE))]))

    def build(self) -> RenderedPage:
        while self.lines and self.lines[-1].is_blank:
            self.lines.pop()
        texts: dict[int, list[str]] = {link.id: [] for link in self.links}
        for line in self.lines:
            for run in line.runs:
                if run.link is not None and run.text.strip():
                    texts.setdefault(run.link, []).append(run.text)
        for link in self.links:
            link.text = sanitize(" ".join(texts.get(link.id, []))).strip()[:160]
        return RenderedPage(
            lines=self.lines,
            links=self.links,
            segments=collect_segments(self.lines),
            title=self.title,
            base_url=self.base_url,
            width=self.width,
        )


# ------------------------------------------------------------------- logo ---

_LOGO_ROWS = [
    " ██████ ██    ██ ██████   ██████  ███    ███  ██████ ",
    "██      ██    ██ ██   ██ ██    ██ ████  ████ ██    ██",
    "██      ████████ ██████  ██    ██ ██ ████ ██ ████████",
    "██      ██    ██ ██   ██ ██    ██ ██  ██  ██ ██    ██",
    " ██████ ██    ██ ██   ██  ██████  ██      ██ ██    ██",
]


def _gradient_runs(text: str, bg: tuple[int, int, int] | None = None, bold: bool = True) -> list[Run]:
    runs: list[Run] = []
    n = max(1, len(text) - 1)
    for index, ch in enumerate(text):
        color = theme.gradient_rgb(index / n)
        if runs and runs[-1].style.fg == color:
            runs[-1] = Run(runs[-1].text + ch, runs[-1].style, runs[-1].link)
        else:
            runs.append(Run(ch, Style(fg=color, bg=bg, bold=bold)))
    return runs


def start_page(width: int, *, version: str, config, bookmarks=None) -> RenderedPage:
    b = PageBuilder(width, "about:start", "ChromaBrowser")
    logo_width = max(text_width(row) for row in _LOGO_ROWS)

    if width >= logo_width + 2:
        for row in _LOGO_ROWS:
            b.raw(*_gradient_runs(row.rstrip()))
    else:
        word = "CHROMA"
        b.raw(*_gradient_runs(word))
        b.raw(Run("BROWSER", Style(fg=theme.FG, bold=True)))

    b.blank()
    tagline = "a colorful browser for your terminal"
    b.raw(Run(tagline, Style(fg=theme.DIM, italic=True)))
    b.raw(Run(f"version {version}", Style(fg=theme.FAINT)))
    b.blank()

    b.add([Run("Type a URL and press Enter — or search the web.", theme.BASE)])
    b.add([Run("No JavaScript, no tracking, just text and pictures.", DIM)])
    b.blank()

    b.add([Run("Quick start", ACCENT)])
    quick = [
        ("o", "open a URL or search"),
        ("f", "follow a link (hint labels)"),
        ("/", "find text on the page"),
        ("b", "bookmarks"),
        ("?", "help & all keyboard shortcuts"),
        ("q", "quit"),
    ]
    for key, desc in quick:
        b.raw(
            Run("  "),
            Run(f"{key:<3}", ACCENT2),
            Run(desc, theme.BASE),
        )
    b.blank()

    b.add([Run("Try ", DIM), b.link("about:help", "about:help"), Run(" · ", DIM),
           b.link("about:bookmarks", "about:bookmarks"), Run(" · ", DIM),
           b.link("about:history", "about:history")])
    if bookmarks is not None and len(bookmarks):
        b.blank()
        b.add([Run("Bookmarks", ACCENT)])
        for item in list(bookmarks)[:6]:
            title = item.get("title") or item["url"]
            b.raw(
                Run("  ★ ", Style(fg=theme.ACCENT3)),
                b.link(item["url"], title[:width - 8]),
            )
    b.blank()
    b.add([Run("Images are drawn with colored half-blocks; ", FAINT),
           Run("+", Style(fg=theme.ACCENT2, bold=True)),
           Run(" / ", FAINT),
           Run("-", Style(fg=theme.ACCENT2, bold=True)),
           Run(" zooms, ", FAINT),
           Run("i", Style(fg=theme.ACCENT2, bold=True)),
           Run(" toggles them.", FAINT)])
    return b.build()


# ------------------------------------------------------------------- help ---

_HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Navigation",
        [
            ("o", "open a URL, search the web or a local file"),
            ("Ctrl+L", "edit the current URL"),
            ("Enter", "follow the focused link"),
            ("f", "show hint labels for links"),
            ("Tab / Shift+Tab", "cycle through links"),
            ("Backspace, Alt+←", "go back"),
            ("Alt+→", "go forward"),
            ("r, Ctrl+R", "reload the page"),
            ("Esc", "cancel loading / close prompts"),
        ],
    ),
    (
        "Scrolling",
        [
            ("j, ↓", "scroll one line down"),
            ("k, ↑", "scroll one line up"),
            ("Space, PgDn, Ctrl+F", "scroll a page down"),
            ("PgUp, Ctrl+B", "scroll a page up"),
            ("Ctrl+D / Ctrl+U", "half a page down / up"),
            ("g, Home", "jump to the top"),
            ("G, End", "jump to the bottom"),
            ("mouse wheel", "scroll"),
        ],
    ),
    (
        "Finding & copying",
        [
            ("/", "search the page"),
            ("n / N", "next / previous match"),
            ("Esc", "clear the search"),
            ("y", "copy the current URL (OSC 52)"),
            ("Ctrl+S", "save the page to the download folder"),
        ],
    ),
    (
        "Bookmarks & history",
        [
            ("B", "bookmark / unbookmark the current page"),
            ("b", "bookmark list"),
            ("Ctrl+H", "history list"),
            ("d", "download a link (hint labels)"),
        ],
    ),
    (
        "Media",
        [
            ("+ / -", "grow / shrink images"),
            ("0", "reset image zoom"),
            ("i", "images on / off"),
        ],
    ),
    (
        "Mouse",
        [
            ("wheel", "scroll"),
            ("left click", "open a link"),
        ],
    ),
]

_ABOUT = [
    "ChromaBrowser renders HTML (headings, lists, tables, quotes, code, forms and",
    "images) directly in your terminal. There is no JavaScript engine and CSS is",
    "honoured only where it affects text: colors, bold, italic and underline.",
    "",
    "Images are converted to truecolor half-blocks (▀) and scroll together with",
    "the text. Terminals without truecolor fall back to the xterm-256 palette.",
    "",
    "Config:  ~/.config/chromabrowser/config.json",
    "Data:    ~/.local/share/chromabrowser/  (history, bookmarks, cookies)",
    "Cache:   ~/.cache/chromabrowser/images/",
]


def help_page(width: int, *, version: str) -> RenderedPage:
    b = PageBuilder(width, "about:help", "ChromaBrowser — Help")
    b.raw(*_gradient_runs("CHROMA"), Run(" Browser", Style(fg=theme.FG, bold=True)),
          Run("  ·  help", DIM))
    b.blank()
    b.add([Run("Everything you can press.", Style(fg=theme.FG, bold=True))])
    b.blank()

    for section, keys in _HELP_SECTIONS:
        b.add([Run(section, ACCENT2)])
        for key, description in keys:
            b.raw(Run("  "), Run(f"{key:<22}", ACCENT), Run(description, theme.BASE))
        b.blank()

    b.add([Run("About this browser", ACCENT2)])
    for line in _ABOUT:
        if line:
            b.add([Run(line, DIM)])
        else:
            b.blank()
    b.blank()
    b.add([Run("Version ", FAINT), Run(version, DIM), Run(" · ", FAINT),
           b.link("about:start", "back to the start page")])
    return b.build()


# -------------------------------------------------------------- list pages ---

def _format_time(ts: float) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


def bookmarks_page(width: int, bookmarks) -> RenderedPage:
    b = PageBuilder(width, "about:bookmarks", "Bookmarks")
    b.raw(*_gradient_runs("BOOKMARKS"), Run(f"  {len(bookmarks)}", DIM))
    b.blank()
    if not len(bookmarks):
        b.add([Run("No bookmarks yet.", DIM)])
        b.blank()
        b.add([Run("Open a page and press ", DIM), Run("B", ACCENT),
               Run(" to save it here.", DIM)])
    else:
        for index, item in enumerate(bookmarks, 1):
            title = item.get("title") or item["url"]
            b.raw(Run(f"{index:>3}. ", FAINT), b.link(item["url"], title))
            b.raw(Run("     "), Run(item["url"], FAINT))
            stamp = _format_time(item.get("ts", 0))
            if stamp:
                b.raw(Run("     "), Run(stamp, FAINT))
            b.blank()
    b.blank()
    b.add([Run("Press ", DIM), Run("b", ACCENT),
           Run(" to open the bookmark panel, or "),
           b.link("about:start", "return to the start page")])
    return b.build()


def history_page(width: int, history) -> RenderedPage:
    b = PageBuilder(width, "about:history", "History")
    entries = history.recent(120)
    b.raw(*_gradient_runs("HISTORY"), Run(f"  {len(entries)} recent", DIM))
    b.blank()
    if not entries:
        b.add([Run("History is empty.", DIM)])
    else:
        last_day = ""
        for item in entries:
            stamp = _format_time(item.ts)
            day = stamp[:10]
            if day and day != last_day:
                if last_day:
                    b.blank()
                b.add([Run(day, ACCENT2)])
                last_day = day
            title = item.title or item.url
            b.raw(
                Run(stamp[11:] + "  " if stamp else "", FAINT),
                b.link(item.url, title[: max(10, width - 16)]),
            )
    b.blank()
    b.add([Run("History is stored locally. ", FAINT), b.link("about:start", "start page")])
    return b.build()


# ------------------------------------------------------------------ errors ---

def error_page(width: int, url: str, message: str, status: int | None = None) -> RenderedPage:
    b = PageBuilder(width, url, "Error")
    b.raw(Run("  ⚠  ", Style(fg=theme.RED, bold=True)),
          Run("Could not open the page", Style(fg=theme.RED, bold=True)))
    b.blank()
    b.add([Run("URL", FAINT)])
    b.add([Run(url, Style(fg=theme.FG, underline=False))])
    b.blank()
    b.add([Run("What happened", FAINT)])
    b.add([Run(message, Style(fg=theme.RED))])
    if status is not None:
        b.add([Run(f"HTTP status: {status}", DIM)])
    b.blank()
    b.add([Run("Try: ", DIM), Run("r", ACCENT2), Run(" to retry, ", DIM),
           Run("Backspace", ACCENT2), Run(" to go back, ", DIM),
           Run("o", ACCENT2), Run(" to open another address.", DIM)])
    b.blank()
    b.add([b.link("about:start", "start page")])
    return b.build()


# -------------------------------------------------------------------- text ---

def text_page(
    text: str,
    width: int,
    *,
    title: str = "",
    url: str = "",
    code: bool = False,
    note: str = "",
) -> RenderedPage:
    b = PageBuilder(width, url, title)
    if title:
        b.add([Run(title, Style(fg=theme.ACCENT2, bold=True))])
        if url:
            b.add([Run(url, FAINT)])
        b.blank()
    if note:
        b.add([Run(note, DIM)])
        b.blank()
    style = CODE if code else theme.BASE
    text = sanitize(text, collapse=False).expandtabs(4)
    for physical in text.split("\n"):
        if not physical.strip():
            b.raw(Run(""))
            continue
        for chunk in split_by_width(physical, width):
            b.raw(Run(chunk, style))
    return b.build()


# ------------------------------------------------------------------- image ---

def image_page(width: int, url: str, loader, fetched, *, note: str = "") -> RenderedPage:
    b = PageBuilder(width, url, url.rsplit("/", 1)[-1] or "Image")
    block = loader.render(url, width, referer=None)
    if not block.ok:
        b.raw(Run("  ⚠  ", Style(fg=theme.RED, bold=True)),
              Run(f"Could not display image: {block.error}", Style(fg=theme.RED)))
        b.blank()
        b.add([Run(url, FAINT)])
        return b.build()

    b.blank()
    for row in block.rows:
        b.lines.append(Line([], image=row))
    b.blank()
    filename = url.rsplit("/", 1)[-1].split("?")[0] or "image"
    size = format_bytes(len(fetched.content)) if fetched is not None else ""
    dims = f"{block.source_width}×{block.source_height} px"
    meta = " · ".join(part for part in (filename, dims, size, fetched.content_type if fetched else "") if part)
    b.add([Run(meta, DIM)])
    b.add([Run(url, FAINT)])
    if note:
        b.blank()
        b.add([Run(note, DIM)])
    return b.build()
