"""ChromaBrowser application: terminal loop, navigation and interaction."""

from __future__ import annotations

import base64
import os
import re
import shutil
import signal
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import storage, theme
from .imaging import ImageLoader, terminal_supports_unicode
from .keys import KeyReader
from .net import BrowserSession, FetchError
from .pagebuild import Page, PageFactory
from .storage import Bookmarks, Config, History
from .theme import Style
from .ui import UIMixin
from .uistate import HintEntry, HintState, Overlay, Prompt, SearchState
from .util import clip_middle, clip_text, format_bytes, text_width, plural

HINT_ALPHABET = "asdfghjklqwertyuiopzxcvbnm1234567890"
PAGE_STEP = 3


def detect_truecolor(mode: str = "auto") -> bool:
    mode = (mode or "auto").lower()
    if mode in ("truecolor", "24bit", "true"):
        return True
    if mode in ("256", "256color", "xterm"):
        return False
    colorterm = os.environ.get("COLORTERM", "").lower()
    term = os.environ.get("TERM", "").lower()
    program = os.environ.get("TERM_PROGRAM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return True
    if "truecolor" in term or "direct" in term:
        return True
    if program in ("iterm.app", "wezterm", "vscode", "kitty", "ghostty", "hyper", "rio"):
        return True
    return False


def make_hint_labels(count: int) -> list[str]:
    if count <= len(HINT_ALPHABET):
        return list(HINT_ALPHABET[:count])
    labels: list[str] = []
    for first in HINT_ALPHABET:
        for second in HINT_ALPHABET:
            labels.append(first + second)
            if len(labels) >= count:
                return labels
    return labels


@dataclass
class LoadJob:
    gen: int
    url: str
    restore_scroll: int
    event: threading.Event = field(default_factory=threading.Event)
    page: Page | None = None
    error: tuple[str, int | None] | None = None
    started: float = field(default_factory=time.monotonic)


class App(UIMixin):
    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()
        storage.ensure_dirs()
        self._fd = sys.stdin.fileno()
        self.session = BrowserSession(self.config)
        self.history = History()
        self.bookmarks = Bookmarks()
        self.unicode = terminal_supports_unicode()
        self.truecolor = detect_truecolor(self.config.color)
        self.loader = ImageLoader(
            self.session,
            max_width=self.config.max_image_width,
            unicode=self.unicode,
        )
        self.factory = PageFactory(
            self.config,
            self.session,
            self.loader,
            history=self.history,
            bookmarks=self.bookmarks,
        )

        self.images_on = bool(self.config.images)
        self.zoom = float(self.config.zoom or 1.0)
        self.cols, self.rows = self._size()
        self.prev: list[list] | None = None
        self.canvas = None
        self.visible_segments: list[tuple[int, int, int, int]] = []
        self.content_top = 2
        self.content_h = max(1, self.rows - 3)
        self.max_scroll = 0
        self.prompt_cursor = (1, self.rows - 1)

        self.page: Page | None = None
        self.entries: list[dict] = []
        self.pos = -1
        self.cache: dict[str, Page] = {}
        self.cache_order: list[str] = []

        self.job: LoadJob | None = None
        self.load_gen = 0
        self.prompt: Prompt | None = None
        self.hints: HintState | None = None
        self.overlay: Overlay | None = None
        self.search: SearchState | None = None
        self.search_saved_scroll: int | None = None
        self.focus_link: int | None = None
        self.focus_links: list[int] = []

        self.message = ""
        self.message_until = 0.0
        self.message_style = Style(fg=theme.ACCENT3)
        self.bg_messages: list[tuple[str, bool]] = []
        self.bg_count = 0

        self.running = True
        self.resized = False
        self.keys = KeyReader(self._fd)
        self._saved_attrs = None
        self._old_winch = None
        self._old_term = None
        self._wake_r: int | None = None
        self._wake_w: int | None = None

    # ------------------------------------------------------------- terminal ---
    def _size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size(self._fd)
        except OSError:
            try:
                size = shutil.get_terminal_size()
            except OSError:
                size = os.terminal_size((80, 24))
        return max(20, size.columns), max(6, size.lines)

    def _write(self, text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (BrokenPipeError, ValueError):
            self.running = False

    def _enter_terminal(self) -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError(
                "ChromaBrowser needs an interactive terminal. "
                "Use --dump to render a page to stdout."
            )
        self._saved_attrs = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)
        self._write(
            "\x1b[?1049h"  # alternate screen
            "\x1b[?25l"    # hide cursor
            "\x1b[?1000h\x1b[?1006h"  # mouse clicks + SGR
            "\x1b[?2004h"  # bracketed paste
            "\x1b[2J\x1b[H"
        )

    def _leave_terminal(self) -> None:
        try:
            self._write(
                "\x1b[?2004l\x1b[?1006l\x1b[?1000l"
                "\x1b[?25h\x1b[0m\x1b[?1049l"
            )
        finally:
            if self._saved_attrs is not None:
                try:
                    termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved_attrs)
                except termios.error:
                    pass

    def _install_signals(self) -> None:
        self._old_winch = signal.getsignal(signal.SIGWINCH)
        self._old_term = signal.getsignal(signal.SIGTERM)
        # Make signals interrupt the blocking key read so resizes are handled
        # immediately instead of waiting for the next keypress.
        try:
            wake_r, wake_w = os.pipe()
            os.set_blocking(wake_r, False)
            os.set_blocking(wake_w, False)
            self._wake_r, self._wake_w = wake_r, wake_w
            signal.set_wakeup_fd(wake_w)
            self.keys.wake_fds = [wake_r]
        except (OSError, ValueError):
            self._wake_r = self._wake_w = None
        signal.signal(signal.SIGWINCH, self._on_winch)
        signal.signal(signal.SIGTERM, self._on_term)

    def _restore_signals(self) -> None:
        try:
            if self._wake_w is not None:
                signal.set_wakeup_fd(-1)
        except (ValueError, OSError):
            pass
        self.keys.wake_fds = []
        for fd in (self._wake_r, self._wake_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._wake_r = self._wake_w = None
        try:
            if self._old_winch is not None:
                signal.signal(signal.SIGWINCH, self._old_winch)
            if self._old_term is not None:
                signal.signal(signal.SIGTERM, self._old_term)
        except (ValueError, OSError):
            pass

    def _on_winch(self, *_args) -> None:
        self.resized = True

    def _on_term(self, *_args) -> None:
        self.running = False

    def _set_terminal_title(self, title: str) -> None:
        clean = re.sub(r"[\x00-\x1f\x7f]", " ", title or "ChromaBrowser").strip()
        self._write(f"\x1b]0;{clean[:120]} — ChromaBrowser\x07")

    # ------------------------------------------------------------------ run ---
    def run(self, target: str | None = None) -> None:
        self._enter_terminal()
        self._install_signals()
        self._set_terminal_title("ChromaBrowser")
        try:
            self.navigate(target or self.config.homepage, push=True)
            self._loop()
        finally:
            self._restore_signals()
            try:
                self.session.close()
            finally:
                try:
                    self.config.save()
                finally:
                    self._leave_terminal()

    def _loop(self) -> None:
        while self.running:
            if self.resized:
                self.resized = False
                self.on_resize()
            self._drain_bg()
            if self.job is not None and self.job.event.is_set():
                self._apply_job()
            self.draw()
            timeout = 0.1 if (self.job is not None or self.bg_count > 0) else None
            try:
                key = self.keys.read(timeout)
            except EOFError:
                break
            if key is None:
                continue
            try:
                self.handle_key(key)
            except Exception as exc:  # keep the browser alive on unexpected input
                if os.environ.get("CHROMA_DEBUG"):
                    raise
                self.notify(f"Internal error: {exc}", error=True, seconds=6)

    def on_resize(self) -> None:
        self.prev = None
        if self.page is not None and self.page.view is not None:
            width = self.content_width()
            if self.page.width != width:
                self.page = self.factory.reflow(
                    self.page, width, images=self.images_on, zoom=self.zoom
                )
            limit = max(0, len(self.page.view.lines) - max(1, self.rows - 3))
            self.page.scroll = max(0, min(self.page.scroll, limit))

    def content_width(self) -> int:
        return max(20, self.cols - 2)

    def content_height(self) -> int:
        return max(1, self.rows - 3)

    # ----------------------------------------------------------- navigation ---
    def navigate(
        self,
        target: str,
        *,
        push: bool = True,
        restore_scroll: int = 0,
        use_cache: bool = True,
    ) -> None:
        url = self.session.resolve(target)
        self.prompt = None
        self.hints = None
        self.overlay = None
        self.focus_link = None
        self.search = None
        if push:
            self._save_entry()
            self.entries = self.entries[: self.pos + 1]
            self.entries.append({"url": url, "title": "", "scroll": 0})
            self.pos = len(self.entries) - 1
        self.load_gen += 1
        self.job = None

        cached = self.cache.get(url)
        if cached is not None and use_cache and cached.kind != "error":
            if cached.width != self.content_width():
                cached = self.factory.reflow(
                    cached, self.content_width(), images=self.images_on, zoom=self.zoom
                )
                self._cache_put(url, cached)
                self._cache_put(cached.url, cached)
            if cached.view is not None:
                cached.scroll = max(0, min(restore_scroll, max(0, len(cached.view.lines) - 1)))
            self.page = cached
            self._sync_entry()
            self._set_terminal_title(cached.title or cached.url)
            self.prev = None
            return

        self._start_load(url, restore_scroll)

    def _start_load(self, url: str, restore_scroll: int) -> None:
        self.load_gen += 1
        job = LoadJob(self.load_gen, url, restore_scroll)
        self.job = job
        width = self.content_width()
        images = self.images_on
        zoom = self.zoom
        thread = threading.Thread(
            target=self._worker, args=(job, width, images, zoom), daemon=True
        )
        thread.start()

    def _worker(self, job: LoadJob, width: int, images: bool, zoom: float) -> None:
        try:
            job.page = self.factory.fetch(job.url, width, images=images, zoom=zoom)
        except FetchError as exc:
            job.error = (exc.message, exc.status)
        except Exception as exc:  # pragma: no cover - truly unexpected
            job.error = (f"{type(exc).__name__}: {exc}", None)
        finally:
            job.event.set()

    def _apply_job(self) -> None:
        job = self.job
        if job is None:
            return
        if not job.event.is_set():
            return
        self.job = None
        if job.gen != self.load_gen:
            return
        width = self.content_width()
        if job.error is not None:
            page = self.factory.error(job.url, job.error[0], job.error[1], width)
        else:
            page = job.page
        if page is None:
            return
        if page.width != width:
            page = self.factory.reflow(page, width, images=self.images_on, zoom=self.zoom)
        if page.view is not None:
            max_scroll = max(0, len(page.view.lines) - 1)
            page.scroll = max(0, min(job.restore_scroll, max_scroll))
        self.page = page
        self.hints = None
        self.overlay = None
        self.focus_link = None
        self.search = None
        self._sync_entry()
        self._cache_put(job.url, page)
        self._cache_put(page.url, page)
        if page.kind not in ("about", "error"):
            self.history.add(page.url, page.title)
            if page.source is None and page.show_source:
                page.source = self.factory.source_page(page, width)
        self._set_terminal_title(page.title or page.url)
        if job.error is not None:
            self.notify(job.error[0], error=True, seconds=6)
        elif page.status is not None and page.status >= 400:
            reason = f"HTTP {page.status}"
            self.notify(reason, error=True, seconds=5)

    def _sync_entry(self) -> None:
        if self.page is None or not (0 <= self.pos < len(self.entries)):
            return
        entry = self.entries[self.pos]
        entry["title"] = self.page.title
        entry["scroll"] = self.page.scroll
        if self.page.kind != "error":
            entry["url"] = self.page.url

    def _save_entry(self) -> None:
        if self.page is None or not (0 <= self.pos < len(self.entries)):
            return
        entry = self.entries[self.pos]
        entry["scroll"] = self.page.scroll
        entry["title"] = self.page.title

    def _cache_put(self, url: str, page: Page) -> None:
        if not url:
            return
        self.cache[url] = page
        if url in self.cache_order:
            self.cache_order.remove(url)
        self.cache_order.append(url)
        while len(self.cache_order) > 16:
            old = self.cache_order.pop(0)
            self.cache.pop(old, None)

    def back(self) -> None:
        if self.job is not None:
            self.stop_loading()
        if self.pos <= 0:
            self.notify("No previous page")
            return
        self._save_entry()
        self.pos -= 1
        entry = self.entries[self.pos]
        self.navigate(
            entry["url"],
            push=False,
            restore_scroll=int(entry.get("scroll") or 0),
            use_cache=True,
        )

    def forward(self) -> None:
        if self.pos < len(self.entries) - 1:
            self._save_entry()
            self.pos += 1
            entry = self.entries[self.pos]
            self.navigate(
                entry["url"],
                push=False,
                restore_scroll=int(entry.get("scroll") or 0),
                use_cache=True,
            )
        else:
            self.notify("No next page")

    def reload(self) -> None:
        if self.page is not None:
            self.navigate(
                self.page.url,
                push=False,
                restore_scroll=self.page.scroll,
                use_cache=False,
            )
        elif self.entries and self.pos >= 0:
            self.navigate(
                self.entries[self.pos]["url"],
                push=False,
                use_cache=False,
            )

    def stop_loading(self) -> None:
        if self.job is not None:
            self.load_gen += 1
            self.job = None
            self.notify("Loading cancelled")

    # --------------------------------------------------------------- prompt ---
    def prompt_open(self, prefill: str = "") -> None:
        self.prompt = Prompt(
            label="Open: ",
            text=prefill,
            cursor=len(prefill),
            kind="url",
            on_submit=self._submit_open,
        )

    def prompt_edit_url(self) -> None:
        prefill = self.page.url if self.page else ""
        self.prompt_open(prefill)

    def _submit_open(self, text: str) -> None:
        target = text.strip()
        if not target:
            self.notify("No address entered")
            return
        self.navigate(target, push=True)

    def prompt_find(self) -> None:
        if self.page is None or self.page.view is None:
            return
        self.search_saved_scroll = self.page.scroll
        prefill = self.search.query if self.search else ""
        self.prompt = Prompt(
            label="Find: ",
            text=prefill,
            cursor=len(prefill),
            kind="find",
            on_submit=self._finish_search,
            on_cancel=self._cancel_search,
            on_change=self._live_search,
        )
        if prefill:
            self._live_search(prefill)

    def _live_search(self, query: str) -> None:
        if self.page is None or self.page.view is None:
            return
        if not query:
            self.search = None
            if self.search_saved_scroll is not None:
                self.page.scroll = self.search_saved_scroll
            return
        matches = self._find_matches(query)
        saved = self.search_saved_scroll if self.search_saved_scroll is not None else self.page.scroll
        self.search = SearchState(query, matches, 0, saved)
        if matches:
            index = 0
            for i, match in enumerate(matches):
                if match[0] >= saved:
                    index = i
                    break
            self.search.index = index
            self._scroll_to_line(matches[index][0], center=True)

    def _finish_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            self.search = None
            return
        if self.search is None or self.search.query != query:
            self._live_search(query)
        if self.search is not None and self.search.matches:
            count = len(self.search.matches)
            self.notify(f"{count} {plural(count, 'match', 'matches', 'matches')}")
        else:
            self.notify("No matches", error=True)
        self.search_saved_scroll = None

    def _cancel_search(self) -> None:
        self.search = None
        if self.search_saved_scroll is not None and self.page is not None:
            self.page.scroll = self.search_saved_scroll
        self.search_saved_scroll = None

    def _find_matches(self, query: str) -> list[tuple[int, int, int]]:
        if not query or self.page is None or self.page.view is None:
            return []
        q = query.casefold()
        q_len = len(query)
        found: list[tuple[int, int, int]] = []
        for y, line in enumerate(self.page.view.lines):
            text = line.text
            if not text:
                continue
            low = text.casefold()
            start = 0
            while len(found) < 5000:
                pos = low.find(q, start)
                if pos < 0:
                    break
                end = pos + q_len
                x0 = text_width(text[:pos])
                x1 = x0 + text_width(text[pos:end])
                found.append((y, x0, max(x0 + 1, x1)))
                start = pos + max(1, q_len)
        return found

    def search_step(self, delta: int) -> None:
        if self.search is None or not self.search.matches:
            self.notify("No active search")
            return
        self.search.index = (self.search.index + delta) % len(self.search.matches)
        line = self.search.matches[self.search.index][0]
        self._scroll_to_line(line, center=True)

    # -------------------------------------------------------------- hints -----
    def start_hints(self, action: str = "open") -> None:
        if self.page is None or self.page.view is None:
            return
        rendered = self.page.view
        width = self.content_width()
        top = self.content_top
        bottom = top + self.content_h
        seen: set[int] = set()
        entries: list[HintEntry] = []
        for segment in rendered.segments:
            if segment.link in seen:
                continue
            screen_y = top + segment.y - self.page.scroll
            if not (top <= screen_y < bottom):
                continue
            if segment.x1 >= width - 1 or segment.x0 < 0:
                continue
            url = self._link_url(segment.link)
            if not url:
                continue
            seen.add(segment.link)
            entries.append(HintEntry("", segment.link, url, segment.y, segment.x0))
        if not entries:
            self.notify("No visible links on screen")
            return
        for entry, label in zip(entries, make_hint_labels(len(entries))):
            entry.label = label
        self.hints = HintState(entries=entries, action=action)
        self.focus_link = None
        self.overlay = None

    def _activate_hint(self, entry: HintEntry) -> None:
        action = self.hints.action if self.hints else "open"
        self.hints = None
        if action == "download":
            self.download_url(entry.url)
        else:
            self.navigate(entry.url, push=True)

    def _handle_hint_key(self, key) -> None:
        hints = self.hints
        if hints is None:
            return
        if key in ("esc", "ctrl-c", "ctrl-g"):
            self.hints = None
            return
        if key == "backspace":
            hints.prefix = hints.prefix[:-1]
            return
        matches = [e for e in hints.entries if e.label.startswith(hints.prefix)]
        if key == "enter":
            if matches:
                self._activate_hint(matches[0])
            return
        if isinstance(key, str) and len(key) == 1 and key.isprintable() and not key.isspace():
            hints.prefix += key.lower()
            matches = [e for e in hints.entries if e.label.startswith(hints.prefix)]
            if not matches:
                self.notify("No link matches that hint", error=True)
                self.hints = None
            elif len(matches) == 1:
                self._activate_hint(matches[0])

    # --------------------------------------------------------------- focus -----
    def _focus_segments(self, link_id: int):
        if self.page is None or self.page.view is None:
            return []
        return [seg for seg in self.page.view.segments if seg.link == link_id]

    def _link_url(self, link_id: int) -> str | None:
        if self.page is None or self.page.view is None:
            return None
        for link in self.page.view.links:
            if link.id == link_id:
                return link.url
        return None

    def _focus_move(self, delta: int) -> None:
        if self.page is None or self.page.view is None:
            return
        order: list[int] = []
        seen: set[int] = set()
        for segment in self.page.view.segments:
            if segment.link not in seen:
                seen.add(segment.link)
                order.append(segment.link)
        if not order:
            self.notify("No links on this page")
            return
        self.focus_links = order
        if self.focus_link in order:
            index = (order.index(self.focus_link) + delta) % len(order)
        else:
            index = 0 if delta > 0 else len(order) - 1
        self.focus_link = order[index]
        segments = self._focus_segments(self.focus_link)
        if segments:
            self._scroll_to_line(segments[0].y, center=False, margin=2)

    def _focus_open(self) -> None:
        if self.focus_link is None:
            self.notify("No link focused — press Tab or f")
            return
        url = self._link_url(self.focus_link)
        if url:
            self.navigate(url, push=True)

    # ------------------------------------------------------------- overlays -----
    def open_bookmarks(self) -> None:
        items = [
            {"title": item.get("title") or item["url"], "url": item["url"]}
            for item in self.bookmarks
        ]
        if not items:
            self.notify("No bookmarks yet — press B to add the current page")
            return
        self.overlay = Overlay("bookmarks", "Bookmarks", items)
        self.hints = None
        self.focus_link = None

    def open_history(self) -> None:
        items = [
            {"title": e.title or e.url, "url": e.url}
            for e in self.history.recent(300)
        ]
        if not items:
            self.notify("History is empty")
            return
        self.overlay = Overlay("history", "History", items)
        self.hints = None
        self.focus_link = None

    def _handle_overlay_key(self, key) -> None:
        overlay = self.overlay
        if overlay is None:
            return
        count = len(overlay.items)
        if key in ("esc", "q", "ctrl-c"):
            self.overlay = None
            return
        if key in ("j", "down"):
            overlay.index = min(count - 1, overlay.index + 1)
        elif key in ("k", "up"):
            overlay.index = max(0, overlay.index - 1)
        elif key == "pagedown":
            overlay.index = min(count - 1, overlay.index + 8)
        elif key == "pageup":
            overlay.index = max(0, overlay.index - 8)
        elif key in ("home", "g"):
            overlay.index = 0
        elif key in ("end", "G"):
            overlay.index = count - 1
        elif key == "enter":
            item = overlay.items[overlay.index]
            self.overlay = None
            self.navigate(item["url"], push=True)
        elif key == "d":
            self._overlay_delete()
        elif key == "b" and overlay.kind == "bookmarks":
            self.overlay = None
            self.open_bookmarks()

    def _overlay_delete(self) -> None:
        overlay = self.overlay
        if overlay is None or not overlay.items:
            return
        item = overlay.items.pop(overlay.index)
        if overlay.kind == "bookmarks":
            self.bookmarks.remove(item["url"])
        else:
            self.history.remove(item["url"])
        if not overlay.items:
            self.overlay = None
            self.notify("List is now empty")
        else:
            overlay.index = min(overlay.index, len(overlay.items) - 1)
        if self.page is not None and self.page.kind == "about" and self.page.about in (
            "about:bookmarks",
            "about:history",
            "about:start",
        ):
            self._reflow()

    def toggle_bookmark(self) -> None:
        if self.page is None or self.page.kind == "error":
            self.notify("Nothing to bookmark", error=True)
            return
        added = self.bookmarks.toggle(self.page.url, self.page.title or self.page.url)
        if added:
            self.notify(f"Bookmarked: {clip_text(self.page.title or self.page.url, 50)}")
        else:
            self.notify("Bookmark removed")
        if self.page.kind == "about" and self.page.about in ("about:start", "about:bookmarks"):
            self._reflow()

    # -------------------------------------------------------------- actions ----
    def toggle_images(self) -> None:
        self.images_on = not self.images_on
        self.config.images = self.images_on
        self.config.save()
        self.notify("Images on" if self.images_on else "Images off")
        self._reflow()

    def zoom_images(self, factor: float) -> None:
        self.zoom = max(0.25, min(4.0, self.zoom * factor))
        if abs(self.zoom - round(self.zoom)) < 0.02:
            self.zoom = float(round(self.zoom))
        self.config.zoom = self.zoom
        self.config.save()
        self.notify(f"Image zoom: {self.zoom * 100:.0f}%")
        self._reflow()

    def reset_zoom(self) -> None:
        self.zoom = 1.0
        self.config.zoom = 1.0
        self.config.save()
        self.notify("Image zoom reset")
        self._reflow()

    def _reflow(self) -> None:
        if self.page is None:
            return
        page = self.factory.reflow(
            self.page, self.content_width(), images=self.images_on, zoom=self.zoom
        )
        self.page = page
        if page.view is not None:
            limit = max(0, len(page.view.lines) - self.content_height())
            page.scroll = max(0, min(page.scroll, limit))

    def toggle_source(self) -> None:
        if self.page is None or self.page.fetched is None:
            self.notify("Source is not available for this page")
            return
        if not self.page.show_source:
            source = self.factory.source_page(self.page, self.content_width())
            if source is None:
                self.notify("Source is not available")
                return
            self.page.source = source
            self.page.show_source = True
            self.page.scroll = 0
            self.notify("Viewing HTML source — press v to go back")
        else:
            self.page.show_source = False
            self.page.scroll = 0
            self.notify("Back to the rendered page")
        self.search = None
        self.focus_link = None

    def copy_url(self) -> None:
        if self.page is None:
            return
        payload = base64.b64encode(self.page.url.encode("utf-8")).decode("ascii")
        self._write(f"\x1b]52;c;{payload}\x1b\\")
        self.notify("URL copied (OSC 52)")

    def save_page(self) -> None:
        if self.page is None or self.page.fetched is None:
            self.notify("Nothing to save", error=True)
            return
        parsed = urlparse(self.page.url)
        name = os.path.basename(parsed.path) or ("page.html" if self.page.is_html else "page.bin")
        try:
            path = self._save_bytes(name, self.page.fetched.content)
        except OSError as exc:
            self.notify(f"Could not save: {exc}", error=True)
            return
        self.notify(f"Saved {path.name} ({format_bytes(len(self.page.fetched.content))}) → {path.parent}")

    def download_url(self, url: str) -> None:
        self.notify(f"Downloading {clip_middle(url, 50)} …")
        self.bg_count += 1
        referer = self.page.url if self.page else None

        def work() -> None:
            try:
                download = self.session.download(url, referer=referer)
                path = self._save_bytes(download.filename, download.content)
                self.bg_messages.append(
                    (
                        f"Saved {path.name} ({format_bytes(len(download.content))}) → {path.parent}",
                        False,
                    )
                )
            except FetchError as exc:
                self.bg_messages.append((f"Download failed: {exc.message}", True))
            except Exception as exc:  # pragma: no cover - defensive
                self.bg_messages.append((f"Download failed: {exc}", True))
            finally:
                self.bg_count -= 1

        threading.Thread(target=work, daemon=True).start()

    def _save_bytes(self, filename: str, content: bytes) -> Path:
        base = Path(self.config.download_dir).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[\x00-\x1f/\\]", "_", filename).strip() or "download"
        name = name[:180]
        path = base / name
        stem, suffix = os.path.splitext(name)
        index = 1
        while path.exists():
            path = base / f"{stem} ({index}){suffix}"
            index += 1
        path.write_bytes(content)
        return path

    def _drain_bg(self) -> None:
        while self.bg_messages:
            text, is_error = self.bg_messages.pop(0)
            self.notify(text, error=is_error, seconds=6)

    # ---------------------------------------------------------------- input ----
    def handle_key(self, key) -> None:
        if isinstance(key, tuple):
            if key and key[0] == "mouse":
                self._handle_mouse(key)
                return
            if key and key[0] == "text":
                if self.prompt is not None:
                    self._insert_prompt(key[1])
                return
            return

        if self.prompt is not None:
            self._handle_prompt_key(key)
            return
        if self.overlay is not None:
            self._handle_overlay_key(key)
            return
        if self.hints is not None:
            self._handle_hint_key(key)
            return

        if key in ("q", "ctrl-c"):
            self.running = False
        elif key == "esc":
            if self.job is not None:
                self.stop_loading()
            elif self.search is not None:
                self.search = None
                self.notify("Search cleared")
            elif self.focus_link is not None:
                self.focus_link = None
            elif self.hints is not None:
                self.hints = None
        elif key == "o":
            self.prompt_open("")
        elif key == "ctrl-l":
            self.prompt_edit_url()
        elif key in ("r", "ctrl-r"):
            self.reload()
        elif key in ("backspace", "alt-left"):
            self.back()
        elif key == "alt-right":
            self.forward()
        elif key in ("j", "down"):
            self.scroll_by(1)
        elif key in ("k", "up"):
            self.scroll_by(-1)
        elif key in ("space", "pagedown", "ctrl-f"):
            self.scroll_by(self.content_height() - 2)
        elif key in ("pageup", "ctrl-b"):
            self.scroll_by(-(self.content_height() - 2))
        elif key == "ctrl-d":
            self.scroll_by(max(1, self.content_height() // 2))
        elif key == "ctrl-u":
            self.scroll_by(-max(1, self.content_height() // 2))
        elif key in ("home", "g"):
            self.scroll_to(0)
        elif key in ("end", "G"):
            self.scroll_to(self._max_scroll())
        elif key == "f":
            self.start_hints("open")
        elif key == "d":
            self.start_hints("download")
        elif key == "tab":
            self._focus_move(1)
        elif key == "backtab":
            self._focus_move(-1)
        elif key == "enter":
            self._focus_open()
        elif key == "/":
            self.prompt_find()
        elif key == "n":
            self.search_step(1)
        elif key == "N":
            self.search_step(-1)
        elif key == "b":
            self.open_bookmarks()
        elif key == "B":
            self.toggle_bookmark()
        elif key in ("H", "ctrl-h"):
            self.open_history()
        elif key == "?":
            self.navigate("about:help", push=True)
        elif key == "v":
            self.toggle_source()
        elif key == "i":
            self.toggle_images()
        elif key in ("+", "="):
            self.zoom_images(1.25)
        elif key == "-":
            self.zoom_images(0.8)
        elif key == "0":
            self.reset_zoom()
        elif key == "y":
            self.copy_url()
        elif key == "ctrl-s":
            self.save_page()

    def _handle_prompt_key(self, key) -> None:
        prompt = self.prompt
        if prompt is None:
            return
        if key in ("esc", "ctrl-c"):
            self.prompt = None
            if prompt.on_cancel is not None:
                prompt.on_cancel()
            return
        if key == "enter":
            self.prompt = None
            if prompt.on_submit is not None:
                prompt.on_submit(prompt.text)
            return

        text = prompt.text
        cursor = prompt.cursor
        edited = False
        if key in ("backspace", "ctrl-h"):
            if cursor > 0:
                text = text[: cursor - 1] + text[cursor:]
                cursor -= 1
                edited = True
        elif key == "delete":
            if cursor < len(text):
                text = text[:cursor] + text[cursor + 1 :]
                edited = True
        elif key in ("left", "ctrl-b"):
            cursor = max(0, cursor - 1)
        elif key in ("right", "ctrl-f"):
            cursor = min(len(text), cursor + 1)
        elif key in ("home", "ctrl-a"):
            cursor = 0
        elif key in ("end", "ctrl-e"):
            cursor = len(text)
        elif key == "ctrl-u":
            text = text[cursor:]
            cursor = 0
            edited = True
        elif key == "ctrl-k":
            text = text[:cursor]
            edited = True
        elif key == "ctrl-w":
            start = cursor
            while start > 0 and text[start - 1].isspace():
                start -= 1
            while start > 0 and not text[start - 1].isspace():
                start -= 1
            text = text[:start] + text[cursor:]
            cursor = start
            edited = True
        elif key == "ctrl-l":
            text = ""
            cursor = 0
            edited = True
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            text = text[:cursor] + key + text[cursor:]
            cursor += 1
            edited = True
        if edited:
            prompt.text = text
            prompt.cursor = cursor
            if prompt.on_change is not None:
                prompt.on_change(text)
        else:
            prompt.text = text
            prompt.cursor = cursor

    def _insert_prompt(self, chunk: str) -> None:
        prompt = self.prompt
        if prompt is None:
            return
        cleaned = "".join(
            ch for ch in chunk.replace("\r", " ").replace("\n", " ") if ch >= " " or ch == "\t"
        )
        if not cleaned:
            return
        text = prompt.text[: prompt.cursor] + cleaned + prompt.text[prompt.cursor :]
        prompt.text = text
        prompt.cursor += len(cleaned)
        if prompt.on_change is not None:
            prompt.on_change(text)

    def _handle_mouse(self, event: tuple) -> None:
        _, button, x, y, pressed = event
        if button in (64, 65):
            if self.prompt is None and self.overlay is None:
                self.scroll_by(-PAGE_STEP if button == 64 else PAGE_STEP)
            return
        if button in (66, 67) or not pressed:
            return
        if self.prompt is not None or self.overlay is not None or self.hints is not None:
            return
        if (button & 0x3) != 0:
            return
        for (screen_y, x0, x1, link_id) in self.visible_segments:
            if screen_y == y and x0 - 1 <= x <= x1 + 1:
                url = self._link_url(link_id)
                if url:
                    self.navigate(url, push=True)
                return

    # -------------------------------------------------------------- scrolling --
    def _max_scroll(self) -> int:
        if self.page is None or self.page.view is None:
            return 0
        return max(0, len(self.page.view.lines) - self.content_height())

    def scroll_to(self, value: int) -> None:
        if self.page is None:
            return
        self.page.scroll = max(0, min(value, self._max_scroll()))

    def scroll_by(self, delta: int) -> None:
        if self.page is None:
            return
        self.scroll_to(self.page.scroll + delta)

    def _scroll_to_line(self, line: int, *, center: bool = False, margin: int = 1) -> None:
        if self.page is None:
            return
        height = self.content_height()
        if center:
            target = line - height // 2
        else:
            if self.page.scroll + margin <= line < self.page.scroll + height - margin:
                return
            target = line - height // 3
        self.scroll_to(target)

    # ---------------------------------------------------------------- misc -----
    def notify(self, text: str, *, error: bool = False, seconds: float = 3.5) -> None:
        self.message = text
        self.message_until = time.monotonic() + seconds
        self.message_style = Style(fg=theme.RED if error else theme.ACCENT3, bold=error)
