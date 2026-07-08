"""Pixel linkmap tests: geometry, rasterizer coverage, encoders."""

import base64
import zlib
from types import SimpleNamespace

from tmeld.linkmap import (
    Connector,
    connectors_for_chunks,
    kitty_delete_escape,
    kitty_place_escape,
    render_connectors,
    sixel_escape,
)
from tmeld.palette import MELD_BASE


def chunk(tag, start_a, end_a, start_b, end_b):
    return SimpleNamespace(
        tag=tag, start_a=start_a, end_a=end_a, start_b=start_b, end_b=end_b
    )


def pixel(rgba, width, x, y):
    i = (y * width + x) * 4
    return tuple(rgba[i:i + 4])


def test_connectors_for_chunks_geometry():
    chunks = [chunk("replace", 2, 4, 1, 2), chunk("insert", 5, 5, 3, 4)]
    conns = connectors_for_chunks(
        chunks, scroll_f_px=16, scroll_t_px=0, line_height_px=16,
        current_chunk_starts=frozenset({(2, 1)}),
    )
    first = conns[0]
    assert first.tag == "replace" and first.emphasized
    assert first.f0 == 2 * 16 - 16 == 16
    assert first.f1 == 4 * 16 - 16 - 1 == 47  # last pixel of previous line
    assert first.t0 == 16 and first.t1 == 31
    second = conns[1]
    # Empty left side: no -1 nudge (f1 stays == f0), but the band is centred
    # half an insert-marker below the line boundary so the connector terminates
    # on exactly the rows the pane paints its marker on -- see
    # test_insert_marker.test_connector_band_lands_on_the_markers_rows.
    from tmeld.linkmap import INSERT_MARKER_PX
    boundary = 5 * 16 - 16
    assert second.f0 == second.f1 == boundary + INSERT_MARKER_PX / 2
    assert not second.emphasized


def test_render_fills_center_with_chunk_fill():
    # flat connector: rows 8..24 on both sides -> horizontal band
    conn = Connector("insert", 8.0, 24.0, 8.0, 24.0)
    width, height = 20, 40
    rgba = render_connectors([conn], width, height, MELD_BASE)
    fill = MELD_BASE.chunk["insert"].fill
    r, g, b = (int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16))
    # solid interior pixel (away from the stroked edges)
    assert pixel(rgba, width, 10, 16) == (r, g, b, 255)
    # outside the band: fully transparent
    assert pixel(rgba, width, 10, 2)[3] == 0
    assert pixel(rgba, width, 10, 37)[3] == 0


def test_render_strokes_edges_with_line_color():
    conn = Connector("replace", 10.0, 20.0, 10.0, 20.0)
    width, height = 12, 32
    rgba = render_connectors([conn], width, height, MELD_BASE)
    line = MELD_BASE.chunk["replace"].line
    fill = MELD_BASE.chunk["replace"].fill
    lr = (int(line[1:3], 16), int(line[3:5], 16), int(line[5:7], 16))
    fr = (int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16))
    # rows 10..20 span the shape (±0.5 nudges): the boundary pixel rows
    # are crisp 1px line color, the interior pure fill
    assert pixel(rgba, width, 6, 9)[:3] == lr
    assert pixel(rgba, width, 6, 20)[:3] == lr
    for y in range(10, 20):
        assert pixel(rgba, width, 6, y)[:3] == fr
    assert pixel(rgba, width, 6, 8)[3] == 0


def test_render_slope_is_monotone():
    # left rows 0..8, right rows 24..32: edge must descend monotonically
    conn = Connector("replace", 0.0, 8.0, 24.0, 32.0)
    width, height = 24, 48
    rgba = render_connectors([conn], width, height, MELD_BASE)

    def top_edge_y(x):
        for y in range(height):
            if pixel(rgba, width, x, y)[3] > 0:
                return y
        return height

    edges = [top_edge_y(x) for x in range(width)]
    assert edges[0] < edges[-1]
    assert all(b >= a for a, b in zip(edges, edges[1:]))


def test_render_with_background_is_opaque():
    conn = Connector("insert", 4.0, 10.0, 4.0, 10.0)
    rgba = render_connectors([conn], 8, 16, MELD_BASE, background="#ffffff")
    assert all(rgba[i + 3] == 255 for i in range(0, len(rgba), 4))
    assert pixel(rgba, 8, 4, 0) == (255, 255, 255, 255)


def test_kitty_escape_roundtrip():
    conn = Connector("conflict", 2.0, 6.0, 2.0, 6.0)
    width, height = 6, 10
    rgba = render_connectors([conn], width, height, MELD_BASE)
    esc = kitty_place_escape(7, rgba, width, height)
    assert esc.startswith("\x1b_G")
    head, _, payload = esc.partition(";")
    assert "a=T" in head and "f=32" in head and "i=7" in head
    assert f"s={width}" in head and f"v={height}" in head
    # reassemble the base64 payload across chunks and round-trip it
    b64 = "".join(
        part.partition(";")[2]
        for part in esc.split("\x1b\\")
        if part.startswith("\x1b_G")
    )
    decoded = zlib.decompress(base64.standard_b64decode(b64))
    assert decoded == bytes(rgba)
    assert kitty_delete_escape(7) == "\x1b_Ga=d,d=I,i=7,q=2\x1b\\"


def test_sixel_escape_structure():
    conn = Connector("replace", 2.0, 8.0, 2.0, 8.0)
    width, height = 10, 12
    rgba = render_connectors(
        [conn], width, height, MELD_BASE, background="#ffffff"
    )
    esc = sixel_escape(rgba, width, height)
    assert esc.startswith('\x1bP0;0;8q"1;1;10;12')
    assert esc.endswith("\x1b\\")
    assert "#0;2;" in esc  # palette definition
    assert esc.count("-") == 2  # 12 rows -> two 6-row bands


def test_chunk_map_keeps_tiny_chunks_visible():
    from tmeld.linkmap import render_chunk_map

    # one single-line chunk in a 5000-line file, mapped to 32x720px:
    # the cell renderer would lose it; the pixel map must show it
    chunks = [("conflict", 2500, 2501)]
    width, height = 16, 720
    rgba = render_chunk_map(chunks, 5000, width, height, MELD_BASE)
    line = MELD_BASE.chunk["conflict"].line
    lr = (int(line[1:3], 16), int(line[3:5], 16), int(line[5:7], 16))
    target_y = round(2500.5 * height / 5000)
    found = False
    for y in range(target_y - 2, target_y + 3):
        px = pixel(rgba, width, 8, y)
        if all(abs(c - e) < 90 for c, e in zip(px[:3], lr)):
            found = True
    assert found, "single-line chunk not visible in pixel map"


def test_chunk_map_viewport_is_translucent():
    from tmeld.linkmap import render_chunk_map

    rgba = render_chunk_map(
        [], 100, 8, 200, MELD_BASE, viewport=(0.0, 50.0)
    )
    inside = pixel(rgba, 8, 4, 50)
    outside = pixel(rgba, 8, 4, 150)
    assert outside[:3] == (255, 255, 255)  # meld-base page bg
    assert inside != outside  # shaded
    assert inside[0] > 120  # but not opaque overlay color (#646464)
