from __future__ import annotations

from types import SimpleNamespace

import pytest

from chromabrowser import internal
from chromabrowser.storage import Bookmarks, Config, History


@pytest.fixture
def bookmarks(tmp_path):
    return Bookmarks(tmp_path / "b.json")


@pytest.fixture
def history(tmp_path):
    return History(tmp_path / "h.json")


def text(page):
    return "\n".join(line.text for line in page.lines)


def test_start_page_contains_logo_and_shortcuts(bookmarks):
    page = internal.start_page(100, version="9.9.9", config=Config(), bookmarks=bookmarks)
    body = text(page)
    assert "a colorful browser" in body
    assert "9.9.9" in body
    assert "Quick start" in body
    assert page.title == "ChromaBrowser"
    assert page.base_url == "about:start"


def test_start_page_narrow_uses_wordmark(bookmarks):
    page = internal.start_page(30, version="1.0.0", config=Config(), bookmarks=bookmarks)
    body = text(page)
    assert "CHROMA" in body
    assert "BROWSER" in body


def test_start_page_lists_bookmarks(bookmarks):
    bookmarks.add("https://example.com/", "Example")
    page = internal.start_page(100, version="1.0.0", config=Config(), bookmarks=bookmarks)
    assert "Example" in text(page)
    assert any(link.url == "https://example.com/" for link in page.links)


def test_help_page_sections():
    page = internal.help_page(100, version="1.0.0")
    body = text(page)
    for section in ("Navigation", "Scrolling", "Finding & copying", "Bookmarks & history", "Media"):
        assert section in body
    assert "Ctrl+L" in body
    assert any(link.url == "about:start" for link in page.links)


def test_bookmarks_page_empty(bookmarks):
    page = internal.bookmarks_page(80, bookmarks)
    assert "No bookmarks yet" in text(page)


def test_bookmarks_page_with_items(bookmarks):
    bookmarks.add("https://a.test/", "Alpha")
    bookmarks.add("https://b.test/", "Beta")
    page = internal.bookmarks_page(80, bookmarks)
    body = text(page)
    assert "Alpha" in body and "Beta" in body
    assert "https://a.test/" in body


def test_history_page_grouped_by_day(history):
    history.add("https://a.test/", "Alpha")
    page = internal.history_page(80, history)
    body = text(page)
    assert "HISTORY" in body
    assert "Alpha" in body


def test_history_page_empty(history):
    page = internal.history_page(80, history)
    assert "History is empty" in text(page)


def test_error_page_shows_details():
    page = internal.error_page(80, "https://bad.test/", "connect failed", 503)
    body = text(page)
    assert "Could not open the page" in body
    assert "connect failed" in body
    assert "HTTP status: 503" in body
    assert "https://bad.test/" in body


def test_text_page_keeps_indentation_and_blank_lines():
    page = internal.text_page("def f():\n    x = 1\n\n    return x", 60, title="src", code=True)
    lines = [line.text for line in page.lines]
    assert "def f():" in lines
    assert "    x = 1" in lines
    assert "" in lines
    assert "    return x" in lines


def test_text_page_wraps_long_lines():
    page = internal.text_page("z" * 100, 20)
    body = [line.text for line in page.lines if line.text.strip()]
    assert len(body) == 5
    assert all(len(line) <= 20 for line in body)


def test_image_page_renders_image():
    class Loader:
        def render(self, url, width, referer=None):
            from chromabrowser.imaging import ImageBlock
            from chromabrowser.theme import Style

            return ImageBlock(rows=[[("▀", Style(fg=(1, 2, 3)))]], width=1, height=1,
                              source_width=10, source_height=10)

    fetched = SimpleNamespace(content=b"x" * 2048, content_type="image/png")
    page = internal.image_page(80, "https://e.test/a.png", Loader(), fetched)
    assert any(line.image is not None for line in page.lines)
    body = text(page)
    assert "10×10 px" in body
    assert "a.png" in body


def test_image_page_error():
    class Loader:
        def render(self, url, width, referer=None):
            from chromabrowser.imaging import ImageBlock

            return ImageBlock(error="nope")

    fetched = SimpleNamespace(content=b"", content_type="image/png")
    page = internal.image_page(80, "https://e.test/a.png", Loader(), fetched)
    assert "Could not display image: nope" in text(page)
