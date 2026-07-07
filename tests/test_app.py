"""Headless end-to-end tests of the tmeld app via Textual's pilot."""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.pane import DiffPane


@pytest.fixture
def paths(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a_lines = [f"common line {i}" for i in range(120)]
    b_lines = list(a_lines)
    b_lines[10] = "changed line ten"
    b_lines.insert(60, "inserted line sixty")
    del b_lines[90]
    a.write_text("\n".join(a_lines) + "\n", encoding="utf-8")
    b.write_text("\n".join(b_lines) + "\n", encoding="utf-8")
    return [str(a), str(b)]


def test_chunk_colors_reach_the_screen(paths):
    async def run():
        app = TmeldApp(paths)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            return app.export_screenshot()

    svg = asyncio.run(run()).upper()
    assert "BDDDFF" in svg  # meld:replace fill (pale blue)
    assert "8AC2FF" in svg  # meld:inline background


def test_chunk_navigation_scrolls_both_panes(paths):
    async def run():
        app = TmeldApp(paths)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            panes = list(app.query(DiffPane).results())
            assert all(p.scroll_offset.y == 0 for p in panes)
            # First chunk (line 10) is on-screen; second (line ~60) is not
            await pilot.press("ctrl+d")
            await pilot.pause()
            await pilot.press("ctrl+d")
            await pilot.pause()
            return [p.scroll_offset.y for p in panes]

    y0, y1 = asyncio.run(run())
    assert y0 > 0 and y1 > 0
    assert abs(y0 - y1) <= 2  # same region in both panes near chunk 2

    async def run_sync_check():
        app = TmeldApp(paths)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            pane0 = app.panes[0]
            pane1 = app.panes[1]
            pane0.scroll_to(y=60, animate=False)
            await pilot.pause()
            return pane0.scroll_offset.y, pane1.scroll_offset.y

    y0, y1 = asyncio.run(run_sync_check())
    assert y0 == 60
    # b has one insert and one delete before line ~100: net offset 0,
    # but interpolation may differ by a line near chunk edges
    assert abs(y1 - y0) <= 2


def test_focus_border_does_not_leak_from_textarea_defaults(paths):
    """Regression: with pane CSS in DEFAULT_CSS instead of App.CSS,
    TextArea's built-in `TextArea:focus {border: ...}` outranked our
    `border: none` and painted full-height dark/accent columns beside
    the focused pane — visible only while the terminal window had focus
    (blur removes :focus). The rules must stay in TmeldApp.CSS."""

    async def run():
        app = TmeldApp(paths)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.focused is app.panes[0]
            return app.export_screenshot()

    svg = asyncio.run(run())
    # The leak painted near-full-height columns (~22 cells at one x) in
    # Textual's default border colors. Scrollbar handles legitimately
    # use the accent color, but only for a few cells at one position.
    import re
    from collections import Counter

    for color in ("#121212", "#0178d4"):
        xs = Counter(
            x for x in re.findall(
                r'<rect fill="%s" x="([\d.]+)"' % color, svg
            )
        )
        tallest = max(xs.values(), default=0)
        assert tallest < 15, f"{color} column of {tallest} cells: border leak"
