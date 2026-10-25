"""All screen drawing for ChromaBrowser.

The canvas is redrawn from scratch every frame and diffed against the previous
snapshot, so scrolling and spinners are cheap even on slow terminals.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from . import theme
from .canvas import Canvas
from .htmlrender import RenderedPage
from .theme import Style
from .util import clip_middle, clip_text, split_by_width, text_width

SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class UIMixin:
    # ------------------------------------------------------------- widgets ---
    def _footer_left(self) -> tuple[str, Style]:
        now = time.monotonic()
        if self.message and now < self.message_until:
            return self.message, self.message_style
        if self.hints is not None:
            return "hint: type a label · Esc cancels", Style(fg=theme.ACCENT3, bold=True)
        if self.overlay is not None:
            return (
                f"{self.overlay.title.lower()} · {len(self.overlay.items)} entries",
                Style(fg=theme.ACCENT2, bold=True),
            )
        if self.job is not None:
            return (
                f"{SPINNER_FRAMES[int(time.monotonic() * 12) % len(SPINNER_FRAMES)]} "
                f"loading {clip_middle(self.job.url, max(10, self.cols - 20))}",
                Style(fg=theme.ACCENT2),
            )
        if self.focus_link is not None:
            url = self._link_url(self.focus_link) or ""
            return "→ " + clip_middle(url, max(10, self.cols - 30)), Style(fg=theme.LINK)
        if self.page is not None:
            title = self.page.title or self.page.url
            return title, Style(fg=theme.FG, bold=True)
        return "ChromaBrowser", Style(fg=theme.DIM)

    def _footer_right(self) -> str:
        bits: list[str] = []
        page = self.page
        if page is not None and page.view is not None:
            if self.max_scroll <= 0:
                bits.append("all")
            else:
                pct = int(round(100 * page.scroll / self.max_scroll))
                bits.append(f"{min(100, max(0, pct))}%")
        if self.search is not None and self.search.matches:
            bits.append(
                f'"{clip_text(self.search.query, 18)}" '
                f"{self.search.index + 1}/{len(self.search.matches)}"
            )
        if self.overlay is not None:
            bits.append("Enter open · d delete · Esc")
        elif self.hints is not None:
            bits.append("Esc cancel")
        elif self.focus_link is not None:
            bits.append("Enter open · Esc")
        elif self.job is not None:
            bits.append("Esc cancel")
        else:
            if self.cols >= 96:
                bits.append("f links · / find · ? help · q quit")
            elif self.cols >= 64:
                bits.append("/ find · ? help · q quit")
            else:
                bits.append("? help")
        return "  ".join(bits)

    # ---------------------------------------------------------------- frame ---
    def draw(self) -> None:
        cols, rows = self._size()
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self.prev = None
            self.on_resize()
        self.canvas = Canvas(self.cols, self.rows)
        self.visible_segments = []
        self.prompt_cursor = (1, self.rows - 1)

        self._draw_header()
        self._draw_content()
        if self.prompt is not None:
            self._draw_prompt()
        else:
            self._draw_footer()
        if self.overlay is not None:
            self._draw_overlay()
        if self.hints is not None:
            self._draw_hint_labels()

        stream = self.canvas.render(self.prev, self.truecolor)
        self.prev = self.canvas.snapshot()
        if self.prompt is not None:
            cx, cy = self.prompt_cursor
            cx = max(0, min(cx, self.cols - 1))
            stream += f"\x1b[?25h\x1b[{cy + 1};{cx + 1}H"
        else:
            stream += "\x1b[?25l"
        self._write(stream)

    def _draw_header(self) -> None:
        c = self.canvas
        hdr = Style(bg=theme.HEADER_BG)
        c.fill_rect(0, 0, self.cols, 1, hdr)
        x = 1
        if self.cols >= 44:
            for index, ch in enumerate("CHROMA"):
                c.put(x, 0, ch, Style(fg=theme.gradient_rgb(index / 5), bg=theme.HEADER_BG, bold=True))
                x += 1
            x = c.put(x, 0, " Browser", Style(fg=theme.FG, bg=theme.HEADER_BG))
            x = c.put(x, 0, "  ", hdr)
            x = c.put(x, 0, "▏", Style(fg=theme.FAINT, bg=theme.HEADER_BG))
            x = c.put(x, 0, " ", hdr)
        else:
            x = c.put(x, 0, "◈ ", Style(fg=theme.ACCENT2, bg=theme.HEADER_BG, bold=True))

        url = self.job.url if self.job is not None else (self.page.url if self.page else "")
        right = self._header_right()
        right_w = text_width(right)
        avail = self.cols - x - right_w - 3
        if avail >= 4 and url:
            c.put(x, 0, clip_middle(url, avail), Style(fg=theme.FG, bg=theme.HEADER_BG))
        if right and right_w <= self.cols - 4:
            c.put(self.cols - right_w - 2, 0, right, Style(fg=theme.DIM, bg=theme.HEADER_BG))

        for index in range(self.cols):
            c.put(
                index,
                1,
                "─",
                Style(fg=theme.gradient_rgb(index / max(1, self.cols - 1)), bg=theme.BG),
            )

    def _header_right(self) -> str:
        bits: list[str] = []
        if self.job is not None:
            frame = SPINNER_FRAMES[int(time.monotonic() * 12) % len(SPINNER_FRAMES)]
            bits.append(f"{frame} loading")
        if self.page is not None:
            if self.page.status and self.page.status >= 400:
                bits.append(f"HTTP {self.page.status}")
            elif self.page.kind not in ("about", "error", "image"):
                host = urlparse(self.page.url).hostname or ""
                if host and self.job is None:
                    bits.append(host)
            if self.page.show_source:
                bits.append("source")
            if not self.images_on:
                bits.append("images off")
            if abs(self.zoom - 1.0) > 0.01:
                bits.append(f"images {self.zoom * 100:.0f}%")
        text = " · ".join(bits)
        return clip_text(text, max(6, self.cols // 2))

    def _draw_content(self) -> None:
        c = self.canvas
        top = 2
        height = max(1, self.rows - 3)
        self.content_top = top
        self.content_h = height
        page = self.page
        if page is None or page.view is None:
            self.max_scroll = 0
            label = "Loading…" if self.job is not None else "ChromaBrowser"
            self._draw_centered(label, top, height, Style(fg=theme.ACCENT2, bold=True))
            return

        rendered: RenderedPage = page.view
        lines = rendered.lines
        max_scroll = max(0, len(lines) - height)
        page.scroll = max(0, min(page.scroll, max_scroll))
        self.max_scroll = max_scroll
        limit = self.cols - 2
        for offset in range(height):
            index = page.scroll + offset
            if index >= len(lines):
                break
            y = top + offset
            line = lines[index]
            if line.image is not None:
                c.put_cells(1, y, line.image, max_width=limit)
                continue
            x = 1
            for run in line.runs:
                if not run.text:
                    continue
                if x > limit:
                    break
                text = run.text
                total_w = text_width(text)
                clipped = text
                if x + total_w > limit:
                    clipped = split_by_width(text, limit - x)[0]
                    total_w = text_width(clipped)
                if not clipped:
                    break
                c.put(x, y, clipped, run.style)
                if run.link is not None and clipped.strip():
                    self.visible_segments.append(
                        (y, x, min(limit, x + max(0, total_w - 1)), run.link)
                    )
                x += total_w
                if total_w < text_width(text):
                    break

        # Scrollbar
        if max_scroll > 0 and self.cols > 4:
            track = height
            thumb_h = max(2, int(round(track * track / max(1, len(lines)))))
            thumb_h = min(track, thumb_h)
            pos = int(round((track - thumb_h) * page.scroll / max_scroll))
            color = Style(fg=theme.tint(theme.ACCENT2, 0.15), bg=theme.BG)
            for k in range(thumb_h):
                c.put(self.cols - 1, top + pos + k, "▐", color)

        # Search highlights
        if self.search is not None and self.search.matches:
            for index, (line_index, x0, x1) in enumerate(self.search.matches):
                if not (page.scroll <= line_index < page.scroll + height):
                    continue
                y = top + line_index - page.scroll
                current = index == self.search.index
                c.paint_span(
                    1 + x0,
                    y,
                    max(1, x1 - x0),
                    fg=theme.BG if current else theme.SEARCH_FG,
                    bg=theme.ACCENT3 if current else theme.SEARCH_BG,
                    bold=current,
                )

        # Focused link
        if self.focus_link is not None:
            for (y, x0, x1, link) in self.visible_segments:
                if link == self.focus_link:
                    c.paint_span(
                        x0,
                        y,
                        max(1, x1 - x0 + 1),
                        fg=theme.LINK_ACTIVE,
                        bg=theme.SELECT_BG,
                        bold=True,
                    )

    def _draw_hint_labels(self) -> None:
        if self.hints is None:
            return
        c = self.canvas
        top = self.content_top
        limit = self.cols - 2
        for entry in self.hints.entries:
            if self.hints.prefix and not entry.label.startswith(self.hints.prefix):
                continue
            y = top + entry.line - (self.page.scroll if self.page else 0)
            if not (top <= y < top + self.content_h):
                continue
            x = 1 + entry.x
            if x >= limit:
                continue
            label = clip_text(entry.label, limit - x)
            c.put(x, y, label, Style(fg=theme.HINT_FG, bg=theme.HINT_BG, bold=True))

    def _draw_footer(self) -> None:
        if self.rows < 2:
            return
        row = self.rows - 1
        c = self.canvas
        c.fill_rect(0, row, self.cols, 1, Style(bg=theme.STATUS_BG))
        left, lstyle = self._footer_left()
        right = self._footer_right()
        rw = text_width(right)
        avail = max(0, self.cols - rw - 4)
        c.put(1, row, clip_text(left, avail), lstyle.merge(bg=theme.STATUS_BG))
        if right:
            c.put(
                max(1, self.cols - rw - 2),
                row,
                right,
                Style(fg=theme.STATUS_FG, bg=theme.STATUS_BG),
            )

    def _draw_prompt(self) -> None:
        if self.prompt is None or self.rows < 2:
            return
        row = self.rows - 1
        c = self.canvas
        bg = theme.BG_ALT
        c.fill_rect(0, row, self.cols, 1, Style(bg=bg))
        label = self.prompt.label
        c.put(1, row, label, Style(fg=theme.ACCENT2, bold=True, bg=bg))
        x0 = 1 + text_width(label)
        avail = max(1, self.cols - x0 - 2)
        text = self.prompt.text
        cursor = max(0, min(self.prompt.cursor, len(text)))
        start = 0
        while start < cursor and text_width(text[start:cursor]) > avail - 1:
            start += 1
        visible = split_by_width(text[start:], avail)[0]
        c.put(x0, row, visible, Style(fg=theme.FG, bg=bg))
        cursor_rel = text_width(text[start:cursor])
        cx = x0 + min(cursor_rel, max(0, avail - 1))
        ch = c.grid[row][cx][0] if 0 <= cx < self.cols else " "
        c.put(cx, row, ch or " ", Style(fg=bg, bg=theme.FG))
        self.prompt_cursor = (cx, row)

    def _draw_overlay(self) -> None:
        overlay = self.overlay
        if overlay is None:
            return
        c = self.canvas
        width = min(self.cols - 4, 100)
        if width < 20:
            return
        wanted = len(overlay.items) * 2 + 4
        height = min(self.content_h, max(6, wanted))
        x = max(0, (self.cols - width) // 2)
        y = self.content_top + max(0, (self.content_h - height) // 2)
        fill = Style(bg=theme.BG_ALT)
        c.box(
            x,
            y,
            width,
            height,
            Style(fg=theme.tint(theme.ACCENT2, 0.45), bg=theme.BG_ALT),
            title=f"{overlay.title} · {len(overlay.items)}",
            title_style=Style(fg=theme.ACCENT2, bold=True, bg=theme.BG_ALT),
            fill=fill,
        )
        visible = max(1, (height - 3) // 2)
        if overlay.index < overlay.offset:
            overlay.offset = overlay.index
        if overlay.index >= overlay.offset + visible:
            overlay.offset = overlay.index - visible + 1
        for row_index in range(visible):
            index = overlay.offset + row_index
            if index >= len(overlay.items):
                break
            item = overlay.items[index]
            line_y = y + 1 + row_index * 2
            if line_y + 1 >= y + height - 1:
                break
            selected = index == overlay.index
            if selected:
                c.fill_rect(x + 1, line_y, width - 2, 1, Style(bg=theme.SELECT_BG))
            title = ("▌ " if selected else "  ") + (item.get("title") or item["url"])
            c.put(
                x + 2,
                line_y,
                clip_text(title, width - 4),
                Style(
                    fg=theme.FG if selected else theme.STATUS_FG,
                    bg=theme.SELECT_BG if selected else theme.BG_ALT,
                    bold=selected,
                ),
            )
            c.put(
                x + 2,
                line_y + 1,
                clip_text("  " + item["url"], width - 4),
                Style(fg=theme.DIM, bg=theme.BG_ALT),
            )
        c.put(
            x + 2,
            y + height - 2,
            clip_text("Enter open · d delete · Esc close", width - 4),
            Style(fg=theme.FAINT, bg=theme.BG_ALT),
        )

    def _draw_centered(self, text: str, top: int, height: int, style: Style) -> None:
        y = top + max(0, (height - 1) // 2)
        x = max(1, (self.cols - text_width(text)) // 2)
        self.canvas.put(x, y, text, style)
