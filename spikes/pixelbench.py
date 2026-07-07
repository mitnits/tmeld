#!/usr/bin/env python3
"""pixelbench: how fast can this terminal move pixels? (kitty graphics)

Animates a full-screen scrolling gradient via the kitty graphics
protocol and reports achieved FPS, wire throughput, and per-frame
latency (kitty ACKs every transmit, so command -> decoded round trip
is measurable). Run it locally and over SSH to see exactly what the
network + pty + escape parser cost — and with --transfer s locally to
see why shared memory changes everything (the escape then carries a
~60-byte name instead of megabytes of base64).

Usage, from a kitty-protocol terminal (kitty, Ghostty, WezTerm):
    python3 spikes/pixelbench.py                 # 10s, full window, t=d
    python3 spikes/pixelbench.py --scale 0.5     # quarter of the pixels
    python3 spikes/pixelbench.py --no-compress   # honest worst case
    python3 spikes/pixelbench.py --transfer s    # LOCAL ONLY: shm
    python3 spikes/pixelbench.py --transfer t    # LOCAL ONLY: temp file

iTerm2 does not implement the kitty graphics protocol; use Ghostty or
kitty on macOS.
"""

import argparse
import base64
import fcntl
import os
import select
import statistics
import struct
import sys
import termios
import time
import tty
import zlib

ESC = "\x1b"
IMAGE_ID = 4711
GRADIENT_EXTRA_ROWS = 240  # scroll depth of the precomputed sheet


def winsize():
    raw = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
    rows, cols, xpix, ypix = struct.unpack("HHHH", raw)
    return rows, cols, xpix, ypix


def build_gradient_sheet(width_px, height_px):
    """A tall RGBA sheet; each animation frame is a window into it, so
    per-frame cost is a slice, not per-pixel Python work."""
    total_rows = height_px + GRADIENT_EXTRA_ROWS
    sheet = bytearray(total_rows * width_px * 4)
    row_template = bytearray(width_px * 4)
    for y in range(total_rows):
        r = (y * 3) % 256
        g = (y * 7) % 256
        b = 255 - (y * 5) % 256
        for x in range(0, width_px * 4, 4):
            # horizontal shading so columns differ too
            shade = (x // 4) % 64
            row_template[x] = min(255, r + shade)
            row_template[x + 1] = g
            row_template[x + 2] = max(0, b - shade)
            row_template[x + 3] = 255
        base = y * width_px * 4
        sheet[base:base + width_px * 4] = row_template
    return sheet


def frame_view(sheet, frame_index, width_px, height_px):
    offset = (frame_index % GRADIENT_EXTRA_ROWS) * width_px * 4
    return memoryview(sheet)[offset:offset + width_px * height_px * 4]


def kitty_chunks(control, payload_b64):
    pieces = [payload_b64[i:i + 4096] for i in range(0, len(payload_b64), 4096)] or [b""]
    out = []
    for i, piece in enumerate(pieces):
        keys = control if i == 0 else []
        keys = keys + [f"m={0 if i == len(pieces) - 1 else 1}"]
        out.append(f"{ESC}_G{','.join(keys)};".encode() + piece + f"{ESC}\\".encode())
    return b"".join(out)


def wait_for(fd, needle, timeout):
    """Read raw tty input until needle appears; returns elapsed or None."""
    start = time.perf_counter()
    buf = b""
    while time.perf_counter() - start < timeout:
        ready, _, _ = select.select([fd], [], [], timeout / 10)
        if not ready:
            continue
        buf += os.read(fd, 4096)
        if needle in buf:
            return time.perf_counter() - start
    return None


def measure_text_rtt(fd, samples=5):
    """Baseline escape round-trip via cursor position report."""
    rtts = []
    for _ in range(samples):
        start = time.perf_counter()
        sys.stdout.write(f"{ESC}[6n")
        sys.stdout.flush()
        if wait_for(fd, b"R", 2.0) is not None:
            rtts.append(time.perf_counter() - start)
    return statistics.median(rtts) if rtts else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--scale", type=float, default=1.0,
                        help="fraction of window pixels to use (0.1-1.0)")
    parser.add_argument("--transfer", choices=("d", "t", "s"), default="d",
                        help="d=escape stream (works over SSH), "
                             "t=temp file, s=POSIX shm (both local-only)")
    parser.add_argument("--no-compress", action="store_true",
                        help="skip zlib (o=z); shows uncompressed wire cost")
    parser.add_argument("--force", action="store_true",
                        help="skip the kitty-protocol probe")
    args = parser.parse_args()

    if not sys.stdout.isatty():
        sys.exit("pixelbench needs a tty")

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    if not args.force:
        from tmeld.term import probe_graphics

        if probe_graphics() != "kitty":
            sys.exit(
                "this terminal did not answer the kitty-graphics probe "
                "(iTerm2 won't; use kitty/Ghostty/WezTerm, or --force)"
            )

    rows, cols, xpix, ypix = winsize()
    if not (xpix and ypix):
        cell_w, cell_h = 8, 16
        xpix, ypix = cols * cell_w, rows * cell_h
        print("warning: tty reports no pixel size; assuming 8x16 cells")
    width = max(64, int(xpix * args.scale)) & ~1
    height = max(64, int((ypix - 2 * (ypix // rows)) * args.scale)) & ~1

    frame_bytes = width * height * 4
    print(f"window: {cols}x{rows} cells, {xpix}x{ypix} px")
    print(f"frame:  {width}x{height} px = {frame_bytes / 1e6:.1f} MB RGBA "
          f"({'zlib' if not args.no_compress else 'raw'}, t={args.transfer})")
    print("building gradient sheet...", flush=True)
    sheet = build_gradient_sheet(width, height)

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    shm = None
    frames = 0
    sent_bytes = 0
    write_times = []
    ack_times = []
    try:
        tty.setraw(fd)
        out = sys.stdout
        text_rtt = measure_text_rtt(fd)
        out.write(f"{ESC}[?25l")  # hide cursor

        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            view = frame_view(sheet, frames, width, height)
            control = [
                "a=T", "f=32", f"i={IMAGE_ID}", "p=1", "q=0", "C=1",
                f"s={width}", f"v={height}",
            ]
            if args.transfer == "d":
                data = zlib.compress(view, 1) if not args.no_compress else view
                if not args.no_compress:
                    control.append("o=z")
                payload = base64.standard_b64encode(data)
            elif args.transfer == "t":
                path = f"/dev/shm/pixelbench-{os.getpid()}-{frames}.rgba"
                with open(path, "wb") as f:
                    f.write(view)
                control.append("t=t")
                payload = base64.standard_b64encode(path.encode())
            else:  # s
                from multiprocessing import shared_memory

                shm = shared_memory.SharedMemory(
                    create=True, size=frame_bytes
                )
                shm.buf[:frame_bytes] = view
                control.append("t=s")
                payload = base64.standard_b64encode(shm.name.encode())

            blob = f"{ESC}[1;1H".encode() + kitty_chunks(control, payload)
            start = time.perf_counter()
            os.write(out.fileno(), blob)
            write_times.append(time.perf_counter() - start)
            ack = wait_for(fd, f"i={IMAGE_ID}".encode(), 5.0)
            if ack is None:
                raise RuntimeError(
                    "no ACK from terminal (protocol unsupported here?)"
                )
            ack_times.append(write_times[-1] + ack)
            sent_bytes += len(blob)
            frames += 1
            if args.transfer == "s" and shm is not None:
                shm.close()  # terminal unlinks it after reading
                shm = None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        sys.stdout.write(f"{ESC}_Ga=d,d=I,i={IMAGE_ID},q=2{ESC}\\{ESC}[?25h\n")
        sys.stdout.flush()

    elapsed = args.seconds
    print(f"\nframes: {frames} in {elapsed:.1f}s  ->  {frames / elapsed:.1f} fps")
    print(f"wire:   {sent_bytes / 1e6:.1f} MB total, "
          f"{sent_bytes / elapsed / 1e6:.1f} MB/s, "
          f"{sent_bytes / max(frames, 1) / 1e6:.2f} MB/frame")
    if text_rtt is not None:
        print(f"escape round-trip (baseline): {text_rtt * 1000:.1f} ms")
    if ack_times:
        print(f"frame latency (write+decode ACK): "
              f"median {statistics.median(ack_times) * 1000:.1f} ms, "
              f"p95 {sorted(ack_times)[int(len(ack_times) * 0.95) - 1] * 1000:.1f} ms")
    if write_times:
        print(f"pty write (backpressure): "
              f"median {statistics.median(write_times) * 1000:.1f} ms")


if __name__ == "__main__":
    main()
