from __future__ import annotations

import pytest

from chromabrowser.net import BrowserSession
from chromabrowser.imaging import ImageLoader
from chromabrowser.pagebuild import PageFactory
from chromabrowser.storage import Bookmarks, History

PNG_1PX_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def factory(config, tmp_path):
    session = BrowserSession(config)
    loader = ImageLoader(session, max_width=40)
    yield PageFactory(
        config,
        session,
        loader,
        history=History(tmp_path / "h.json"),
        bookmarks=Bookmarks(tmp_path / "b.json"),
    )
    session.close()


@pytest.fixture
def html_file(tmp_path):
    img = tmp_path / "dot.png"
    import base64

    img.write_bytes(base64.b64decode(PNG_1PX_B64))
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>T</title></head><body>"
        "<h1>Hello</h1><p>Some text with a <a href='other.html'>link</a>.</p>"
        "<img src='dot.png' alt='dot'></body></html>",
        encoding="utf-8",
    )
    return page


def test_fetch_html_page(factory, html_file):
    page = factory.fetch(html_file.as_uri(), 60, images=False)
    assert page.kind == "html"
    assert page.title == "T"
    assert page.dom is not None
    assert page.fetched is not None
    assert page.view is not None
    body = "\n".join(line.text for line in page.view.lines)
    assert "Hello" in body
    assert "[image: dot]" in body
    assert page.url.startswith("file://")


def test_fetch_plain_text(factory, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello\nworld", encoding="utf-8")
    page = factory.fetch(f.as_uri(), 40)
    assert page.kind == "text"
    assert "hello" in "\n".join(line.text for line in page.view.lines)


def test_fetch_image_page(factory, tmp_path):
    import base64

    p = tmp_path / "dot.png"
    p.write_bytes(base64.b64decode(PNG_1PX_B64))
    page = factory.fetch(p.as_uri(), 40)
    assert page.kind == "image"
    # A 1x1 image is rejected by the loader, so an error line is rendered.
    body = "\n".join(line.text for line in page.view.lines)
    assert "Could not display image" in body


def test_reflow_changes_line_count(factory, html_file):
    page = factory.fetch(html_file.as_uri(), 80, images=False)
    wide = len(page.view.lines)
    page = factory.reflow(page, 30, images=False, zoom=1.0)
    narrow = len(page.view.lines)
    assert page.width == 30
    assert narrow >= wide


def test_source_page(factory, html_file):
    page = factory.fetch(html_file.as_uri(), 60, images=False)
    source = factory.source_page(page, 60)
    assert source is not None
    body = "\n".join(line.text for line in source.lines)
    assert "<html>" in body or "<h1>" in body
    assert "source:" in source.title


def test_about_pages(factory):
    assert factory.about("about:start", 80).title == "ChromaBrowser"
    assert "Everything you can press" in "\n".join(
        line.text for line in factory.about("about:help", 80).view.lines
    )
    assert factory.about("about:nope", 80).kind == "about"
    assert "Unknown internal page" in "\n".join(
        line.text for line in factory.about("about:nope", 80).view.lines
    )


def test_error_page(factory):
    page = factory.error("https://bad.test/", "boom", None, 80)
    assert page.kind == "error"
    assert page.error == ("boom", None)
    assert "boom" in "\n".join(line.text for line in page.view.lines)


def test_broken_file_error(factory):
    with pytest.raises(Exception):
        factory.fetch("file:///definitely/not/here.html", 60)
