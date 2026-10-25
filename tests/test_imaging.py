from __future__ import annotations

import pytest
from PIL import Image

from chromabrowser.imaging import ImageLoader, terminal_supports_unicode
from chromabrowser.net import BrowserSession


@pytest.fixture
def loader(config, tmp_path):
    session = BrowserSession(config)
    yield ImageLoader(session, max_width=40)
    session.close()


def make_png(path, size=(160, 100), color=(255, 0, 0)):
    img = Image.new("RGB", size, color)
    img.save(path)
    return path


def test_render_png_from_file(loader, tmp_path):
    path = make_png(tmp_path / "red.png")
    block = loader.render(path.as_uri(), 30)
    assert block.ok
    assert block.error is None
    assert 2 <= block.width <= 30
    assert block.height >= 1
    assert len(block.rows) == block.height
    assert all(len(row) == block.width for row in block.rows)
    assert block.source_width == 160
    assert block.source_height == 100
    ch, style = block.rows[0][0]
    assert ch in ("▀", "█", " ")


def test_render_is_cached(loader, tmp_path):
    path = make_png(tmp_path / "red.png")
    first = loader.render(path.as_uri(), 30)
    second = loader.render(path.as_uri(), 30)
    assert first is second


def test_render_respects_max_width(loader, tmp_path):
    path = make_png(tmp_path / "wide.png", size=(2000, 200))
    block = loader.render(path.as_uri(), 500)
    assert block.width <= 40  # ImageLoader.max_width


def test_render_tall_image_is_capped(loader, tmp_path):
    path = make_png(tmp_path / "tall.png", size=(20, 4000))
    block = loader.render(path.as_uri(), 30)
    assert block.ok
    assert block.height <= 56


def test_broken_image_reports_error(loader, tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image at all")
    block = loader.render(path.as_uri(), 30)
    assert not block.ok
    assert block.error


def test_missing_image_reports_error(loader, tmp_path):
    block = loader.render((tmp_path / "nope.png").as_uri(), 30)
    assert not block.ok


def test_tiny_image_reports_error(loader, tmp_path):
    img = Image.new("RGB", (1, 1), (0, 0, 0))
    path = tmp_path / "tiny.png"
    img.save(path)
    block = loader.render(path.as_uri(), 10)
    assert not block.ok


def test_alpha_is_composited_on_matte(loader, tmp_path):
    img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    path = tmp_path / "alpha.png"
    img.save(path)
    block = loader.render(path.as_uri(), 20)
    assert block.ok
    ch, style = block.rows[0][0]
    # Fully transparent pixels become the dark matte, not black.
    assert style.fg is not None
    assert style.fg != (0, 0, 0)


def test_zoom_changes_size(loader, tmp_path):
    path = make_png(tmp_path / "z.png", size=(400, 400))
    small = loader.render(path.as_uri(), 40, zoom=0.5)
    big = loader.render(path.as_uri(), 40, zoom=1.4)
    assert big.width >= small.width


def test_no_unicode_uses_spaces(loader, tmp_path):
    path = make_png(tmp_path / "solid.png", size=(80, 80), color=(10, 200, 30))
    loader.unicode = False
    block = loader.render(path.as_uri(), 20)
    chars = {ch for row in block.rows for ch, _ in row}
    assert chars == {" "}
    styles = [style for row in block.rows for _, style in row]
    assert all(style.bg is not None for style in styles)


def test_terminal_supports_unicode_env(monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("LC_ALL", "")
    monkeypatch.setenv("LC_CTYPE", "")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert terminal_supports_unicode()
    monkeypatch.setenv("TERM", "linux")
    assert not terminal_supports_unicode()
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_ALL", "C")
    assert not terminal_supports_unicode()
