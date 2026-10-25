"""Shared pytest fixtures.

The XDG directories are redirected to a temporary location *before* the
package is imported, so tests never touch the user's real history or cache.
"""

from __future__ import annotations

import os
import tempfile

_XDG_ROOT = os.path.join(tempfile.gettempdir(), "chroma-pytest-xdg")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_XDG_ROOT, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_XDG_ROOT, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_XDG_ROOT, "cache")
os.environ.pop("COLUMNS", None)
os.environ.pop("LINES", None)

import pytest

from chromabrowser.htmlrender import HTMLRenderer, RenderedPage  # noqa: E402
from chromabrowser.storage import Config  # noqa: E402


@pytest.fixture
def config() -> Config:
    return Config()


def render_html(html: str, width: int = 80, base: str = "https://example.test/page.html") -> RenderedPage:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    renderer = HTMLRenderer(width, base, images=False)
    return renderer.render(soup)


@pytest.fixture
def render():
    return render_html


def page_text(page: RenderedPage, *, keep_blank: bool = False) -> str:
    lines = [line.text.rstrip() for line in page.lines]
    if not keep_blank:
        lines = [line for line in lines if line]
    return "\n".join(lines)
