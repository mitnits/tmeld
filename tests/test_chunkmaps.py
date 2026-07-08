"""One overview strip per pane, and scrollbars kept off the linkmaps.

Meld builds chunkmap0..2, one per pane (filediff.ui). tmeld had a single map
hardwired to pane 1, so the left pane's overview was simply absent -- and the
two never drifted apart as the sides diverged.
"""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.chunkmap import ChunkMap


@pytest.fixture
def two(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("".join(f"line {i}\n" for i in range(200)), encoding="utf-8")
    lines = [f"line {i}\n" for i in range(200)]
    lines.insert(50, "ADDED\n")
    lines.insert(120, "ADDED\n")
    b.write_text("".join(lines), encoding="utf-8")
    return [str(a), str(b)]


@pytest.fixture
def three(tmp_path):
    out = []
    for name, mark in (("local", "LOCAL"), ("base", None), ("remote", "REMOTE")):
        p = tmp_path / f"{name}.txt"
        p.write_text("".join(
            (f"{mark} {i}\n" if mark and i in (10, 40) else f"line {i}\n")
            for i in range(60)), encoding="utf-8")
        out.append(str(p))
    return out


def run(coro):
    return asyncio.run(coro)


def test_one_chunkmap_per_pane(two):
    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            view = app.views[0]
            assert len(view.chunkmaps) == view.num_panes == 2
            assert len(app.query(ChunkMap)) == 2
            for i, cm in enumerate(view.chunkmaps):
                assert cm.pane is view.panes[i]

    run(scenario())


def test_maps_use_their_own_pane_geometry(two):
    """The whole point: remove lines from one side and its map drifts."""
    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            left, right = app.views[0].chunkmaps
            assert left._total_lines == 200
            assert right._total_lines == 202       # b.txt gained two lines
            assert left._total_lines != right._total_lines
            # the same chunk sits at different document lines on each side
            assert left._chunks[1][1] != right._chunks[1][1]

    run(scenario())


def test_clicking_a_map_scrolls_its_own_pane(two):
    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            view = app.views[0]
            view.chunkmaps[0].on_jump(150)
            await pilot.pause()
            await pilot.pause()
            assert int(view.panes[0].scroll_offset.y) > 100

    run(scenario())


def test_chunkmap_is_one_column(two):
    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            for cm in app.views[0].chunkmaps:
                assert cm.size.width == 1

    run(scenario())


def test_scrollbars_never_sit_against_a_linkmap(three):
    """First pane's bar goes far left; a middle pane hides its own."""
    async def scenario():
        app = TmeldApp(three)
        async with app.run_test(size=(120, 20)) as pilot:
            await pilot.pause()
            view = app.views[0]
            first, middle, last = view.panes

            assert first.scrollbar_on_left is True
            bar = first.vertical_scrollbar.region
            assert bar.x < first.content_region.x, "bar must precede the text"
            assert bar.right <= first.content_region.x, "bar overlaps the text"

            # the middle pane is between two gutters and cannot win either way
            assert middle.styles.scrollbar_size_vertical == 0
            assert last.scrollbar_on_left is False

            # ...and it is still scrollable
            middle.scroll_to(y=20, animate=False)
            await pilot.pause()
            assert int(middle.scroll_offset.y) > 0

    run(scenario())


def test_left_scrollbar_padding_is_stable(two):
    """Text must not jump sideways when the scrollbar appears."""
    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.views[0].panes[0]
            assert pane.styles.padding.left == pane.styles.scrollbar_size_vertical
            # reserved even though Widget.scrollbar_size_vertical is dynamic
            assert pane.styles.padding.left == 1

    run(scenario())


def test_no_chunk_action_on_the_title_border_row(two):
    """Gutter row 0 maps to the line above the viewport, under the title."""
    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            view = app.views[0]
            view.panes[0].scroll_to(y=51, animate=False)
            await pilot.pause()
            await pilot.pause()
            gutter = view.gutters[0]
            row0 = "".join(seg.text for seg in gutter.render_line(0))
            assert "▶" not in row0 and "◀" not in row0, row0

    run(scenario())


def test_title_rule_runs_across_the_gutter(two):
    """The panes' top border must meet in the middle of the ditch.

    Row 0 of the gutter lines up with the panes' title border. Painting it in
    the gutter's grey left a notch directly above the ditch.
    """
    from tmeld.palette import THEMES

    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(90, 12)) as pilot:
            await pilot.pause()
            view = app.views[0]
            gutter = view.gutters[0]

            strip = gutter.render_line(0)
            text = "".join(seg.text for seg in strip)
            assert text == "─" * gutter.size.width, repr(text)

            # ...on the page, not in the gutter's grey
            bgs = {seg.style.bgcolor.name.lower() for seg in strip}
            assert bgs == {THEMES["meld-base"].page_bg.lower()}, bgs
            assert THEMES["meld-base"].gutter_bg.lower() not in bgs

            # each half continues its own pane's rule
            left = view.panes[0].styles.border_top[1].hex.lower()
            right = view.panes[1].styles.border_top[1].hex.lower()
            assert left != right, "focused and unfocused borders should differ"
            colours = [seg.style.color.name.lower() for seg in strip]
            assert colours == [left, right]

    run(scenario())


def test_title_rule_follows_focus(two):
    async def scenario():
        app = TmeldApp(two)
        async with app.run_test(size=(90, 12)) as pilot:
            await pilot.pause()
            view = app.views[0]
            gutter = view.gutters[0]

            def halves():
                strip = gutter.render_line(0)
                return [seg.style.color.name.lower() for seg in strip]

            view.panes[0].focus()
            await pilot.pause()
            first = halves()
            view.panes[1].focus()
            await pilot.pause()
            second = halves()
            assert first != second, "rule did not re-tint when focus moved"
            assert first[0] == second[1], "the focused colour should swap sides"

    run(scenario())


def test_three_way_joins_both_gutters(three):
    async def scenario():
        app = TmeldApp(three)
        async with app.run_test(size=(120, 12)) as pilot:
            await pilot.pause()
            for gutter in app.views[0].gutters:
                text = "".join(seg.text for seg in gutter.render_line(0))
                assert text == "─" * gutter.size.width, repr(text)

    run(scenario())
