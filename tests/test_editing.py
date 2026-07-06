"""Phase 3 tests: editing, live re-diff, chunk actions, mouse actions."""

import asyncio

import pytest

from tmeld.app import REDIFF_DEBOUNCE, TmeldApp
from tmeld.chunkmap import ChunkMap
from tmeld.gutter import ActionGutter


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


def test_editing_triggers_rediff(paths):
    files = paths(["a", "b", "c"], ["a", "b", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            assert app.comparison.differ.diff_count() == 0
            pane = app.panes[1]
            pane.focus()
            pane.move_cursor((1, 0))
            await pilot.press("X")
            await pilot.pause(REDIFF_DEBOUNCE + 0.2)
            return (
                app.comparison.differ.diff_count(),
                app.dirty[:],
                app.panes[1].border_title,
            )

    count, dirty, title = run(scenario())
    assert count == 1
    assert dirty == [False, True]
    assert title.endswith("*")


def test_push_chunk_right_resolves_diff(paths):
    files = paths(["a", "NEW", "c"], ["a", "old", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            assert app.comparison.differ.diff_count() == 1
            app.panes[0].focus()
            app.panes[0].move_cursor((1, 0))
            app.action_push_right()
            await pilot.pause()
            return app.comparison.differ.diff_count(), app.panes[1].text

    count, text = run(scenario())
    assert count == 0
    assert text.split("\n") == ["a", "NEW", "c"]


def test_push_chunk_at_eof(paths):
    # Chunk touching EOF exercises the newline splice path
    files = paths(["a", "b", "EXTRA"], ["a", "b"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app.panes[0].focus()
            app.panes[0].move_cursor((2, 0))
            app.action_push_right()
            await pilot.pause()
            return app.comparison.differ.diff_count(), app.panes[1].text

    count, text = run(scenario())
    assert count == 0
    assert text.split("\n") == ["a", "b", "EXTRA"]


def test_delete_chunk(paths):
    files = paths(["a", "GONE", "c"], ["a", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app.panes[0].focus()
            app.panes[0].move_cursor((1, 0))
            app.action_delete_chunk()
            await pilot.pause()
            return app.comparison.differ.diff_count(), app.panes[0].text

    count, text = run(scenario())
    assert count == 0
    assert text.split("\n") == ["a", "c"]


def test_save_writes_file_preserving_trailing_newline(paths):
    files = paths(["a", "x", "c"], ["a", "y", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app.panes[0].focus()
            app.panes[0].move_cursor((1, 0))
            app.action_push_right()
            await pilot.pause()
            app.panes[1].focus()
            await pilot.pause()
            app.action_save()
            await pilot.pause()
            return app.dirty[:]

    dirty = run(scenario())
    assert dirty == [False, False]
    with open(files[1], encoding="utf-8") as f:
        assert f.read() == "a\nx\nc\n"


def test_gutter_click_pushes_chunk(paths):
    files = paths(["a", "NEW", "c"], ["a", "old", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            # Chunk starts at doc line 1; panes are unscrolled and have a
            # 1-row border, so the arrow sits at gutter row 2
            await pilot.click(ActionGutter, offset=(0, 2))
            await pilot.pause()
            return app.comparison.differ.diff_count(), app.panes[1].text

    count, text = run(scenario())
    assert count == 0
    assert text.split("\n") == ["a", "NEW", "c"]


def test_chunkmap_click_scrolls(paths):
    a_lines = [f"line {i}" for i in range(200)]
    b_lines = list(a_lines)
    b_lines[150] = "changed"
    files = paths(a_lines, b_lines)

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            chunkmap = app.query_one(ChunkMap)
            height = chunkmap.size.height
            # Click near the bottom of the map: jump deep into the file
            await pilot.click(ChunkMap, offset=(0, height - 2))
            await pilot.pause()
            return app.panes[1].scroll_offset.y

    y = run(scenario())
    assert y > 100


def test_undo_after_push(paths):
    files = paths(["a", "NEW", "c"], ["a", "old", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app.panes[0].focus()
            app.panes[0].move_cursor((1, 0))
            app.action_push_right()
            await pilot.pause()
            app.panes[1].focus()
            await pilot.press("ctrl+z")
            await pilot.pause(REDIFF_DEBOUNCE + 0.2)
            return app.comparison.differ.diff_count(), app.panes[1].text

    count, text = run(scenario())
    assert text.split("\n") == ["a", "old", "c"]
    assert count == 1

