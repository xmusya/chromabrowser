"""End-to-end test: run the real TUI in a pseudo-terminal and inspect the screen."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

pyte = pytest.importorskip("pyte")

ROOT = Path(__file__).resolve().parents[1]


class TUI:
    def __init__(self, args: list[str], cols: int = 100, rows: int = 30):
        self.cols, self.rows = cols, rows
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = dict(os.environ)
        env.update(
            TERM="xterm-256color",
            COLORTERM="truecolor",
            LINES=str(rows),
            COLUMNS=str(cols),
            XDG_CONFIG_HOME="/tmp/chroma-pytest-pty/config",
            XDG_DATA_HOME="/tmp/chroma-pytest-pty/data",
            XDG_CACHE_HOME="/tmp/chroma-pytest-pty/cache",
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "chromabrowser", *args],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=ROOT,
            close_fds=True,
        )
        os.close(slave)
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.log = b""

    def pump(self, seconds: float = 0.2) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.master], [], [], 0.03)
            if not ready:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            self.log += data
            self.stream.feed(data.decode("utf-8", "replace"))

    def wait_for(self, needle: str, timeout: float = 10.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.1)
            if needle in self.text():
                # Let the rest of the current frame arrive before returning.
                self.pump(0.15)
                return True
        return False

    def send(self, data: str) -> None:
        os.write(self.master, data.encode())
        self.pump(0.2)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        try:
            os.kill(self.proc.pid, signal.SIGWINCH)
        except ProcessLookupError:
            pass
        self.screen.resize(rows, cols)
        self.pump(0.8)

    def line(self, y: int) -> str:
        return self.screen.display[y].rstrip()

    def text(self) -> str:
        return "\n".join(self.screen.display)

    def quit(self) -> int:
        if self.proc.poll() is None:
            try:
                os.write(self.master, b"q")
            except OSError:
                pass
        try:
            code = self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = -9
        else:
            self.pump(0.1)
        finally:
            try:
                os.close(self.master)
            except OSError:
                pass
        return code


@pytest.fixture
def demo(tmp_path):
    page = tmp_path / "demo.html"
    page.write_text(
        "<html><head><title>PTY demo</title></head><body>"
        "<h1>PTY page</h1>"
        "<p>Text with a <a href='two.html'>first link</a> here.</p>"
        "<ul><li>alpha</li><li>beta</li></ul>"
        + "".join(
            f"<p>Filler paragraph {i} long enough to need several screenfuls.</p>"
            for i in range(40)
        )
        + "</body></html>",
        encoding="utf-8",
    )
    (tmp_path / "two.html").write_text(
        "<html><head><title>Second page</title></head><body><h1>Second</h1></body></html>",
        encoding="utf-8",
    )
    return page


def test_tui_loads_and_quits(demo):
    tui = TUI([demo.as_uri()])
    try:
        assert tui.wait_for("PTY page"), tui.text()
        assert "CHROMA" in tui.line(0)
        assert tui.line(0).count("file://") >= 1
        footer = tui.line(tui.rows - 1)
        assert "PTY demo" in footer
        assert "q quit" in footer or "help" in footer
    finally:
        assert tui.quit() == 0


def test_tui_scrolling(demo):
    tui = TUI([demo.as_uri()])
    try:
        assert tui.wait_for("PTY page")
        tui.send("G")
        assert "100%" in tui.line(tui.rows - 1)
        tui.send("g")
        assert "0%" in tui.line(tui.rows - 1)
    finally:
        tui.quit()


def test_tui_hint_follows_link(demo):
    tui = TUI([demo.as_uri()])
    try:
        assert tui.wait_for("PTY page")
        tui.send("f")
        assert "hint:" in tui.line(tui.rows - 1)
        tui.send("a")
        assert tui.wait_for("Second page"), tui.text()
        assert "Second" in tui.text()
    finally:
        tui.quit()


def test_tui_search(demo):
    tui = TUI([demo.as_uri()])
    try:
        assert tui.wait_for("PTY page")
        tui.send("/")
        tui.send("alpha")
        tui.send("\r")
        footer = tui.line(tui.rows - 1)
        assert "1/1" in footer
        tui.send("n")
        assert "1/1" in tui.line(tui.rows - 1)
    finally:
        tui.quit()


def test_tui_resize_redraws(demo):
    tui = TUI([demo.as_uri()], 100, 30)
    try:
        assert tui.wait_for("PTY page")
        tui.resize(70, 20)
        # The wakeup pipe makes the TUI react to SIGWINCH without a keypress.
        header = tui.line(0)
        assert header.strip(), "header is empty after resize"
        assert len(header) <= 70
        footer = tui.line(19)
        assert footer.strip(), "footer is empty after resize"
        assert "PTY demo" in footer
        assert all(len(tui.line(y)) <= 70 for y in range(20))
    finally:
        tui.quit()


def test_tui_source_and_images_toggle(demo):
    tui = TUI([demo.as_uri()])
    try:
        assert tui.wait_for("PTY page")
        tui.send("v")
        assert tui.wait_for("Viewing HTML source"), tui.text()
        assert "<h1>" in tui.text()
        tui.send("v")
        assert tui.wait_for("Back to the rendered page")
        tui.send("i")
        assert tui.wait_for("images off")
        tui.send("i")
        assert tui.wait_for("Images on")
    finally:
        tui.quit()


def test_tui_mouse_click_follows_link(demo):
    tui = TUI([demo.as_uri()])
    try:
        assert tui.wait_for("PTY page")
        row = next(y for y in range(tui.rows) if "first link" in tui.line(y))
        col = tui.line(row).index("first link")
        tui.send(f"\x1b[<0;{col + 1};{row + 1}M")
        tui.send(f"\x1b[<0;{col + 1};{row + 1}m")
        assert tui.wait_for("Second page"), tui.text()
    finally:
        tui.quit()


def test_tui_256_color_mode(demo):
    tui = TUI(["--color", "256", demo.as_uri()])
    try:
        assert tui.wait_for("PTY page")
        assert b"38;5;" in tui.log
        assert b"38;2;" not in tui.log
    finally:
        tui.quit()


def test_tui_open_prompt_and_back():
    tui = TUI(["about:start"])
    try:
        assert tui.wait_for("Quick start")
        tui.send("o")
        tui.send("about:help")
        tui.send("\r")
        assert tui.wait_for("Everything you can press"), tui.text()
        tui.send("\x7f")  # Backspace -> back
        assert tui.wait_for("Quick start"), tui.text()
    finally:
        tui.quit()


def test_tui_tab_focus_and_enter(demo):
    tui = TUI([demo.as_uri()])
    try:
        assert tui.wait_for("PTY page")
        tui.send("\t")
        assert "two.html" in tui.line(tui.rows - 1), tui.line(tui.rows - 1)
        tui.send("\r")
        assert tui.wait_for("Second page"), tui.text()
    finally:
        tui.quit()
