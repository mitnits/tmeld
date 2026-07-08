"""Meld's insert marker: the thin line on the side that gains no lines.

Upstream draws every chunk with a [top, 0, bottom, 0] border on a rect of
height max(1, y1 - y0) + 1 (sourceview.py do_snapshot), skipping the fill when
the chunk is empty -- so a zero-height chunk collapses into one thin line where
the other pane's text would land.

The TUI can only draw this in Tier 2, and only under kitty: sixel pixels become
cell content, so a marker over a text row would erase its glyphs.
"""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.linkmap import INSERT_MARKER_PX, _hex_rgb, render_insert_marker
from tmeld.overlay import IMAGE_IDS_PER_WIDGET


@pytest.fixture
def paths(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("keep\ntail\n", encoding="utf-8")       # nothing added here
    b.write_text("keep\nADDED\ntail\n", encoding="utf-8")
    return [str(a), str(b)]


def run(coro):
    return asyncio.run(coro)


def test_render_insert_marker_is_two_opaque_rows():
    rgba = render_insert_marker(3, (10, 20, 30))
    assert len(rgba) == 3 * INSERT_MARKER_PX * 4
    assert bytes(rgba[:4]) == bytes((10, 20, 30, 255))
    # every pixel identical and fully opaque
    assert set(rgba[3::4]) == {255}


def test_view_marks_the_empty_side_only(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            view = app.views[0]
            # pane 0 gains nothing where b adds "ADDED" -> zero-height chunk
            assert view.panes[0]._insert_markers == [(1, "insert")]
            # pane 1 actually has the line, so it is filled, not marked
            assert view.panes[1]._insert_markers == []

    run(scenario())


def test_kitty_draws_one_thin_image_per_visible_marker(paths):
    async def scenario():
        app = TmeldApp(paths, graphics="kitty", cell_px=(8, 16))
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            pane = app.views[0].panes[0]
            assert pane.graphics == "kitty"

            images = pane._render_overlays()
            assert len(images) == 1, images
            rgba, w, h, row, col = images[0]

            assert h == INSERT_MARKER_PX
            region = pane.content_region
            assert w == (region.width - pane.gutter_width) * 8
            # marker for doc line 1, unscrolled, sits on the pane's second row
            assert row == region.y + 1
            assert col == region.x + pane.gutter_width

            # painted in the insert *line* colour, as Meld does
            want = _hex_rgb(pane.theme_def.chunk["insert"].line)
            assert bytes(rgba[:4]) == bytes((*want, 255))
            assert len(rgba) == w * h * 4

    run(scenario())


def test_sixel_refuses_to_draw_over_text(paths):
    """Sixel pixels become cell content: a marker would erase the glyphs."""
    async def scenario():
        app = TmeldApp(paths, graphics="sixel", cell_px=(8, 16))
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            pane = app.views[0].panes[0]
            assert pane.graphics == "sixel"
            assert pane._insert_markers == [(1, "insert")]
            assert pane._render_overlays() == []

    run(scenario())


def test_marker_scrolled_out_of_view_is_not_drawn(paths, tmp_path):
    a = tmp_path / "long_a.txt"
    b = tmp_path / "long_b.txt"
    a.write_text("".join(f"line {i}\n" for i in range(200)), encoding="utf-8")
    lines = [f"line {i}\n" for i in range(200)]
    lines.insert(150, "ADDED\n")
    b.write_text("".join(lines), encoding="utf-8")

    async def scenario():
        app = TmeldApp([str(a), str(b)], graphics="kitty", cell_px=(8, 16))
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            pane = app.views[0].panes[0]
            assert pane._insert_markers == [(150, "insert")]
            # marker is far below the viewport
            assert pane._render_overlays() == []

            pane.scroll_to(y=145, animate=False)
            await pilot.pause()
            images = pane._render_overlays()
            assert len(images) == 1, "marker should appear once scrolled to"
            assert images[0][3] == pane.content_region.y + (150 - 145)

    run(scenario())


def test_stale_kitty_images_are_deleted_when_markers_vanish(paths):
    """kitty images float above the cells, so they must be explicitly removed."""
    async def scenario():
        app = TmeldApp(paths, graphics="kitty", cell_px=(8, 16))
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            pane = app.views[0].panes[0]

            written = []
            pane._write = written.append

            pane._paint_overlay()
            assert pane._painted == 1
            assert any("\x1b_G" in w for w in written), "no kitty payload"

            # remove the difference: the marker goes away, its image must too
            written.clear()
            pane._insert_markers = []
            pane._paint_overlay()
            assert pane._painted == 0
            assert any(f"d=I,i={pane._image_id}" in w for w in written), written

    run(scenario())


def test_widgets_own_disjoint_image_id_blocks(paths):
    async def scenario():
        app = TmeldApp(paths, graphics="kitty", cell_px=(8, 16))
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            view = app.views[0]
            ids = [w._image_id for w in
                   (*view.panes, *view.gutters)]
            assert len(set(ids)) == len(ids)
            # a pane may place many marker images; blocks must not overlap
            assert min(abs(x - y) for x in ids for y in ids if x != y) \
                >= IMAGE_IDS_PER_WIDGET

    run(scenario())
