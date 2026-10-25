from __future__ import annotations

from chromabrowser.util import (
    char_width,
    clip_middle,
    clip_text,
    format_bytes,
    plural,
    sanitize,
    split_by_width,
    text_width,
    wrap_plain_text,
)


def test_char_width_ascii_cyrillic_cjk():
    assert char_width("a") == 1
    assert char_width("я") == 1
    assert char_width("日") == 2
    assert char_width("😀") == 2
    assert char_width("\u200b") == 0
    assert char_width("\u0301") == 0  # combining accent


def test_text_width():
    assert text_width("abc") == 3
    assert text_width("привет") == 6
    assert text_width("日a") == 3


def test_sanitize_collapses_and_strips_controls():
    assert sanitize("a\x00b\x07c") == "abc"
    assert sanitize("a   b\t\tc") == "a b c"
    assert sanitize("a \n b") == "a\nb"
    assert sanitize("a\u00a0b") == "a b"
    assert sanitize("  x  ") == " x "


def test_sanitize_keeps_newlines_when_not_collapsing():
    text = sanitize("  a\n\n    b  ", collapse=False)
    assert text == "  a\n\n    b  "


def test_clip_text():
    assert clip_text("hello", 10) == "hello"
    assert clip_text("hello world", 8) == "hello w…"
    assert clip_text("hello", 0) == ""
    assert clip_text("日本", 1) == "…"


def test_clip_middle_keeps_ends():
    url = "https://example.com/very/long/path/to/something"
    out = clip_middle(url, 20)
    assert text_width(out) <= 20
    assert out.startswith("https")
    assert out.endswith("something")
    assert "…" in out


def test_split_by_width():
    assert split_by_width("abcdef", 3) == ["abc", "def"]
    assert split_by_width("ab", 3) == ["ab"]
    assert split_by_width("", 3) == [""]
    assert split_by_width("a日b", 3) == ["a日", "b"]


def test_wrap_plain_text_preserves_blank_lines():
    out = wrap_plain_text("one two\n\nthree", 40)
    assert out == ["one two", "", "three"]


def test_wrap_plain_text_breaks_long_words():
    out = wrap_plain_text("x" * 25, 10)
    assert out == ["x" * 10, "x" * 10, "x" * 5]


def test_plural_russian():
    assert plural(1, "файл", "файла", "файлов") == "файл"
    assert plural(2, "файл", "файла", "файлов") == "файла"
    assert plural(5, "файл", "файла", "файлов") == "файлов"
    assert plural(11, "файл", "файла", "файлов") == "файлов"
    assert plural(21, "файл", "файла", "файлов") == "файл"


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(2048) == "2.0 KB"
    assert format_bytes(5 * 1024 * 1024) == "5.0 MB"
