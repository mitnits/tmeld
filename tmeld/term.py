"""Terminal capability probe (PLAN.md D3): run BEFORE Textual owns
the tty.

Graphics detection sends a 1x1 kitty-graphics query followed by DA1
(primary device attributes). Terminals answer in order, so once the
DA1 reply arrives we know everything: a kitty "OK" APC means the kitty
protocol; otherwise DA1 parameter 4 advertises sixel. Terminals that
support neither still answer DA1, so the timeout only bites on very
odd ttys.

cell_pixel_size() reads the pixel dimensions from TIOCGWINSZ (set by
iTerm2, kitty, WezTerm, VTE, ...) to size the linkmap bitmap; 8x16 is
the fallback guess.
"""

import os
import re
import select
import sys
import time
from typing import Tuple

PROBE_TIMEOUT = 0.3
FALLBACK_CELL = (8, 16)

# 1x1 RGB query (t=d direct, dummy payload) + DA1
_KITTY_QUERY = "\x1b_Gi=4242,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\"
_DA1 = "\x1b[c"
_DA1_RE = re.compile(r"\x1b\[\?([\d;]*)c")
_KITTY_OK_RE = re.compile(r"\x1b_Gi=4242;OK\x1b\\")


def probe_graphics(timeout: float = PROBE_TIMEOUT) -> str:
    """Return 'kitty', 'sixel' or 'none' for the controlling terminal."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return "none"
    try:
        import termios
        import tty
    except ImportError:  # not a POSIX tty (Windows)
        return "none"

    fd = sys.stdin.fileno()
    try:
        old_attrs = termios.tcgetattr(fd)
    except termios.error:
        return "none"
    buf = ""
    try:
        tty.setraw(fd)
        sys.stdout.write(_KITTY_QUERY + _DA1)
        sys.stdout.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([fd], [], [], max(0.0, remaining))
            if not ready:
                break
            data = os.read(fd, 512)
            if not data:
                break
            buf += data.decode("ascii", "replace")
            if _DA1_RE.search(buf):
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)

    if _KITTY_OK_RE.search(buf):
        return "kitty"
    da1 = _DA1_RE.search(buf)
    if da1 and "4" in da1.group(1).split(";"):
        return "sixel"
    return "none"


def cell_pixel_size() -> Tuple[int, int]:
    """(width, height) of one terminal cell in pixels."""
    try:
        import fcntl
        import struct
        import termios

        raw = fcntl.ioctl(
            sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8
        )
        rows, cols, xpix, ypix = struct.unpack("HHHH", raw)
        if rows and cols and xpix and ypix:
            return (xpix // cols, ypix // rows)
    except (OSError, ImportError, ValueError):
        pass
    return FALLBACK_CELL
