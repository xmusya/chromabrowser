"""HTML -> styled terminal lines.

This is a layout engine of the "good enough" kind: it understands headings,
paragraphs, lists, quotes, code, tables, forms, links and images, but it does
not try to implement CSS layout or JavaScript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Sequence
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from . import theme
from .theme import RGB, Style
from .util import sanitize, split_by_width, text_width

# ------------------------------------------------------------------ models ---


@dataclass(frozen=True)
class Run:
    text: str = ""
    style: Style = theme.BASE
    link: int | None = None
    image: str | None = None
    alt: str = ""


@dataclass
class Line:
    runs: list[Run] = field(default_factory=list)
    image: list[tuple[str, Style]] | None = None

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)

    @property
    def is_blank(self) -> bool:
        return not self.runs and not self.image


@dataclass
class LinkInfo:
    id: int
    url: str
    text: str = ""


@dataclass
class LinkSegment:
    y: int
    x0: int
    x1: int
    link: int


@dataclass
class RenderedPage:
    lines: list[Line]
    links: list[LinkInfo] = field(default_factory=list)
    segments: list[LinkSegment] = field(default_factory=list)
    title: str = ""
    base_url: str = ""
    width: int = 80
    plain: bool = False

    def line_text(self, index: int) -> str:
        if 0 <= index < len(self.lines):
            return self.lines[index].text
        return ""


@dataclass(frozen=True)
class Ctx:
    indent: int = 0
    prefix: tuple[Run, ...] = ()
    first: tuple[Run, ...] = ()
    rest: tuple[Run, ...] = ()
    compact: bool = False
    align: str = "left"

    def derive(self, **kwargs) -> "Ctx":
        return replace(self, **kwargs)


# ------------------------------------------------------------------- tags ---

SKIP_TAGS = {
    "script",
    "style",
    "head",
    "meta",
    "link",
    "title",
    "base",
    "template",
    "noscript",
    "svg",
    "canvas",
    "param",
    "source",
    "track",
    "area",
    "map",
    "wbr",
}

BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "body",
    "center",
    "dd",
    "details",
    "dialog",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hgroup",
    "hr",
    "html",
    "iframe",
    "li",
    "main",
    "menu",
    "nav",
    "ol",
    "p",
    "picture",
    "pre",
    "search",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
    "video",
    "audio",
    "object",
    "embed",
}

HEADINGS = {f"h{i}": i for i in range(1, 7)}

_NAMED_COLORS = {
    "black": (0, 0, 0),
    "silver": (192, 192, 192),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "white": (255, 255, 255),
    "maroon": (128, 0, 0),
    "red": (255, 0, 0),
    "purple": (128, 0, 128),
    "fuchsia": (255, 0, 255),
    "magenta": (255, 0, 255),
    "green": (0, 128, 0),
    "lime": (0, 255, 0),
    "olive": (128, 128, 0),
    "yellow": (255, 255, 0),
    "navy": (0, 0, 128),
    "blue": (0, 0, 255),
    "teal": (0, 128, 128),
    "aqua": (0, 255, 255),
    "cyan": (0, 255, 255),
    "orange": (255, 165, 0),
    "pink": (255, 192, 203),
    "brown": (165, 42, 42),
    "gold": (255, 215, 0),
    "tomato": (255, 99, 71),
    "crimson": (220, 20, 60),
    "indigo": (75, 0, 130),
    "violet": (238, 130, 238),
    "salmon": (250, 128, 114),
    "khaki": (240, 230, 140),
    "beige": (245, 245, 220),
}

_LINK_STYLE = Style(fg=theme.LINK, underline=True)
_BUTTON_STYLE = Style(fg=theme.ACCENT3, bold=True)
_INPUT_STYLE = Style(fg=theme.CODE_FG, bg=theme.CODE_BG)
_IMG_PLACEHOLDER = Style(fg=theme.DIM, italic=True)
_IMG_ERROR = Style(fg=theme.RED, italic=True)
_CAPTION = Style(fg=theme.DIM, italic=True)
_BORDER = Style(fg=theme.BORDER)
_RULE = Style(fg=theme.RULE)


def parse_css_color(value: str | None) -> RGB | None:
    if not value:
        return None
    value = value.strip().lower()
    if value.startswith("#"):
        h = value[1:]
        try:
            if len(h) in (3, 4):
                return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
            if len(h) in (6, 8):
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            return None
    m = re.match(r"rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?)", value)
    if m:
        parts = []
        for token in m.groups():
            if token.endswith("%"):
                parts.append(round(float(token[:-1]) * 2.55))
            else:
                parts.append(round(float(token)))
        if len(parts) == 3:
            return tuple(max(0, min(255, p)) for p in parts)  # type: ignore[return-value]
    if value in _NAMED_COLORS:
        return _NAMED_COLORS[value]
    return None


def _luminance(rgb: RGB) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


# -------------------------------------------------------------------- wrap ---


class _Wrapper:
    """Word-wraps styled runs into lines with per-line prefixes."""

    def __init__(
        self,
        width: int,
        prefix_first: Sequence[Run],
        prefix_rest: Sequence[Run],
    ):
        self.width = max(1, width)
        self.prefix_first = list(prefix_first)
        self.prefix_rest = list(prefix_rest)
        self.lines: list[list[Run]] = []
        self.cur: list[Run] = []
        self.curw = 0
        self.first = True
        self.content = False
        self.pending: tuple[Style, int | None] | None = None
        self._start()

    @property
    def prefix(self) -> list[Run]:
        return self.prefix_first if self.first else self.prefix_rest

    def _start(self) -> None:
        self.cur = list(self.prefix)
        self.curw = sum(text_width(r.text) for r in self.prefix)
        self.content = False
        self.pending = None

    def _trim(self) -> None:
        base = len(self.prefix)
        while len(self.cur) > base:
            last = self.cur[-1]
            if not last.text.strip() and last.text:
                self.cur.pop()
                continue
            stripped = last.text.rstrip()
            if stripped != last.text:
                self.cur[-1] = replace(last, text=stripped)
            break

    def _end(self) -> None:
        self._trim()
        if self.content:
            self.lines.append(self.cur)
        self.first = False
        self._start()

    def newline(self) -> None:
        self._end()

    def line_break(self) -> None:
        if self.content:
            self._end()
        else:
            # A second consecutive <br> creates an intentional blank line.
            if self.lines and self.lines[-1]:
                self.lines.append([])
            self.pending = None

    def _append(self, text: str, style: Style, link: int | None) -> None:
        if not text:
            return
        if self.cur and self.cur[-1].style == style and self.cur[-1].link == link:
            prev = self.cur[-1]
            self.cur[-1] = replace(prev, text=prev.text + text)
        else:
            self.cur.append(Run(text, style, link))
        self.curw += text_width(text)
        if text.strip():
            self.content = True

    def add_word(self, word: str, style: Style, link: int | None) -> None:
        if self.content and self.pending is not None:
            pstyle, plink = self.pending
            if self.curw + 1 + text_width(word) <= self.width:
                self._append(" ", pstyle, plink)
            else:
                self.newline()
        self.pending = None
        self._append_word(word, style, link)

    def _append_word(self, word: str, style: Style, link: int | None) -> None:
        while word:
            avail = self.width - self.curw
            if avail <= 0:
                self.newline()
                avail = self.width
            if text_width(word) <= avail:
                self._append(word, style, link)
                return
            chunk = split_by_width(word, avail)[0]
            if not chunk:
                self.newline()
                continue
            self._append(chunk, style, link)
            word = word[len(chunk) :]
            if word:
                self.newline()

    def add_run(self, run: Run) -> None:
        if not run.text:
            return
        parts = run.text.split("\n")
        for index, part in enumerate(parts):
            if index:
                self.line_break()
            if not part:
                continue
            for token in re.findall(r"\s+|\S+", part):
                if token.isspace():
                    self.pending = (run.style, run.link)
                else:
                    self.add_word(token, run.style, run.link)

    def finish(self) -> list[list[Run]]:
        self._end()
        return self.lines


def wrap_runs(
    runs: Sequence[Run],
    width: int,
    prefix_first: Sequence[Run] = (),
    prefix_rest: Sequence[Run] | None = None,
) -> list[list[Run]]:
    if prefix_rest is None:
        prefix_rest = prefix_first
    wrapper = _Wrapper(width, prefix_first, prefix_rest)
    for run in runs:
        wrapper.add_run(run)
    return wrapper.finish()


def collect_segments(lines: Sequence[Line]) -> list[LinkSegment]:
    segments: list[LinkSegment] = []
    for y, line in enumerate(lines):
        if line.image:
            continue
        x = 0
        for run in line.runs:
            w = text_width(run.text)
            if run.link is not None and run.text.strip():
                segments.append(LinkSegment(y, x, max(x, x + w - 1), run.link))
            x += w
    return segments


# ---------------------------------------------------------------- renderer ---


class HTMLRenderer:
    def __init__(
        self,
        width: int,
        base_url: str,
        *,
        images: bool = True,
        image_loader=None,
        zoom: float = 1.0,
        loader_referer: str | None = None,
    ):
        self.width = max(20, width)
        self.base_url = base_url
        self.images = images
        self.image_loader = image_loader
        self.zoom = zoom
        self.referer = loader_referer or base_url
        self.lines: list[Line] = []
        self.links: list[LinkInfo] = []
        self._link_ids: dict[str, int] = {}
        self._link_text: dict[int, list[str]] = {}
        self.image_count = 0
        self.image_errors = 0

    # -------------------------------------------------------------- public ---
    def render(self, soup: BeautifulSoup) -> RenderedPage:
        title = ""
        if soup.title and soup.title.string:
            title = sanitize(str(soup.title.string)).strip()
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = sanitize(h1.get_text(" ", strip=True))[:160]
        base_tag = soup.find("base", href=True)
        if base_tag is not None:
            href = str(base_tag.get("href") or "").strip()
            if href:
                self.base_url = urljoin(self.base_url, href)
        body = soup.body or soup

        ctx = Ctx()
        if isinstance(body, Tag):
            self.render_mixed(body, ctx, theme.BASE)
        self._trim()
        if not self.lines:
            self.lines.append(Line([Run("(this page is empty)", theme.DIM_STYLE)]))

        self._finalize_link_text()
        segments = collect_segments(self.lines)
        return RenderedPage(
            lines=self.lines,
            links=self.links,
            segments=segments,
            title=title,
            base_url=self.base_url,
            width=self.width,
        )

    def render_runs(self, runs: Sequence[Run], page_title: str = "") -> RenderedPage:
        """Render pre-built runs (used by internal pages)."""
        wrapped = wrap_runs(runs, self.width)
        for line in wrapped:
            self.lines.append(Line(line))
        self._finalize_link_text()
        return RenderedPage(
            lines=self.lines,
            links=self.links,
            segments=collect_segments(self.lines),
            title=page_title,
            base_url=self.base_url,
            width=self.width,
        )

    # --------------------------------------------------------------- flow ---
    def _prefix(self, ctx: Ctx, first: bool) -> list[Run]:
        runs: list[Run] = []
        if ctx.indent:
            runs.append(Run(" " * ctx.indent, theme.BASE))
        runs.extend(ctx.prefix)
        runs.extend(ctx.first if first else ctx.rest)
        return runs

    def _avail(self, ctx: Ctx) -> int:
        pref = self._prefix(ctx, False)
        used = sum(text_width(r.text) for r in pref)
        return max(4, self.width - used)

    def emit(self, runs: Sequence[Run], ctx: Ctx) -> None:
        cleaned = [r for r in runs if r.text or r.image]
        if not any(r.image or r.text.strip() for r in cleaned):
            return
        wrapped = wrap_runs(
            cleaned,
            self.width,
            self._prefix(ctx, True),
            self._prefix(ctx, False),
        )
        for line in wrapped:
            self.lines.append(Line(line))

    def emit_paragraph(self, runs: Sequence[Run], ctx: Ctx) -> None:
        if not runs:
            return
        if any(r.image for r in runs):
            buffer: list[Run] = []
            for run in runs:
                if run.image:
                    if buffer:
                        self.emit(buffer, ctx)
                        buffer = []
                    self.render_image(run.image, run.alt, ctx)
                else:
                    buffer.append(run)
            if buffer:
                self.emit(buffer, ctx)
            return
        if not any(r.text.strip() for r in runs):
            return
        self.emit(runs, ctx)

    def ensure_blank(self) -> None:
        if self.lines and not self.lines[-1].is_blank:
            self.lines.append(Line([]))

    def blank(self) -> None:
        if self.lines and not self.lines[-1].is_blank:
            self.lines.append(Line([]))

    def _trim(self) -> None:
        while self.lines and self.lines[-1].is_blank:
            self.lines.pop()

    def rule(self, ctx: Ctx, width: int | None = None, char: str = "─", style: Style | None = None) -> None:
        length = width if width is not None else min(self._avail(ctx), 80)
        length = max(2, length)
        self.emit([Run(char * length, style or _RULE)], ctx)

    # --------------------------------------------------------------- links ---
    def link_id(self, href: str) -> int:
        if href in self._link_ids:
            return self._link_ids[href]
        link_id = len(self.links)
        self._link_ids[href] = link_id
        self.links.append(LinkInfo(link_id, href))
        return link_id

    def _resolve_href(self, href: str | None) -> str | None:
        if not href:
            return None
        href = href.strip()
        low = href.lower()
        if low.startswith(("javascript:", "data:", "mailto:", "tel:", "blob:", "about:blank")):
            return None
        if href.startswith("#"):
            return None
        return urljoin(self.base_url, href)

    def _finalize_link_text(self) -> None:
        texts: dict[int, list[str]] = {link.id: [] for link in self.links}
        for line in self.lines:
            for run in line.runs:
                if run.link is not None and run.text.strip():
                    texts.setdefault(run.link, []).append(run.text)
        for link in self.links:
            text = sanitize(" ".join(texts.get(link.id, []))).strip()
            link.text = text[:160]

    # ------------------------------------------------------------- inline ---
    @staticmethod
    def inline_text(raw: str) -> str:
        """Collapse HTML whitespace; real line breaks come only from <br>."""
        return sanitize(raw).replace("\n", " ")

    def inline_style(self, el: Tag, style: Style) -> Style:
        name = el.name
        st = style
        if name in ("strong", "b"):
            st = st.merge(bold=True)
        elif name in ("em", "i", "cite", "var", "dfn"):
            st = st.merge(italic=True)
        elif name in ("u", "ins"):
            st = st.merge(underline=True)
        elif name in ("s", "del", "strike"):
            st = st.merge(strike=True)
        elif name in ("code", "samp", "tt", "kbd"):
            st = st.merge(fg=theme.CODE_FG, bg=theme.CODE_BG)
        elif name == "mark":
            st = st.merge(fg=theme.BG, bg=theme.ACCENT3)
        elif name in ("small", "sub", "sup"):
            st = st.merge(fg=theme.DIM)

        color = parse_css_color(el.get("color"))
        if color is not None and _luminance(color) > 45:
            st = st.merge(fg=color)

        css = el.get("style", "")
        if css:
            m = re.search(r"(?<!-)\bcolor\s*:\s*([^;]+)", css, re.I)
            if m:
                col = parse_css_color(m.group(1))
                if col is not None and _luminance(col) > 45:
                    st = st.merge(fg=col)
            m = re.search(r"background(?:-color)?\s*:\s*([^;]+)", css, re.I)
            if m:
                col = parse_css_color(m.group(1))
                if col is not None and col not in ((0, 0, 0), (255, 255, 255)) and _luminance(col) < 140:
                    st = st.merge(bg=col)
            if re.search(r"font-weight\s*:\s*(?:bold|[6-9]00)", css, re.I):
                st = st.merge(bold=True)
            if re.search(r"font-style\s*:\s*italic", css, re.I):
                st = st.merge(italic=True)
            if "underline" in css.lower():
                st = st.merge(underline=True)
            if "line-through" in css.lower():
                st = st.merge(strike=True)
        return st

    def collect_inline(
        self,
        node: Tag,
        style: Style,
        ctx: Ctx,
        link: int | None = None,
    ) -> list[Run]:
        out: list[Run] = []
        for child in node.children:
            if isinstance(child, NavigableString):
                text = self.inline_text(str(child))
                if text:
                    out.append(Run(text, style, link))
                continue
            if isinstance(child, Tag):
                out.extend(self.element_runs(child, style, ctx, link))
        return out

    def element_runs(
        self,
        el: Tag,
        style: Style,
        ctx: Ctx,
        link: int | None = None,
    ) -> list[Run]:
        """Runs for a single inline element (link, image, control, styled span)."""
        name = el.name
        if name in SKIP_TAGS:
            return []
        if name == "br":
            return [Run("\n", style, link)]
        if name == "img":
            src = el.get("src") or el.get("data-src") or el.get("data-original")
            if not src and el.get("srcset"):
                src = el.get("srcset", "").split()[0].split(",")[0]
            alt = sanitize(el.get("alt", "")).strip()
            if src:
                return [Run("", style, link, image=urljoin(self.base_url, src.strip()), alt=alt)]
            return [Run(f"[{alt}]", _IMG_PLACEHOLDER, link)] if alt else []
        if name == "a":
            href = self._resolve_href(el.get("href"))
            if href:
                lid = self.link_id(href)
                return self.collect_inline(el, _LINK_STYLE, ctx, lid)
            return self.collect_inline(el, style, ctx, link)
        if name in ("input", "textarea", "select"):
            return self.control_runs(el, style)
        if name == "button":
            inner = self.collect_inline(el, _BUTTON_STYLE, ctx, link)
            label = sanitize("".join(r.text for r in inner)).strip() or "Button"
            return [Run(f"[ {label} ]", _BUTTON_STYLE, link)]
        if name == "q":
            inner = self.collect_inline(el, style, ctx, link)
            return [Run("«", theme.DIM_STYLE, link), *inner, Run("»", theme.DIM_STYLE, link)]
        if name == "picture":
            return self.collect_inline(el, style, ctx, link)
        if name == "iframe":
            src = el.get("src", "")
            return [Run(f"[iframe {src}]" if src else "[iframe]", _IMG_PLACEHOLDER, link)]
        if name in BLOCK_TAGS:
            # Invalid/nested block inside inline context: keep the text.
            return self.collect_inline(el, style, ctx, link)
        st = self.inline_style(el, style)
        return self.collect_inline(el, st, ctx, link)

    def control_runs(self, el: Tag, style: Style) -> list[Run]:
        name = el.name
        if name == "input":
            itype = (el.get("type") or "text").lower()
            if itype == "hidden":
                return []
            if itype in ("submit", "button", "reset"):
                label = el.get("value") or itype.title()
                return [Run(f"[ {label} ]", _BUTTON_STYLE)]
            if itype in ("checkbox", "radio"):
                mark = "x" if el.has_attr("checked") else " "
                return [Run(f"[{mark}] ", theme.DIM_STYLE)]
            value = sanitize(el.get("value") or "").strip()
            placeholder = sanitize(el.get("placeholder") or "").strip()
            label = value or placeholder
            return [Run(f"[ {label} ]" if label else "[          ]", _INPUT_STYLE)]
        if name == "textarea":
            text = sanitize(el.get_text(" ", strip=True))[:60]
            return [Run(f"[ {text} ]" if text else "[          ]", _INPUT_STYLE)]
        if name == "select":
            selected = ""
            for opt in el.find_all("option"):
                if opt.has_attr("selected"):
                    selected = sanitize(opt.get_text(" ", strip=True))
                    break
            if not selected:
                first = el.find("option")
                selected = sanitize(first.get_text(" ", strip=True)) if first else ""
            return [Run(f"[ {selected or 'select'} ▾ ]", _INPUT_STYLE)]
        return []

    # -------------------------------------------------------------- blocks ---
    def render_mixed(self, el: Tag, ctx: Ctx, style: Style) -> None:
        runs: list[Run] = []
        for child in el.children:
            if isinstance(child, NavigableString):
                text = self.inline_text(str(child))
                if text:
                    runs.append(Run(text, style))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in SKIP_TAGS:
                continue
            if name in BLOCK_TAGS:
                self.emit_paragraph(runs, ctx)
                runs = []
                self.render_block(child, ctx, style)
            else:
                runs.extend(self.element_runs(child, style, ctx))
        self.emit_paragraph(runs, ctx)

    def render_block(self, el: Tag, ctx: Ctx, style: Style) -> None:
        name = el.name
        if name in SKIP_TAGS:
            return
        if name in HEADINGS:
            self.heading(el, ctx, HEADINGS[name])
        elif name == "p":
            self.paragraph(el, ctx, style)
        elif name in ("ul", "ol", "menu"):
            self.render_list(el, ctx, style, ordered=(name == "ol"))
        elif name == "li":
            self.render_item(el, ctx, style, "• ")
        elif name == "blockquote":
            self.blockquote(el, ctx, style)
        elif name == "pre":
            self.preformatted(el, ctx)
        elif name == "table":
            self.table(el, ctx, style)
        elif name == "hr":
            self.horizontal_rule(ctx)
        elif name == "img":
            src = el.get("src") or el.get("data-src")
            if src:
                self.render_image(
                    urljoin(self.base_url, src.strip()),
                    sanitize(el.get("alt", "")).strip(),
                    ctx,
                )
        elif name == "figure":
            self.ensure_blank()
            self.render_mixed(el, ctx, style)
            self.blank()
        elif name == "figcaption":
            self.ensure_blank()
            self.emit(self.collect_inline(el, _CAPTION, ctx), ctx)
        elif name == "dl":
            self.ensure_blank()
            self.render_mixed(el, ctx, style)
            self.blank()
        elif name == "dt":
            self.emit(self.collect_inline(el, style.merge(bold=True), ctx), ctx)
        elif name == "dd":
            dd_ctx = ctx.derive(indent=ctx.indent + 2)
            self.emit(self.collect_inline(el, style, dd_ctx), dd_ctx)
        elif name == "summary":
            st = style.merge(fg=theme.ACCENT2, bold=True)
            runs = [Run("▸ ", st)] + self.collect_inline(el, st, ctx)
            self.emit(runs, ctx)
        elif name == "details":
            self.ensure_blank()
            self.render_mixed(el, ctx, style)
            self.blank()
        elif name in ("iframe", "video", "audio", "object", "embed"):
            self.media_placeholder(el, ctx)
        elif name in ("td", "th", "tr", "thead", "tbody", "tfoot"):
            # Stray table parts outside a table.
            self.render_mixed(el, ctx, style)
        elif name in ("hgroup", "center", "div", "section", "article", "main", "aside",
                      "header", "footer", "nav", "form", "fieldset", "dialog", "search",
                      "body", "html"):
            if name in ("nav", "footer"):
                style = style.merge(fg=theme.DIM)
            self.render_mixed(el, ctx, style)
        else:
            self.render_mixed(el, ctx, style)

    # ---------------------------------------------------------- paragraphs ---
    def paragraph(self, el: Tag, ctx: Ctx, style: Style) -> None:
        runs = self.collect_inline(el, style, ctx)
        if not any(r.image or r.text.strip() for r in runs):
            return
        if not ctx.compact:
            self.ensure_blank()
        self.emit_paragraph(runs, ctx)
        if not ctx.compact:
            self.blank()

    def heading(self, el: Tag, ctx: Ctx, level: int) -> None:
        color = theme.HEADING_COLORS[(level - 1) % len(theme.HEADING_COLORS)]
        style = Style(fg=color, bold=True)
        runs = self.collect_inline(el, style, ctx)
        if not any(r.text.strip() or r.image for r in runs):
            return
        self.ensure_blank()
        if level <= 2:
            runs = [Run("▌ ", style)] + runs
        self.emit(runs, ctx)
        if level == 1:
            width = min(self._avail(ctx), 70)
            self.rule(ctx, width, "─", Style(fg=theme.tint(color, 0.45)))
        self.blank()

    def blockquote(self, el: Tag, ctx: Ctx, style: Style) -> None:
        self.ensure_blank()
        bar_style = Style(fg=theme.tint(theme.ACCENT2, 0.25))
        inner = ctx.derive(
            indent=ctx.indent + 2,
            prefix=ctx.prefix + (Run("▌ ", bar_style),),
            first=(),
            rest=(),
            compact=False,
        )
        self.render_mixed(el, inner, style.merge(fg=theme.QUOTE_FG))
        self.blank()

    def preformatted(self, el: Tag, ctx: Ctx) -> None:
        text = sanitize(el.get_text(), collapse=False).strip("\n")
        if not text:
            return
        self.ensure_blank()
        self.emit_pre(text, ctx)
        self.blank()

    def emit_pre(self, text: str, ctx: Ctx) -> None:
        style = theme.CODE
        avail_first = self._avail(ctx)
        pref_first = self._prefix(ctx, True)
        pref_rest = self._prefix(ctx, False)
        width = max(4, min(avail_first, self.width - sum(text_width(r.text) for r in pref_rest)))
        for pindex, physical in enumerate(text.split("\n")):
            physical = physical.expandtabs(4).rstrip()
            if not physical.strip():
                if self.lines and not self.lines[-1].is_blank:
                    self.lines.append(Line([]))
                continue
            chunks = split_by_width(physical, width)
            for cindex, chunk in enumerate(chunks):
                prefix = pref_first if (pindex == 0 and cindex == 0) else pref_rest
                self.lines.append(
                    Line([*prefix, Run(chunk, style)])
                )

    # --------------------------------------------------------------- lists ---
    def render_list(self, el: Tag, ctx: Ctx, style: Style, *, ordered: bool) -> None:
        items = [c for c in el.children if isinstance(c, Tag) and c.name == "li"]
        if not items:
            return
        if not ctx.compact:
            self.ensure_blank()
        start = 1
        if ordered:
            try:
                start = int(el.get("start") or 1)
            except (TypeError, ValueError):
                start = 1
        for index, li in enumerate(items):
            if ordered:
                marker = f"{start + index}. "
                mstyle = Style(fg=theme.ACCENT3, bold=True)
            else:
                marker = "• "
                mstyle = Style(fg=theme.tint(theme.ACCENT, 0.1), bold=True)
            self.render_item(li, ctx, style, marker, mstyle)
        if not ctx.compact:
            self.blank()

    def render_item(
        self,
        li: Tag,
        ctx: Ctx,
        style: Style,
        marker: str,
        marker_style: Style | None = None,
    ) -> None:
        mstyle = marker_style or Style(fg=theme.ACCENT, bold=True)
        marker_w = text_width(marker)
        hanging = ctx.derive(
            indent=ctx.indent + 2,
            first=(Run(marker, mstyle),),
            rest=(Run(" " * marker_w, theme.BASE),),
            compact=True,
        )
        plain = hanging.derive(first=(), rest=())
        runs: list[Run] = []
        for child in li.children:
            if isinstance(child, Tag) and child.name in BLOCK_TAGS and child.name not in ("p", "span", "a", "picture"):
                if runs:
                    self.emit_paragraph(runs, hanging)
                    runs = []
                    hanging = plain
                self.render_block(child, plain, style)
                hanging = plain
            elif isinstance(child, Tag):
                runs.extend(self.element_runs(child, style, hanging))
            elif isinstance(child, NavigableString):
                text = self.inline_text(str(child))
                if text:
                    runs.append(Run(text, style))
        if runs:
            self.emit_paragraph(runs, hanging)

    # -------------------------------------------------------------- tables ---
    def _runs_content(self, runs: Sequence[Run]) -> bool:
        return any(run.text.strip() or run.image for run in runs)

    def layout_table(self, rows: list[list[tuple[Tag, list[Run]]]], ctx: Ctx) -> None:
        """Render a layout table (used by old sites for page structure) as text."""
        started = False
        for row in rows:
            cells = [(index, runs) for index, (_, runs) in enumerate(row) if self._runs_content(runs)]
            if not cells:
                continue
            first_index = cells[0][0]
            if first_index == 0:
                if started:
                    self.ensure_blank()
                if len(cells) >= 2:
                    self.emit_paragraph([*cells[0][1], Run("  "), *cells[1][1]], ctx)
                    rest = cells[2:]
                else:
                    self.emit_paragraph(cells[0][1], ctx)
                    rest = []
                started = True
            else:
                rest = cells
            for _, runs in rest:
                self.emit_paragraph(runs, ctx.derive(indent=ctx.indent + 3))

    def table(self, el: Tag, ctx: Ctx, style: Style) -> None:
        rows = [tr for tr in el.find_all("tr") if tr.find_parent("table") is el]
        if not rows:
            return
        cells_rows: list[list[tuple[Tag, list[Run]]]] = []
        ncols = 0
        for tr in rows:
            cells = [c for c in tr.find_all(["td", "th"], recursive=False)]
            if not cells:
                continue
            ncols = max(ncols, len(cells))
            row: list[tuple[Tag, list[Run]]] = []
            for cell in cells:
                header = cell.name == "th"
                cstyle = style.merge(bold=True) if header else style
                runs = self.collect_inline(cell, cstyle, ctx)
                row.append((cell, runs))
            cells_rows.append(row)
        if not cells_rows or ncols == 0:
            return

        nonempty = [row for row in cells_rows if any(self._runs_content(runs) for _, runs in row)]
        if not nonempty:
            return

        self.ensure_blank()
        caption = el.find("caption", recursive=False)
        if caption is not None:
            label = sanitize(caption.get_text(" ", strip=True))
            if label:
                self.emit([Run(label, _CAPTION)], ctx)

        multi = sum(
            1
            for row in nonempty
            if sum(1 for _, runs in row if self._runs_content(runs)) >= 2
        )
        if ncols <= 1 or multi * 2 < len(nonempty):
            self.layout_table(nonempty, ctx)
            self.blank()
            return

        avail = self._avail(ctx)
        widths = [3] * ncols
        for row in cells_rows:
            for index, (_, runs) in enumerate(row):
                natural = 0
                for logical in self._split_runs(runs):
                    natural = max(natural, sum(text_width(r.text) for r in logical))
                widths[index] = max(widths[index], min(natural, 44))
        while sum(widths) + 3 * ncols + 1 > avail and max(widths) > 3:
            widths[widths.index(max(widths))] -= 1

        lead = [Run(" " * ctx.indent, theme.BASE), *ctx.prefix]

        def border(left: str, middle: str, right: str) -> None:
            parts = ["─" * (w + 2) for w in widths]
            self.lines.append(Line([*lead, Run(left + middle.join(parts) + right, _BORDER)]))

        def emit_row(row: list[tuple[Tag, list[Run]]]) -> None:
            rendered: list[list[list[Run]]] = []
            header = False
            for index in range(ncols):
                if index < len(row):
                    cell, runs = row[index]
                    header = header or cell.name == "th"
                    logicals = self._split_runs(runs)
                    lines: list[list[Run]] = []
                    for logical in logicals:
                        if not logical:
                            lines.append([])
                            continue
                        wrapped = wrap_runs(logical, widths[index])
                        if not wrapped:
                            lines.append([])
                        else:
                            lines.extend(wrapped)
                    rendered.append(lines)
                else:
                    rendered.append([[]])
            height = max((len(cell) for cell in rendered), default=1)
            for line_index in range(height):
                line_runs: list[Run] = [*lead, Run("│ ", _BORDER)]
                for index in range(ncols):
                    cell_lines = rendered[index]
                    content = cell_lines[line_index] if line_index < len(cell_lines) else []
                    content = [r for r in content if r.text]
                    text_w = sum(text_width(r.text) for r in content)
                    padding = max(0, widths[index] - text_w)
                    is_header = index < len(row) and row[index][0].name == "th"
                    if is_header and padding:
                        left = padding // 2
                        content = [Run(" " * left, theme.BASE), *content, Run(" " * (padding - left), theme.BASE)]
                    else:
                        content = [*content, Run(" " * padding, theme.BASE)]
                    line_runs.extend(content)
                    line_runs.append(Run(" │ " if index < ncols - 1 else " │", _BORDER))
                self.lines.append(Line(line_runs))

        border("┌", "┬", "┐")
        for row_index, row in enumerate(cells_rows):
            emit_row(row)
            is_header = any(cell.name == "th" for cell, _ in row)
            if row_index < len(cells_rows) - 1 and is_header:
                border("├", "┼", "┤")
        border("└", "┴", "┘")
        self.blank()

    def _split_runs(self, runs: Sequence[Run]) -> list[list[Run]]:
        logical: list[list[Run]] = [[]]
        for run in runs:
            parts = run.text.split("\n")
            for index, part in enumerate(parts):
                if index:
                    logical.append([])
                if part:
                    logical[-1].append(replace(run, text=part))
        return logical

    # -------------------------------------------------------------- images ---
    def render_image(self, url: str, alt: str, ctx: Ctx) -> None:
        avail = self._avail(ctx)
        label = alt or url.rsplit("/", 1)[-1].split("?")[0] or "image"
        if not self.images or self.image_loader is None:
            self.emit([Run(f"[image: {label}]", _IMG_PLACEHOLDER)], ctx)
            return
        self.image_count += 1
        block = self.image_loader.render(url, avail, zoom=self.zoom, referer=self.referer)
        if not block.ok:
            self.image_errors += 1
            self.emit([Run(f"[image: {label} — {block.error}]", _IMG_ERROR)], ctx)
            return
        lead = [(" ", theme.BASE)] * ctx.indent
        for run in ctx.prefix:
            lead.extend((ch, run.style) for ch in run.text)
        for row in block.rows:
            self.lines.append(Line([], image=[*lead, *row]))
        if alt:
            self.emit([Run(alt, _CAPTION)], ctx)

    # --------------------------------------------------------------- misc ---
    def horizontal_rule(self, ctx: Ctx) -> None:
        self.ensure_blank()
        self.rule(ctx)
        self.blank()

    def media_placeholder(self, el: Tag, ctx: Ctx) -> None:
        name = el.name
        src = el.get("src") or el.get("data")
        title = el.get("title") or el.get("aria-label") or ""
        label = f"[{name}]"
        if title:
            label += f" {sanitize(title)}"
        if src:
            label += f" {sanitize(src)}"
        self.emit([Run(label[:160], _IMG_PLACEHOLDER)], ctx)
        self.blank()
