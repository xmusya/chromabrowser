"""Raw terminal key decoding (arrows, modifiers, mouse, paste)."""

from __future__ import annotations

import os
import select
import time

CSI = "\x1b["

# Keys whose escape sequence is ``ESC [ <number> ~``
_TILDE_KEYS = {
    1: "home",
    2: "insert",
    3: "delete",
    4: "end",
    5: "pageup",
    6: "pagedown",
    7: "home",
    8: "end",
    11: "f1",
    12: "f2",
    13: "f3",
    14: "f4",
    15: "f5",
    17: "f6",
    18: "f7",
    19: "f8",
    20: "f9",
    21: "f10",
    23: "f11",
    24: "f12",
    29: "menu",
}

_LETTER_KEYS = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "Z": "backtab",
    "P": "f1",
    "Q": "f2",
    "R": "f3",
    "S": "f4",
}

_CTRL_NAMES = {
    0x00: "ctrl-space",
    0x03: "ctrl-c",
    0x04: "ctrl-d",
    0x05: "ctrl-e",
    0x06: "ctrl-f",
    0x07: "ctrl-g",
    0x08: "ctrl-h",
    0x0A: "ctrl-j",
    0x0B: "ctrl-k",
    0x0C: "ctrl-l",
    0x0E: "ctrl-n",
    0x0F: "ctrl-o",
    0x10: "ctrl-p",
    0x11: "ctrl-q",
    0x12: "ctrl-r",
    0x13: "ctrl-s",
    0x14: "ctrl-t",
    0x15: "ctrl-u",
    0x16: "ctrl-v",
    0x17: "ctrl-w",
    0x18: "ctrl-x",
    0x19: "ctrl-y",
    0x1A: "ctrl-z",
    0x1C: "ctrl-backslash",
    0x1D: "ctrl-]",
    0x1E: "ctrl-^",
    0x1F: "ctrl-_",
}

_MODIFIERS = {
    2: ("shift",),
    3: ("alt",),
    4: ("shift", "alt"),
    5: ("ctrl",),
    6: ("ctrl", "shift"),
    7: ("ctrl", "alt"),
    8: ("ctrl", "alt", "shift"),
}


def _utf8_len(byte: int) -> int:
    if byte < 0x80:
        return 1
    if byte >> 5 == 0b110:
        return 2
    if byte >> 4 == 0b1110:
        return 3
    if byte >> 3 == 0b11110:
        return 4
    return 1


class KeyReader:
    """Decode keypresses from a raw-mode file descriptor."""

    def __init__(self, fd: int | None = None):
        self.fd = fd if fd is not None else 0
        self.buf = b""
        # Extra descriptors (signal wakeup pipes) that interrupt a blocking
        # select without producing a key.
        self.wake_fds: list[int] = []

    # ------------------------------------------------------------------ api ---
    def read(self, timeout: float | None = None) -> object | None:
        """Return a key name, a printable character, a tuple, or None on timeout.

        Tuples:
          ("text", str)         - pasted chunk / composed text
          ("mouse", b, x, y, pressed)
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.buf:
                parsed = self._parse()
                if parsed is not None:
                    key, consumed = parsed
                    self.buf = self.buf[consumed:]
                    return key
            if deadline is not None:
                wait = max(0.0, deadline - time.monotonic())
                if wait == 0 and not self.buf:
                    return None
                if self.buf:
                    wait = min(wait, 0.1)
            else:
                # A lone ESC / partial escape sequence must not block forever.
                wait = 0.1 if self.buf else None
            try:
                watched = [self.fd, *self.wake_fds]
                ready, _, _ = select.select(watched, [], [], wait)
            except InterruptedError:
                return None
            if self.wake_fds:
                woke = [fd for fd in self.wake_fds if fd in ready]
                if woke:
                    for fd in woke:
                        try:
                            os.read(fd, 4096)
                        except (BlockingIOError, InterruptedError):
                            pass
                    if self.fd not in ready:
                        return None
            if not ready:
                if self.buf:
                    return self._flush_buffer()
                return None
            try:
                data = os.read(self.fd, 4096)
            except (InterruptedError, BlockingIOError):
                continue
            if not data:
                raise EOFError("terminal closed")
            self.buf += data

    def _flush_buffer(self) -> object:
        """Give up waiting for the rest of a partial escape sequence."""
        data, self.buf = self.buf, b""
        if data == b"\x1b":
            return "esc"
        if data.startswith(b"\x1b"):
            # Keep the remaining bytes so the next call can decode them.
            self.buf = data[1:]
            return "esc"
        return self._decode_text(data)

    def _decode_text(self, data: bytes) -> object:
        text = data.decode("utf-8", "replace")
        if len(text) == 1:
            return text
        return ("text", text)

    # -------------------------------------------------------------- parsing ---
    def _parse(self) -> tuple[object, int] | None:
        buf = self.buf
        if not buf:
            return None
        b0 = buf[0]

        if b0 == 0x1B:
            if len(buf) == 1:
                return None
            b1 = buf[1]
            if b1 == ord("["):
                return self._parse_csi()
            if b1 == ord("O"):
                if len(buf) < 3:
                    return None
                key = _LETTER_KEYS.get(chr(buf[2]))
                return (key, 3) if key else ("esc", 1)
            if b1 in (0x1B,):
                return ("esc", 1)
            # Alt + character
            n = _utf8_len(b1)
            if len(buf) < 1 + n:
                return None
            ch = buf[1 : 1 + n].decode("utf-8", "replace")
            return (f"alt-{ch}", 1 + n)

        if b0 == 0x00 or b0 < 0x20 or b0 == 0x7F:
            return (self._control(b0), 1)

        n = _utf8_len(b0)
        if len(buf) < n:
            return None
        ch = buf[:n].decode("utf-8", "replace")
        if ch == "\x1b":
            return ("esc", n)
        if ch in ("\r", "\n"):
            return ("enter", n)
        if ch == "\t":
            return ("tab", n)
        return (ch, n)

    def _control(self, byte: int) -> str:
        if byte == 0x7F:
            return "backspace"
        if byte == 0x0D:
            return "enter"
        if byte == 0x09:
            return "tab"
        return _CTRL_NAMES.get(byte, f"ctrl-{chr(byte + 64).lower()}" if 1 <= byte <= 26 else "unknown")

    def _parse_csi(self) -> tuple[object, int] | None:
        buf = self.buf
        # Need at least ESC [ X
        if len(buf) < 3:
            return None

        if buf.startswith(b"\x1b[200~"):
            end = buf.find(b"\x1b[201~", 6)
            if end == -1:
                return None
            text = buf[6:end].decode("utf-8", "replace")
            return (("text", text), end + 6)
        if buf.startswith(b"\x1b[201~"):
            return ("unknown", 6)

        if buf[2:3] == b"<":
            return self._parse_mouse()

        # Plain letters: ESC [ A / ESC O A handled elsewhere
        if 0x40 <= buf[2] <= 0x7E and not (0x30 <= buf[2] <= 0x3B):
            key = _LETTER_KEYS.get(chr(buf[2]))
            if key is None:
                return ("unknown", 3)
            return (key, 3)

        # Parameterized sequences: ESC [ 5 ~ / ESC [ 1 ; 5 A / ESC [ 1 ; 2 R
        i = 2
        params = b""
        while i < len(buf) and not (0x40 <= buf[i] <= 0x7E):
            params += bytes([buf[i]])
            i += 1
        if i >= len(buf):
            return None
        final = chr(buf[i])
        consumed = i + 1
        try:
            parts = [int(p) if p else 0 for p in params.replace(b"<", b"").split(b";")]
        except ValueError:
            return ("unknown", consumed)

        mods: tuple[str, ...] = ()
        if len(parts) >= 2 and parts[1] in _MODIFIERS:
            mods = _MODIFIERS[parts[1]]
        base = parts[0] if parts else 0

        if final == "~":
            key = _TILDE_KEYS.get(base)
            if key is None:
                return ("unknown", consumed)
        elif final in _LETTER_KEYS:
            key = _LETTER_KEYS[final]
        elif final == "u" and base:
            # Kitty keyboard protocol: ESC [ code ; mods u
            try:
                key = chr(base)
            except ValueError:
                return ("unknown", consumed)
        else:
            return ("unknown", consumed)

        if mods:
            # ctrl+shift+letter etc. keep useful combinations
            if "shift" in mods and key in ("tab",):
                return ("backtab", consumed)
            if "alt" in mods and key in ("left", "right", "up", "down", "home", "end"):
                return (f"alt-{key}", consumed)
            if "ctrl" in mods:
                return (f"ctrl-{key}", consumed)
        return (key, consumed)

    def _parse_mouse(self) -> tuple[object, int] | None:
        buf = self.buf
        end = buf.find(b"M", 3)
        end_m = buf.find(b"m", 3)
        if end == -1 or (end_m != -1 and end_m < end):
            end = end_m
            pressed = False
        else:
            pressed = True
        if end == -1:
            return None
        try:
            body = buf[3:end].decode("ascii")
            bstr, xstr, ystr = body.split(";")
            b, x, y = int(bstr), int(xstr), int(ystr)
        except (ValueError, UnicodeDecodeError):
            return ("unknown", end + 1)
        return (("mouse", b, x - 1, y - 1, pressed), end + 1)
