"""Tier-2 integration tests: gutter widening, overlay writes, hooks."""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.gutter import GRAPHIC_IMAGE_COLS


@pytest.fixture
def files(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a_lines = [f"line {i}" for i in range(60)]
    b_lines = list(a_lines)
    b_lines[5] = "changed"
    del b_lines[30]
    a.write_text("\n".join(a_lines) + "\n")
    b.write_text("\n".join(b_lines) + "\n")
    return [str(a), str(b)]


def run(coro):
    return asyncio.run(coro)


def capture_writes(app):
    written = []
    for gutter in app.views[0].gutters:
        gutter._write = written.append
    return written


def test_gutter_stays_narrow_without_graphics(files):
    async def scenario():
        app = TmeldApp(files)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.gutters[0].size.width == 3
            assert app.gutters[0].graphics == "none"

    run(scenario())


def test_kitty_mode_widens_gutter_and_paints(files):
    async def scenario():
        app = TmeldApp(files, graphics="kitty", cell_px=(10, 20))
        async with app.run_test() as pilot:
            written = capture_writes(app)
            await pilot.pause()
            gutter = app.gutters[0]
            assert gutter.graphics == "kitty"
            # the whole gutter is the image now: the arrows are painted into
            # it rather than occupying two reserved cells
            assert gutter.size.width == GRAPHIC_IMAGE_COLS
            assert written, "no overlay written after mount"
            escape = written[-1]
            assert "\x1b_G" in escape and "a=T" in escape
            # image sized from cell_px and the gutter's cell geometry
            assert f"s={gutter.size.width * 10}" in escape
            # positioned via CUP inside a cursor save/restore
            assert escape.startswith("\x1b7\x1b[") and escape.endswith("\x1b8")

    run(scenario())


def test_scroll_repaints_overlay(files):
    async def scenario():
        app = TmeldApp(files, graphics="kitty")
        async with app.run_test() as pilot:
            written = capture_writes(app)
            await pilot.pause()
            before = len(written)
            app.panes[0].scroll_to(y=20, animate=False)
            await pilot.pause()
            await pilot.pause()
            assert len(written) > before

    run(scenario())


def test_sixel_mode_writes_dcs(files):
    async def scenario():
        app = TmeldApp(files, graphics="sixel", cell_px=(8, 16))
        async with app.run_test() as pilot:
            written = capture_writes(app)
            await pilot.pause()
            assert any("\x1bP0;0;8q" in w for w in written)

    run(scenario())


def test_tab_switch_hides_kitty_images(files, tmp_path):
    c = tmp_path / "c.py"
    d = tmp_path / "d.py"
    c.write_text("one\n")
    d.write_text("two\n")

    async def scenario():
        app = TmeldApp(
            diffs=[(files, None), ([str(c), str(d)], None)],
            graphics="kitty",
        )
        async with app.run_test() as pilot:
            written = capture_writes(app)
            await pilot.pause()
            from textual.widgets import TabbedContent

            app.query_one(TabbedContent).active = "tab1"
            await pilot.pause()
            gutter = app.views[0].gutters[0]
            delete = f"\x1b_Ga=d,d=I,i={gutter._image_id},q=2\x1b\\"
            assert delete in written

    run(scenario())


def test_probe_returns_none_headless():
    from tmeld.term import probe_graphics

    assert probe_graphics(timeout=0.05) == "none"


def test_chunkmap_paints_pixel_overlay(files):
    async def scenario():
        from tmeld.chunkmap import ChunkMap

        app = TmeldApp(files, graphics="kitty", cell_px=(8, 16))
        async with app.run_test() as pilot:
            written = []
            chunkmap = app.views[0].query_one(ChunkMap)
            chunkmap._write = written.append
            await pilot.pause()
            chunkmap.refresh_overlay()
            await pilot.pause()
            assert any("\x1b_G" in w for w in written)
            # image height covers the full map at cell resolution
            escape = written[-1]
            assert f"v={chunkmap.content_region.height * 16}" in escape

    run(scenario())


def test_graphics_gutter_has_no_reserved_arrow_columns(files):
    """The icons are rasterized into the linkmap, so no cells are set aside.

    Meld's ActionGutter paints the chunk fill behind its button; drawing the
    linkmap as pixels lets the icons live inside it, freeing two cells.
    """
    async def scenario():
        app = TmeldApp(files, graphics="kitty", cell_px=(9, 19))
        async with app.run_test(size=(60, 14)) as pilot:
            await pilot.pause()
            gutter = app.gutters[0]
            assert gutter.size.width == GRAPHIC_IMAGE_COLS

            # every cell under the image is blank; row 0 is the title rule,
            # which the gutter carries across so the panes' borders meet
            from tmeld.gutter import PANE_BORDER_ROWS
            for y in range(PANE_BORDER_ROWS, gutter.size.height):
                strip = gutter.render_line(y)
                assert {seg.text for seg in strip} <= {" ", " " * gutter.size.width}
            border = "".join(seg.text for seg in gutter.render_line(0))
            assert border == "─" * gutter.size.width, repr(border)

            rgba, w, h, _row, _col = gutter._render_overlay()
            assert w == GRAPHIC_IMAGE_COLS * 9, "image must span the whole gutter"

    run(scenario())


def test_gutter_icons_are_drawn_into_the_image(files):
    async def scenario():
        app = TmeldApp(files, graphics="kitty", cell_px=(9, 19))
        async with app.run_test(size=(60, 14)) as pilot:
            await pilot.pause()
            gutter = app.gutters[0]
            arrows = gutter._arrows(19, 12, gutter.size.height - 1)
            assert arrows, "no icons for the visible chunks"
            assert {a.kind for a in arrows} == {"push"}
            assert {a.on_right for a in arrows} == {False, True}

            # an icon on the left edge must colour pixels there
            rgba, w, h, _r, _c = gutter._render_overlay()
            top = int(arrows[0].top) + 6
            px = lambda x, y: tuple(rgba[(y * w + x) * 4:(y * w + x) * 4 + 4])
            assert px(1, top)[3] > 0, "left icon painted no pixels"

    run(scenario())


def test_delete_icon_replaces_the_arrow_toward_a_readonly_pane(files):
    from tmeld.comparisonview import ComparisonView

    async def scenario():
        app = TmeldApp(files, graphics="kitty", cell_px=(9, 19))
        async with app.run_test(size=(60, 14)) as pilot:
            await pilot.pause()
            app.post_message(ComparisonView.OpenComparison(list(files), readonly=(0,)))
            await pilot.pause()
            await pilot.pause()
            gutter = app.views[-1].gutters[0]
            arrows = gutter._arrows(19, 12, gutter.size.height - 1)
            kinds = {(a.on_right, a.kind) for a in arrows}
            # ▶ copies out of the read-only pane; ◀ would write into it
            assert (False, "push") in kinds
            assert (True, "delete") in kinds
            assert (True, "push") not in kinds

    run(scenario())


def test_icon_colour_follows_the_fill_not_the_tag():
    """A one-sided chunk is green; its icon must not be meld:delete's red."""
    from tmeld.palette import THEMES

    theme = THEMES["meld-base"]
    assert theme.chunk["delete"].fill == theme.chunk["insert"].fill
    assert theme.chunk["delete"].fg != theme.chunk["insert"].fg  # upstream quirk
    assert theme.chunk_fg("delete") == theme.chunk["insert"].fg
    assert theme.chunk_fg("replace") == theme.chunk["replace"].fg


def test_arrow_box_matches_melds_proportions():
    """Meld's meld-change-apply-right is 13x12 -- a shade wider than tall.

    Sizing the box to one cell wide made it spindly; the width is driven off
    the height so it stays chunky at every cell size, and two arrows can never
    meet in the middle of the gutter.
    """
    from tmeld.gutter import GRAPHIC_IMAGE_COLS

    for cell_w, cell_h in ((7, 15), (8, 16), (9, 19), (10, 20)):
        width_px = GRAPHIC_IMAGE_COLS * cell_w
        arrow_h = max(8, min(cell_h - 4, 13))
        arrow_w = max(7, min(round(arrow_h * 1.1), width_px // 3))
        assert 0.95 <= arrow_w / arrow_h <= 1.2, (cell_w, cell_h, arrow_w, arrow_h)
        assert 2 * arrow_w < width_px, "arrows would collide"
        assert arrow_h <= cell_h, "arrow taller than its row"


def test_arrow_is_solid_through_the_shaft():
    """Thin arrows were the complaint: the shaft must be a third of the height."""
    from tmeld.linkmap import _Canvas, draw_arrow

    w, h = 13, 12
    canvas = _Canvas(w, h)
    draw_arrow(canvas, 0, 0, w, h, (0, 0, 0), pointing_right=True)
    opaque = lambda x, y: canvas.data[(y * w + x) * 4 + 3] > 200
    shaft_px = sum(opaque(1, y) for y in range(h))
    assert shaft_px >= 4, f"shaft only {shaft_px}px of {h}"
    assert shaft_px / h >= 0.33
    # the head reaches the far edge on the centre line -- antialiased to a
    # point, so it is present rather than fully opaque
    alpha = lambda x, y: canvas.data[(y * w + x) * 4 + 3]
    assert alpha(w - 1, h // 2) > 0, "arrow head does not reach the tip"
    assert opaque(w - 7, h // 2), "head base should be solid"
    # ...and the tip is a point, not a block
    assert alpha(w - 1, 1) == 0


def test_cross_is_heavy():
    from tmeld.linkmap import _Canvas, draw_delete

    w = h = 11
    canvas = _Canvas(w, h)
    draw_delete(canvas, 0, 0, w, h, (0, 0, 0))
    opaque = lambda x, y: canvas.data[(y * w + x) * 4 + 3] > 200
    assert sum(opaque(0, y) for y in range(h)) >= 2, "stroke too thin at the edge"
    assert opaque(w // 2, h // 2), "strokes must cross at the centre"
