from __future__ import annotations

import os
import threading
import time

import pytest

from chromabrowser.keys import KeyReader


@pytest.fixture
def reader():
    r, w = os.pipe()
    kr = KeyReader(r)
    yield kr, w
    os.close(r)
    os.close(w)


def send(fd: int, data: bytes) -> None:
    os.write(fd, data)


def later(fd: int, data: bytes, delay: float = 0.05) -> None:
    def go() -> None:
        time.sleep(delay)
        try:
            os.write(fd, data)
        except OSError:
            pass

    threading.Thread(target=go, daemon=True).start()


def test_printable_ascii(reader):
    kr, w = reader
    send(w, b"a")
    assert kr.read(1) == "a"


def test_utf8_character(reader):
    kr, w = reader
    send(w, "я".encode())
    assert kr.read(1) == "я"


def test_control_keys(reader):
    kr, w = reader
    send(w, b"\r")
    assert kr.read(1) == "enter"
    send(w, b"\t")
    assert kr.read(1) == "tab"
    send(w, b"\x7f")
    assert kr.read(1) == "backspace"
    send(w, b"\x08")
    assert kr.read(1) == "ctrl-h"
    send(w, b"\x03")
    assert kr.read(1) == "ctrl-c"
    send(w, b"\x0c")
    assert kr.read(1) == "ctrl-l"


def test_arrow_and_tilde_sequences(reader):
    kr, w = reader
    send(w, b"\x1b[A")
    assert kr.read(1) == "up"
    send(w, b"\x1b[3~")
    assert kr.read(1) == "delete"
    send(w, b"\x1b[5~")
    assert kr.read(1) == "pageup"
    send(w, b"\x1b[6~")
    assert kr.read(1) == "pagedown"
    send(w, b"\x1b[H")
    assert kr.read(1) == "home"
    send(w, b"\x1b[Z")
    assert kr.read(1) == "backtab"


def test_modified_arrows(reader):
    kr, w = reader
    send(w, b"\x1b[1;3D")
    assert kr.read(1) == "alt-left"
    send(w, b"\x1b[1;3C")
    assert kr.read(1) == "alt-right"
    send(w, b"\x1b[1;5A")
    assert kr.read(1) == "ctrl-up"


def test_alt_character(reader):
    kr, w = reader
    send(w, b"\x1b" + b"x")  # alt-x
    assert kr.read(1) == "alt-x"


def test_mouse_sgr(reader):
    kr, w = reader
    send(w, b"\x1b[<64;10;5M")
    key = kr.read(1)
    assert key == ("mouse", 64, 9, 4, True)
    send(w, b"\x1b[<0;3;7m")
    assert kr.read(1) == ("mouse", 0, 2, 6, False)


def test_bracketed_paste(reader):
    kr, w = reader
    send(w, b"\x1b[200~hello\nworld\x1b[201~")
    assert kr.read(1) == ("text", "hello\nworld")


def test_lone_escape_flushes(reader):
    kr, w = reader
    later(w, b"\x1b")
    start = time.monotonic()
    assert kr.read(None) == "esc"
    assert time.monotonic() - start < 1.0


def test_partial_csi_is_flushed_as_escape(reader):
    kr, w = reader
    later(w, b"\x1b[")
    assert kr.read(None) == "esc"


def test_timeout_returns_none(reader):
    kr, _w = reader
    start = time.monotonic()
    assert kr.read(0.05) is None
    assert time.monotonic() - start < 0.5


def test_wake_fd_interrupts_blocking_read():
    r, w = os.pipe()
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    kr = KeyReader(r)
    kr.wake_fds = [wake_r]
    try:
        def wake() -> None:
            time.sleep(0.05)
            os.write(wake_w, b"x")

        threading.Thread(target=wake, daemon=True).start()
        start = time.monotonic()
        assert kr.read(None) is None
        assert time.monotonic() - start < 1.0
    finally:
        for fd in (r, w, wake_r, wake_w):
            os.close(fd)
