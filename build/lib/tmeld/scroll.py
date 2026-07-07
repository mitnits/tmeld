"""Meld's synchronized-scrolling algorithm, in cell units.

Exact port of upstream meld/misc.py:calc_syncpoint and the interpolation
core of meld/filediff.py:_sync_vscroll (PARITY.md §4). GTK pixel
coordinates become line/cell floats: with soft wrap off, one document
line is one screen cell, so get_line_yrange collapses to identity.
"""

import math
from typing import Iterable, Tuple


def calc_syncpoint(value: float, page_size: float, upper: float) -> float:
    """Where in the viewport [0..1] the cross-pane sync point sits.

    Normally the viewport middle (0.5); scales linearly to 0.0 within the
    first half-screen of the document and to 1.0 within the last, so that
    documents of different lengths pin correctly at both ends.

    Args:
        value: scroll offset (top of viewport), in lines
        page_size: viewport height, in lines
        upper: total scrollable content height, in lines
    """
    half_a_screen = page_size / 2

    syncpoint = 0.0
    if not half_a_screen:
        return syncpoint

    # How far through the first half-screen our adjustment is
    first_scale = value / half_a_screen
    syncpoint += 0.5 * min(1, first_scale)
    # How far through the last half-screen our adjustment is
    bottom_val = upper - 1.5 * page_size
    last_scale = (value - bottom_val) / half_a_screen
    syncpoint += 0.5 * max(0, last_scale)
    return syncpoint


def interpolate_line(
    target_line: float,
    master_total: int,
    other_total: int,
    pair_chunks: Iterable[Tuple],
) -> float:
    """Map a fractional master-pane line to the other pane's line.

    Finds the chunk, or more commonly the space between chunks, that
    contains the target line, and interpolates within that interval.
    pair_chunks are DiffChunks oriented master->other (i.e. start_a/end_a
    in master-pane lines), as yielded by Differ.pair_changes(master, other).
    """
    mbegin, mend = 0, master_total
    obegin, oend = 0, other_total
    for chunk in pair_chunks:
        if chunk.start_a >= target_line:
            mend = chunk.start_a
            oend = chunk.start_b
            break
        elif chunk.end_a >= target_line:
            mbegin, mend = chunk.start_a, chunk.end_a
            obegin, oend = chunk.start_b, chunk.end_b
            break
        else:
            mbegin = chunk.end_a
            obegin = chunk.end_b

    fraction = (target_line - mbegin) / ((mend - mbegin) or 1)
    return obegin + fraction * (oend - obegin)


def scroll_offset_for_line(
    other_line: float, other_page: float, other_total: int, syncpoint: float
) -> int:
    """Scroll offset that places other_line at the viewport's syncpoint."""
    val = other_line - other_page * syncpoint
    max_scroll = max(0.0, other_total - other_page)
    val = min(max(val, 0.0), max_scroll)
    return math.floor(val)


def sync_scroll_target(
    master_value: float,
    master_page: float,
    master_total: int,
    other_page: float,
    other_total: int,
    pair_chunks: Iterable[Tuple],
) -> int:
    """Compute the other pane's scroll offset for a master pane position.

    Single-pair convenience over calc_syncpoint + interpolate_line; the
    3-way influence cascade in the app uses the pieces directly because
    re-mastering through the middle pane needs the fractional line.
    """
    syncpoint = calc_syncpoint(master_value, master_page, master_total)

    # Sync point in buffer coords; usually the middle of the screen,
    # except at the top and bottom of the document.
    target_line = master_value + master_page * syncpoint

    other_line = interpolate_line(
        target_line, master_total, other_total, pair_chunks
    )
    return scroll_offset_for_line(other_line, other_page, other_total, syncpoint)
