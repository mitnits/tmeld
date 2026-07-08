"""Regression tests for the first hands-on feedback round.

1. Cursor on a changed line must not wipe the chunk background
   (line-number gets the highlight instead).
2. Pane text must use the theme's readable fg (black on white for
   meld-base), not the app default.
3. Pushing a chunk must clear the old highlight from BOTH panes —
   guards against TextArea's line cache serving stale strips.
"""

import asyncio

import pytest

from tmeld.app import TmeldApp
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


def test_cursor_on_changed_line_keeps_chunk_background(paths):
    files = paths(["a", "xxx", "c"], ["a", "yyy", "c"])

    async def scenario():
        # line numbers are off by default; the current-line tint this test
        # checks for is painted in the line-number column
        app = TmeldApp(files, show_line_numbers=True)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            pane.move_cursor((1, 0))  # cursor ONTO the changed line
            await pilot.pause()
            return app.export_screenshot().upper()

    svg = run(scenario())
    # Chunk fill must survive in BOTH panes — the cursor sits on the
    # changed line in pane 0, so one occurrence per pane is required
    # (a single occurrence means the cursor line got wiped). With the
    # cursor inside the chunk it is the *current* chunk, so the fill is
    # the emphasized variant (replace fill blended toward white).
    assert svg.count("DEEEFF") >= 2
    # Meld tints the current line in the line-number column, so this only means
    # anything with the numbers shown (they are off by default).
    assert "FFFFBF" in svg


def test_pane_text_is_readable_on_light_page(paths):
    files = paths(["hello world"], ["hello world"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            base = pane._theme.base_style
            return base.color.name, base.bgcolor.name

    fg, bg = run(scenario())
    assert fg == "#000000"
    assert bg == "#ffffff"


def gutter_row_text(gutter, y):
    return "".join(seg.text for seg in gutter.render_line(y))


def test_gutter_row_patterns(paths):
    # User-specified layout: divider on quiet rows, open at chunks,
    # arrows on the side that has lines to push. Equal-length files so
    # rows align across panes at scroll 0.
    files = paths(
        ["a", "both-l", "c", "LEFT", "e"],
        ["a", "both-r", "c", "RIGHT", "e"],
    )

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            gutter = app.query_one(ActionGutter)
            # +1 for the pane top border: gutter row = doc line + 1
            return {
                "quiet": gutter_row_text(gutter, 1),   # line 0: "a"
                "both": gutter_row_text(gutter, 2),    # both-l vs both-r
                "arrow_seg_bg": [
                    seg.style.bgcolor.name
                    for seg in gutter.render_line(2)
                ],
            }

    rows = run(scenario())
    assert rows["quiet"] == "│ │"
    assert rows["both"] == "▶ ◀"
    # Arrows sit on the page background, not a colored pill
    assert set(rows["arrow_seg_bg"]) == {"#ffffff"}


def test_gutter_left_only_pattern(paths):
    files = paths(["a", "LEFT", "c"], ["a", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            gutter = app.query_one(ActionGutter)
            return gutter_row_text(gutter, 2)  # doc line 1 in left pane

    assert run(scenario()) == "▶ │"


def test_gutter_right_only_pattern(paths):
    files = paths(["a", "c"], ["a", "RIGHT", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            gutter = app.query_one(ActionGutter)
            return gutter_row_text(gutter, 2)  # doc line 1 in right pane

    assert run(scenario()) == "│ ◀"


def test_push_clears_highlight_in_both_panes(paths):
    files = paths(["a", "NEW", "c"], ["a", "old", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            before = app.export_screenshot().upper()
            await pilot.click(ActionGutter, offset=(0, 2))
            await pilot.pause()
            after = app.export_screenshot().upper()
            return before, after

    before, after = run(scenario())
    assert "BDDDFF" in before
    # Files now identical: no chunk colors may survive anywhere
    assert "BDDDFF" not in after
    assert "D0FFA3" not in after
    assert "8AC2FF" not in after


def test_sync_scroll_does_not_jitter(paths):
    # Regression for the endless up-down jitter: the synced pane's echo
    # must not re-trigger a sync of the master. Uneven insert chunks make
    # the interpolation asymmetric, which is what fed the loop.
    a_lines = [f"line {i}" for i in range(300)]
    b_lines = list(a_lines)
    for pos in (250, 200, 150, 100, 50):
        b_lines.insert(pos, f"extra at {pos}")
    files = paths(a_lines, b_lines)

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            app.panes[0].scroll_to(y=120, animate=False)
            snapshots = []
            for _ in range(6):
                await pilot.pause(0.05)
                snapshots.append(
                    (app.panes[0].scroll_offset.y, app.panes[1].scroll_offset.y)
                )
            return snapshots

    snapshots = run(scenario())
    # After the initial sync settles, positions must be identical across
    # every subsequent snapshot — any oscillation means the loop is back
    assert len(set(snapshots[1:])) == 1, snapshots


def test_save_button_in_title_when_dirty(paths):
    files = paths(["a", "b", "c"], ["a", "b", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            pane.move_cursor((1, 0))
            await pilot.press("Z")
            await pilot.pause()
            title_dirty = pane.border_title
            # Click the title row (acts as the Save button while dirty)
            await pilot.click(pane, offset=(4, 0))
            await pilot.pause()
            return title_dirty, pane.border_title, app.dirty[0]

    title_dirty, title_after, dirty = run(scenario())
    assert "Save" in title_dirty
    assert "Save" not in title_after
    assert dirty is False
    with open(files[0], encoding="utf-8") as f:
        assert f.read() == "a\nZb\nc\n"


def test_edit_clears_stale_highlight_without_scroll(paths):
    # Same cache-staleness guard through the typing path: fix the
    # difference by editing, highlight must vanish after the debounce
    files = paths(["a", "xxy", "c"], ["a", "xx", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[1]
            pane.focus()
            pane.move_cursor((1, 2))
            await pilot.press("y")
            from tmeld.app import REDIFF_DEBOUNCE

            await pilot.pause(REDIFF_DEBOUNCE + 0.2)
            return (
                app.comparison.differ.diff_count(),
                app.export_screenshot().upper(),
            )

    count, svg = run(scenario())
    assert count == 0
    assert "BDDDFF" not in svg
