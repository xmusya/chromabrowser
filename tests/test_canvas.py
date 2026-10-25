from __future__ import annotations

from chromabrowser.canvas import Canvas, rgb_to_xterm256, sgr
from chromabrowser.theme import BG, FG, Style


def test_put_and_lines():
    c = Canvas(10, 3)
    c.put(1, 1, "hello")
    assert c.lines()[1] == " hello    "
    assert c.text_at(1, 1, 5) == "hello"


def test_put_clips_at_right_edge():
    c = Canvas(5, 1)
    c.put(3, 0, "abcdef")
    assert c.lines()[0] == "   ab"


def test_put_wide_chars_occupy_two_cells():
    c = Canvas(6, 1)
    c.put(0, 0, "日a")
    assert c.lines()[0] == "日a   "
    assert c.grid[0][1] == ("", c.grid[0][0][1])


def test_put_cells_and_fill():
    c = Canvas(6, 2)
    c.fill_rect(0, 0, 3, 1, Style(bg=(1, 2, 3)))
    assert c.lines()[0] == "      "
    assert c.grid[0][0][1].bg == (1, 2, 3)
    assert c.grid[0][3][1].bg == BG


def test_box_draws_corners_and_title():
    c = Canvas(20, 5)
    c.box(0, 0, 10, 4, Style(fg=FG), title="hi")
    lines = c.lines()
    assert lines[0].startswith("╭─ hi ───╮")
    assert lines[3].startswith("╰────────╯")


def test_sgr_truecolor_and_256():
    seq = sgr(Style(fg=(255, 0, 0), bg=(0, 0, 255), bold=True), True)
    assert "38;2;255;0;0" in seq
    assert "48;2;0;0;255" in seq
    assert "1" in seq.split(";")
    seq256 = sgr(Style(fg=(255, 0, 0)), False)
    assert "38;5;" in seq256


def test_rgb_to_xterm256_known_values():
    assert rgb_to_xterm256((0, 0, 0)) == 16
    assert rgb_to_xterm256((255, 255, 255)) == 231
    assert rgb_to_xterm256((128, 128, 128)) in range(232, 256)


def test_render_skips_unchanged_cells():
    c = Canvas(10, 1)
    c.put(0, 0, "abc")
    snap = c.snapshot()
    first = c.render(None, True)
    assert "abc" in first
    assert "abc" not in c.render(snap, True)
    c.put(0, 0, "xbc")
    diff = c.render(snap, True)
    assert diff
    assert "\x1b[1;1H" in diff
    assert "x" in diff


def test_render_positions_cursor_absolutely():
    c = Canvas(10, 2)
    c.put(2, 1, "ok", Style(fg=(10, 20, 30)))
    stream = c.render(None, True)
    assert "\x1b[2;3H" in stream
    assert stream.endswith("\x1b[0m")
