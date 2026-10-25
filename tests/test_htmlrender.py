from __future__ import annotations

from bs4 import BeautifulSoup

from chromabrowser import theme
from chromabrowser.htmlrender import HTMLRenderer, LinkSegment
from chromabrowser.imaging import ImageBlock
from chromabrowser.theme import Style

from conftest import page_text, render_html


def test_title_prefers_title_tag():
    page = render_html("<html><head><title> My title </title></head><body><h1>H</h1></body></html>")
    assert page.title == "My title"


def test_title_falls_back_to_h1():
    page = render_html("<html><body><h1>Hello world</h1></body></html>")
    assert page.title == "Hello world"


def test_headings_are_rendered_and_colored():
    page = render_html("<h1>One</h1><h2>Two</h2><h3>Three</h3>")
    text = page_text(page)
    assert "One" in text and "Two" in text and "Three" in text
    h1 = next(line for line in page.lines if "One" in line.text)
    assert h1.text.startswith("▌ ")
    assert h1.runs[0].style.bold
    assert h1.runs[0].style.fg == theme.HEADING_COLORS[0]
    h3 = next(line for line in page.lines if "Three" in line.text)
    assert not h3.text.startswith("▌ ")


def test_h1_has_a_rule():
    page = render_html("<h1>Title</h1>")
    rules = [line for line in page.lines if line.text.startswith("─")]
    assert rules
    assert len(rules[0].text) <= 70


def test_paragraph_wraps_at_width():
    page = render_html("<p>" + "word " * 40 + "</p>", width=40)
    body = [line.text for line in page.lines if line.text.strip()]
    assert all(len(line) <= 40 for line in body)
    assert len(body) > 1


def test_long_word_is_split():
    page = render_html("<p>" + "x" * 100 + "</p>", width=30)
    body = [line.text for line in page.lines if line.text.strip()]
    assert all(len(line) <= 30 for line in body)
    assert len(body) == 4


def test_inline_styles():
    page = render_html(
        "<p><b>bold</b> <i>italic</i> <u>under</u> <s>strike</s> "
        "<code>code</code> <mark>mark</mark></p>"
    )
    runs = {run.text.strip(): run for line in page.lines for run in line.runs if run.text.strip()}
    assert runs["bold"].style.bold
    assert runs["italic"].style.italic
    assert runs["under"].style.underline
    assert runs["strike"].style.strike
    assert runs["code"].style.bg == theme.CODE_BG
    assert runs["mark"].style.bg == theme.ACCENT3


def test_css_color_and_weight():
    page = render_html(
        '<p><span style="color:#ff0000;font-weight:700">red</span>'
        '<span style="color:#010203">dark</span></p>'
    )
    runs = {run.text.strip(): run for line in page.lines for run in line.runs if run.text.strip()}
    assert runs["red"].style.fg == (255, 0, 0)
    assert runs["red"].style.bold
    assert runs["dark"].style.fg != (1, 2, 3)  # too dark to be readable


def test_links_are_collected_and_resolved():
    page = render_html('<p>Go <a href="/next">there</a> now</p>')
    assert len(page.links) == 1
    assert page.links[0].url == "https://example.test/next"
    assert page.links[0].text == "there"
    link_run = next(run for line in page.lines for run in line.runs if run.link is not None)
    assert link_run.style.fg == theme.LINK
    assert link_run.style.underline
    assert any(isinstance(seg, LinkSegment) for seg in page.segments)


def test_duplicate_links_share_an_id():
    page = render_html('<a href="/x">one</a> <a href="/x">two</a>')
    assert len(page.links) == 1
    ids = {run.link for line in page.lines for run in line.runs if run.link is not None}
    assert ids == {0}


def test_unsupported_link_schemes_are_dropped():
    page = render_html(
        '<a href="javascript:alert(1)">js</a> <a href="mailto:a@b.c">mail</a> '
        '<a href="#anchor">anchor</a> <a href="data:text/plain,x">data</a>'
    )
    assert page.links == []
    assert "js" in page_text(page)


def test_segment_x_positions():
    page = render_html("<p>ab <a href='/x'>cd</a> ef</p>", width=40)
    seg = page.segments[0]
    line = next(line for line in page.lines if "ab" in line.text)
    assert line.text.startswith("ab cd")
    assert seg.x0 == 3
    assert seg.x1 == 4


def test_unordered_and_nested_lists():
    page = render_html("<ul><li>first</li><li>second<ul><li>nested</li></ul></li></ul>")
    lines = [line.text for line in page.lines if line.text.strip()]
    assert any("• first" in line for line in lines)
    assert any("• second" in line for line in lines)
    nested = next(line for line in lines if "nested" in line)
    assert nested.index("•") > lines[0].index("•")


def test_ordered_list_start_and_numbering():
    page = render_html('<ol start="3"><li>a</li><li>b</li></ol>')
    lines = [line.text.strip() for line in page.lines if line.text.strip()]
    assert "3. a" in lines
    assert "4. b" in lines


def test_blockquote_has_bar_on_every_line():
    page = render_html("<blockquote>" + "quote " * 30 + "</blockquote>", width=40)
    lines = [line.text for line in page.lines if "quote" in line.text]
    assert len(lines) > 1
    for line in lines:
        assert line.strip().startswith("▌")


def test_preformatted_keeps_spaces():
    page = render_html("<pre>def f():\n    return 1\n\nx</pre>")
    text = page_text(page, keep_blank=True)
    assert "    return 1" in text
    assert "def f():" in text


def test_preformatted_scrolls_long_line():
    page = render_html("<pre>" + "y" * 50 + "</pre>", width=24)
    body = [line.text for line in page.lines if line.text.strip()]
    assert len(body) == 3
    assert all(len(line) <= 24 for line in body)


def test_table_box_and_content():
    page = render_html(
        "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
        "<tbody><tr><td>alpha</td><td>42</td></tr>"
        "<tr><td>beta</td><td>7</td></tr></tbody></table>"
    )
    lines = [line.text for line in page.lines if any(ch in line.text for ch in "│┌└")]
    assert lines[0].startswith("┌")
    assert any("Name" in line for line in lines)
    assert any("alpha" in line for line in lines)
    assert any(line.startswith("└") for line in lines)
    widths = {len(line) for line in lines}
    assert len(widths) == 1, widths


def test_table_wraps_cell_content():
    page = render_html(
        "<table><tr><td>" + "long text " * 20 + "</td><td>x</td></tr></table>",
        width=40,
    )
    table_lines = [line.text for line in page.lines if any(ch in line.text for ch in "│┌└")]
    assert all(len(line) <= 40 for line in table_lines)


def test_table_without_rows_is_skipped():
    page = render_html("<table><caption>empty</caption></table><p>after</p>")
    assert "after" in page_text(page)


def test_forms_render_as_widgets():
    page = render_html(
        "<form><input placeholder='name'> <input type='checkbox' checked> "
        "<input type='submit' value='Go'> "
        "<select><option>a</option><option selected>b</option></select>"
        "<textarea>hi</textarea></form>"
    )
    text = page_text(page)
    assert "[ name ]" in text
    assert "[x]" in text
    assert "[ Go ]" in text
    assert "[ b ▾ ]" in text
    assert "[ hi ]" in text


def test_hidden_input_is_skipped():
    page = render_html("<form><input type='hidden' value='secret'></form>")
    assert "secret" not in page_text(page)


def test_hr_renders_a_rule():
    page = render_html("<p>a</p><hr><p>b</p>")
    rules = [line for line in page.lines if set(line.text.strip()) == {"─"} and line.text.strip()]
    assert rules


def test_images_disabled_show_placeholder():
    page = render_html('<img src="pic.png" alt="A picture">')
    assert "[image: A picture]" in page_text(page)


def test_empty_page_message():
    page = render_html("<html><body></body></html>")
    assert "(this page is empty)" in page_text(page)


class FakeLoader:
    def __init__(self):
        self.calls = []

    def render(self, url, avail, *, zoom=1.0, referer=None):
        self.calls.append((url, avail, zoom, referer))
        return ImageBlock(
            rows=[[("▀", Style(fg=(1, 2, 3), bg=(4, 5, 6)))]],
            width=1,
            height=1,
        )


def test_images_render_through_loader():
    loader = FakeLoader()
    soup = BeautifulSoup('<p>before</p><img src="pic.png" alt="cap">', "html.parser")
    page = HTMLRenderer(50, "https://example.test/a/b.html", images=True, image_loader=loader).render(soup)
    assert loader.calls
    url, avail, zoom, referer = loader.calls[0]
    assert url == "https://example.test/a/pic.png"
    assert referer == "https://example.test/a/b.html"
    image_lines = [line for line in page.lines if line.image is not None]
    assert len(image_lines) == 1
    assert image_lines[0].image[0][0] == "▀"
    assert "cap" in page_text(page)


def test_image_error_is_reported():
    class Broken:
        def render(self, *a, **k):
            return ImageBlock(error="boom")

    soup = BeautifulSoup('<img src="x.png" alt="pic">', "html.parser")
    page = HTMLRenderer(50, "https://e.test/", images=True, image_loader=Broken()).render(soup)
    assert "boom" in page_text(page)


def test_data_src_and_srcset_are_used():
    page = render_html('<img data-src="real.png" alt="x">')
    assert "[image: x]" in page_text(page) or "real.png" in page_text(page)


def test_nested_blocks_do_not_crash():
    page = render_html(
        "<div><section><article><p>a<div>b</div>c</p></article></section></div>"
    )
    text = page_text(page)
    assert "a" in text and "b" in text and "c" in text


def test_inline_html_newlines_collapse_to_spaces():
    page = render_html("<p>hello\n   world\n\nand more</p>")
    body = page_text(page)
    assert body == "hello world and more"


def test_br_creates_line_breaks():
    page = render_html("<p>one<br>two<br><br>three</p>")
    lines = [line.text for line in page.lines]
    assert "one" in lines
    assert "two" in lines
    assert "three" in lines


def test_links_inside_lists_are_collected():
    page = render_html("<ul><li><a href='/a'>A</a></li><li><a href='/b'>B</a></li></ul>")
    assert [link.url for link in page.links] == [
        "https://example.test/a",
        "https://example.test/b",
    ]


def test_base_tag_changes_resolution():
    page = render_html('<head><base href="https://cdn.test/assets/"></head>'
                       '<body><a href="x.html">x</a><img src="y.png" alt="y"></body>')
    assert page.base_url == "https://cdn.test/assets/"
    assert page.links[0].url == "https://cdn.test/assets/x.html"


def test_table_caption_is_shown():
    page = render_html("<table><caption>My table</caption><tr><td>x</td></tr></table>")
    assert "My table" in page_text(page)


def test_nested_table_does_not_break_layout():
    page = render_html(
        "<table><tr><td>outer<table><tr><td>inner</td></tr></table></td>"
        "<td>right</td></tr></table>"
    )
    text = page_text(page)
    assert "outer" in text
    assert "inner" in text
    assert "right" in text
    lines = [line.text for line in page.lines if any(ch in line.text for ch in "│┌└")]
    assert len({len(line) for line in lines}) == 1


def test_colspan_is_tolerated():
    page = render_html(
        "<table><tr><td colspan='2'>wide</td></tr><tr><td>a</td><td>b</td></tr></table>"
    )
    text = page_text(page)
    assert "wide" in text and "a" in text and "b" in text


def test_definition_list():
    page = render_html("<dl><dt>Term</dt><dd>Definition</dd></dl>")
    lines = [line.text for line in page.lines if line.text.strip()]
    term = next(line for line in lines if "Term" in line)
    definition = next(line for line in lines if "Definition" in line)
    assert definition.index("Definition") > term.index("Term")


def test_layout_table_is_flattened():
    page = render_html(
        "<table>"
        "<tr><td>1.</td><td><a href='/one'>First result</a></td></tr>"
        "<tr><td></td><td>first snippet</td></tr>"
        "<tr><td></td><td>one.test</td></tr>"
        "<tr><td>2.</td><td><a href='/two'>Second result</a></td></tr>"
        "<tr><td></td><td>second snippet</td></tr>"
        "</table>"
    )
    body = page_text(page)
    assert "┌" not in body and "│" not in body
    assert "1. First result" in body
    assert "2. Second result" in body
    assert "   first snippet" in body
    assert [link.url for link in page.links] == [
        "https://example.test/one",
        "https://example.test/two",
    ]


def test_single_column_table_is_flattened():
    page = render_html("<table><tr><td>just text</td></tr></table>")
    body = page_text(page)
    assert "just text" in body
    assert "┌" not in body


def test_empty_table_is_skipped():
    page = render_html("<table><tr><td> </td><td></td></tr></table><p>after</p>")
    assert "┌" not in page_text(page)
    assert "after" in page_text(page)
