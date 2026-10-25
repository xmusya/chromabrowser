"""Fetch + render a URL into a Page (shared by the TUI and --dump)."""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

from . import __version__, internal
from .htmlrender import RenderedPage
from .imaging import ImageLoader
from .net import BrowserSession, Fetched
from .storage import Bookmarks, Config, History
from .util import format_bytes


@dataclass
class Page:
    url: str
    kind: str  # html | text | image | about | error
    rendered: RenderedPage | None = None
    dom: BeautifulSoup | None = None
    fetched: Fetched | None = None
    about: str | None = None
    error: tuple[str, int | None] | None = None
    source: RenderedPage | None = None
    show_source: bool = False
    scroll: int = 0
    width: int = 0
    title: str = ""
    status: int | None = None
    elapsed: float = 0.0

    @property
    def view(self) -> RenderedPage | None:
        if self.show_source and self.source is not None:
            return self.source
        return self.rendered

    @property
    def is_html(self) -> bool:
        return self.kind == "html"


class PageFactory:
    def __init__(
        self,
        config: Config,
        session: BrowserSession,
        loader: ImageLoader,
        *,
        history: History | None = None,
        bookmarks: Bookmarks | None = None,
    ):
        self.config = config
        self.session = session
        self.loader = loader
        self.history = history
        self.bookmarks = bookmarks

    # --------------------------------------------------------------- public ---
    def fetch(self, url: str, width: int, *, images: bool | None = None, zoom: float | None = None) -> Page:
        images = self.config.images if images is None else images
        zoom = self.config.zoom if zoom is None else zoom
        if url.startswith("about:"):
            return self.about(url, width)
        fetched = self.session.fetch(url)
        if fetched.is_image:
            page = self._image_page(fetched, width)
        elif fetched.is_html:
            page = self._html_page(fetched, width, images, zoom)
        else:
            page = self._text_page(fetched, width)
        page.status = fetched.status
        page.elapsed = fetched.elapsed
        return page

    def about(self, url: str, width: int) -> Page:
        url = url.lower()
        if url in ("about:start", "about:home", "about:"):
            rendered = internal.start_page(
                width,
                version=__version__,
                config=self.config,
                bookmarks=self.bookmarks,
            )
        elif url in ("about:help", "about:keys"):
            rendered = internal.help_page(width, version=__version__)
        elif url == "about:bookmarks":
            rendered = internal.bookmarks_page(width, self.bookmarks or Bookmarks())
        elif url == "about:history":
            rendered = internal.history_page(width, self.history or History())
        elif url in ("about:version", "about:about"):
            text = (
                f"ChromaBrowser {__version__}\n\n"
                "A terminal web browser with inline images.\n\n"
                "Python + requests + BeautifulSoup + Pillow.\n"
                "Rendering: cells, truecolor half-blocks, diffed ANSI output.\n"
                "Config: ~/.config/chromabrowser/config.json"
            )
            rendered = internal.text_page(text, width, title="About ChromaBrowser", url=url)
        else:
            rendered = internal.error_page(width, url, f"Unknown internal page: {url}")
        return Page(
            url=url,
            kind="about",
            rendered=rendered,
            about=url,
            width=width,
            title=rendered.title,
        )

    def error(self, url: str, message: str, status: int | None, width: int) -> Page:
        rendered = internal.error_page(width, url, message, status)
        return Page(
            url=url,
            kind="error",
            rendered=rendered,
            error=(message, status),
            width=width,
            title="Error",
            status=status,
        )

    def source_page(self, page: Page, width: int) -> RenderedPage | None:
        if page.fetched is None:
            return None
        text = page.fetched.text
        note = f"{page.fetched.content_type or 'text/html'} · {format_bytes(len(page.fetched.content))}"
        limit = 2_000_000
        if len(text) > limit:
            text = text[:limit] + "\n… source truncated …"
            note += " · truncated"
        return internal.text_page(
            text,
            width,
            title=f"source: {page.title or page.url}",
            url=page.url,
            code=True,
            note=note,
        )

    def reflow(self, page: Page, width: int, *, images: bool, zoom: float) -> Page:
        if page.kind == "html" and page.dom is not None:
            page.rendered = self._render_html(page.dom, page.url, width, images, zoom)
        elif page.kind == "about":
            fresh = self.about(page.about or page.url, width)
            page.rendered = fresh.rendered
        elif page.kind == "text" and page.fetched is not None:
            page.rendered = self._text_page(page.fetched, width).rendered
        elif page.kind == "image" and page.fetched is not None:
            page.rendered = self._image_page(page.fetched, width, images).rendered
        elif page.kind == "error" and page.error is not None:
            message, status = page.error
            page.rendered = internal.error_page(width, page.url, message, status)

        if page.show_source and page.fetched is not None:
            page.source = self.source_page(page, width)
        page.width = width
        if page.rendered is not None:
            page.title = page.rendered.title or page.title
        return page

    # --------------------------------------------------------------- private ---
    def _render_html(
        self,
        dom: BeautifulSoup,
        url: str,
        width: int,
        images: bool,
        zoom: float,
    ) -> RenderedPage:
        renderer = internal_html_renderer(
            width,
            url,
            images=images,
            loader=self.loader,
            zoom=zoom,
        )
        return renderer.render(dom)

    def _html_page(self, fetched: Fetched, width: int, images: bool, zoom: float) -> Page:
        dom = BeautifulSoup(fetched.text, "html.parser")
        rendered = self._render_html(dom, fetched.url, width, images, zoom)
        return Page(
            url=fetched.url,
            kind="html",
            rendered=rendered,
            dom=dom,
            fetched=fetched,
            width=width,
            title=rendered.title,
        )

    def _text_page(self, fetched: Fetched, width: int) -> Page:
        name = fetched.url.rsplit("/", 1)[-1].split("?")[0] or "text"
        note = f"{fetched.content_type or 'text/plain'} · {format_bytes(len(fetched.content))}"
        rendered = internal.text_page(
            fetched.text,
            width,
            title=name,
            url=fetched.url,
            note=note,
        )
        return Page(
            url=fetched.url,
            kind="text",
            rendered=rendered,
            fetched=fetched,
            width=width,
            title=rendered.title,
        )

    def _image_page(self, fetched: Fetched, width: int, images: bool = True) -> Page:
        if images:
            rendered = internal.image_page(width, fetched.url, self.loader, fetched)
        else:
            rendered = internal.text_page(
                f"[image] {fetched.url}\n\nImages are turned off (press i to enable).",
                width,
                title=fetched.url.rsplit("/", 1)[-1] or "Image",
                url=fetched.url,
            )
        return Page(
            url=fetched.url,
            kind="image",
            rendered=rendered,
            fetched=fetched,
            width=width,
            title=rendered.title,
        )


def internal_html_renderer(width: int, url: str, *, images: bool, loader, zoom: float):
    from .htmlrender import HTMLRenderer

    return HTMLRenderer(
        width,
        url,
        images=images,
        image_loader=loader if images else None,
        zoom=zoom,
        loader_referer=url,
    )
