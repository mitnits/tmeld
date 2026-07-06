"""Tests: current-chunk emphasis, locate-based nav, copy actions, viewport."""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.chunkmap import ChunkMap
from tmeld.palette import MELD_BASE, blend


@pytest.fixture
def paths(tmp_path):
    def make(a_lines, b_lines):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("\n".join(a_lines) + "\n", encoding="utf-8")
        b.write_text("\n".join(b_lines) + "\n", encoding="utf-8")
        return [str(a), str(b)]

    return make


def run(coro):
    return asyncio.run(coro)


def test_blend():
    assert blend("#bdddff", "#ffffff", 0.5) == "#deeeff"
    assert blend("#000000", "#ffffff", 0.0) == "#000000"
    assert blend("#000000", "#ffffff", 1.0) == "#ffffff"


def test_current_chunk_emphasis_follows_cursor(paths):
    files = paths(["a", "x1", "c", "x2", "e"], ["a", "y1", "c", "y2", "e"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            pane.move_cursor((1, 0))  # into first chunk
            await pilot.pause()
            svg_on_first = app.export_screenshot().upper()
            first = app.current_chunk
            pane.move_cursor((2, 0))  # between chunks
            await pilot.pause()
            between = app.current_chunk
            return first, between, svg_on_first

    first, between, svg = run(scenario())
    assert first == 0
    assert between is None
    # Emphasized fill (replace fill blended 50% toward white) on screen,
    # in both panes, alongside the normal fill of the other chunk
    emphasized = blend(MELD_BASE.chunk["replace"].fill, "#ffffff", 0.5)
    assert svg.count(emphasized.lstrip("#").upper()) >= 2
    assert "BDDDFF" in svg


def test_nav_is_cursor_relative(paths):
    a = [f"l{i}" for i in range(40)]
    b = list(a)
    b[5] = "chunk-one"
    b[20] = "chunk-two"
    files = paths(a, b)

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            positions = []
            await pilot.press("ctrl+d")
            positions.append(pane.cursor_location[0])
            await pilot.press("ctrl+d")
            positions.append(pane.cursor_location[0])
            await pilot.press("ctrl+e")
            positions.append(pane.cursor_location[0])
            # Click far below both chunks, then prev should find chunk 2
            pane.move_cursor((35, 0))
            await pilot.press("ctrl+e")
            positions.append(pane.cursor_location[0])
            return positions

    assert run(scenario()) == [5, 20, 5, 20]


def test_copy_above_into_other_pane(paths):
    files = paths(["a", "X", "c"], ["a", "Y", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            await pilot.pause()
            pane.move_cursor((1, 0))
            app.action_copy_up_right()
            await pilot.pause()
            return app.panes[1].text

    assert run(scenario()).split("\n") == ["a", "X", "Y", "c"]


def test_copy_below_into_other_pane(paths):
    files = paths(["a", "X", "c"], ["a", "Y", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[1]
            pane.focus()
            await pilot.pause()
            pane.move_cursor((1, 0))
            app.action_copy_down_left()
            await pilot.pause()
            return app.panes[0].text

    assert run(scenario()).split("\n") == ["a", "X", "Y", "c"]


def test_chunkmap_viewport_indicator(paths):
    a = [f"l{i}" for i in range(400)]
    files = paths(a, list(a))

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            cm = app.query_one(ChunkMap)
            top_seg = list(cm.render_line(0))[0]
            bottom_seg = list(cm.render_line(cm.size.height - 1))[0]
            return top_seg.style.bgcolor.name, bottom_seg.style.bgcolor.name

    top_bg, bottom_bg = run(scenario())
    overlay_on_page = blend("#ffffff", "#646464", 0.4)
    assert top_bg == overlay_on_page  # viewport band at the top
    assert bottom_bg == "#ffffff"     # rest of the map is plain page
