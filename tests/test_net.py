from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import quote

import pytest

from chromabrowser.net import (
    BrowserSession,
    FetchError,
    decode_content,
    humanize_net_error,
)

@pytest.fixture
def session(config):
    s = BrowserSession(config)
    yield s
    s.close()


def test_resolve_absolute_and_internal(session):
    assert session.resolve("https://example.com/x") == "https://example.com/x"
    assert session.resolve("http://example.com") == "http://example.com"
    assert session.resolve("about:help") == "about:help"


def test_resolve_bare_domain(session):
    assert session.resolve("example.com") == "https://example.com"
    assert session.resolve("example.com/path?q=1") == "https://example.com/path?q=1"
    assert session.resolve("localhost:8000/x") == "https://localhost:8000/x"


def test_resolve_local_paths_and_files(session, tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<h1>x</h1>", encoding="utf-8")
    assert session.resolve(str(f)).startswith("file://")
    assert session.resolve(str(tmp_path)).startswith("file://")


def test_resolve_search_query(session):
    url = session.resolve("привет мир")
    assert url.startswith("https://")
    assert quote("привет мир") in url


def test_resolve_empty_uses_homepage(session):
    assert session.resolve("") == session.config.homepage


def test_resolve_unknown_scheme_is_preserved(session):
    assert session.resolve("gopher://example.com/") == "gopher://example.com/"


def test_decode_content_charset_header():
    text, charset = decode_content("привет".encode("cp1251"), {"content-type": "text/html; charset=cp1251"})
    assert text == "привет"
    assert charset == "cp1251"


def test_decode_content_meta_charset():
    body = '<meta charset="koi8-r"><p>тест</p>'.encode("koi8-r")
    text, charset = decode_content(body, {})
    assert "тест" in text
    assert charset == "koi8-r"


def test_decode_content_invalid_bytes_fall_back():
    text, _ = decode_content(b"\xff\xfe\x00bad", {"content-type": "text/plain"})
    assert isinstance(text, str)


def test_fetch_file(session, tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello", encoding="utf-8")
    fetched = session.fetch(f.as_uri())
    assert fetched.text == "hello"
    assert fetched.status == 200
    assert fetched.content_type == "text/plain"


def test_fetch_missing_file_raises(session):
    with pytest.raises(FetchError):
        session.fetch((Path("/nonexistent/nope.html")).as_uri())


def test_fetch_unsupported_scheme(session):
    with pytest.raises(FetchError) as err:
        session.fetch("ftp://example.com/file")
    assert "Unsupported scheme" in str(err.value.message)


def test_image_from_data_uri(session):
    payload = base64.b64encode(b"GIF89a").decode()
    data = session.fetch_image(f"data:image/gif;base64,{payload}")
    assert data == b"GIF89a"


def test_invalid_data_uri(session):
    with pytest.raises(FetchError):
        session.fetch_image("data:image/png;base64,!!!!")


def test_image_from_file(session, tmp_path):
    p = tmp_path / "img.bin"
    p.write_bytes(b"\x00\x01\x02")
    assert session.fetch_image(p.as_uri()) == b"\x00\x01\x02"
    # Second call comes from memory.
    assert session.fetch_image(p.as_uri()) == b"\x00\x01\x02"


def test_download_file(session, tmp_path):
    p = tmp_path / "doc.txt"
    p.write_bytes(b"data")
    download = session.download(p.as_uri())
    assert download.filename == "doc.txt"
    assert download.content == b"data"


def test_site_label(session):
    assert session.site_label("https://example.com/a") == "example.com"
    assert session.site_label("file:///tmp/x") == "local file"
    assert session.site_label("about:start") == "internal page"


def test_humanize_net_error_passthrough():
    assert "boom" in humanize_net_error(RuntimeError("boom"))
