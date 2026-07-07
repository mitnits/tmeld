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
            assert gutter.size.width == 2 + GRAPHIC_IMAGE_COLS
            assert written, "no overlay written after mount"
            escape = written[-1]
            assert "\x1b_G" in escape and "a=T" in escape
            # image sized from cell_px and the gutter's cell geometry
            image_cols = gutter.size.width - 2
            assert f"s={image_cols * 10}" in escape
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
