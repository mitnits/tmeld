"""Tabbed shell tests: multi-pair --diff, tab labels, close, exit codes."""

import asyncio

import pytest

from textual.widgets import TabbedContent, Tabs

from tmeld.app import TmeldApp, main
from tmeld.filediff import FileDiffView
from tmeld.misc import shorten_names


@pytest.fixture
def pair(tmp_path):
    counter = [0]

    def make(left_lines, right_lines, subdir=None):
        d = tmp_path / (subdir or f"d{counter[0]}")
        counter[0] += 1
        d.mkdir(exist_ok=True)
        a, b = d / "a.txt", d / "b.txt"
        a.write_text("\n".join(left_lines) + "\n", encoding="utf-8")
        b.write_text("\n".join(right_lines) + "\n", encoding="utf-8")
        return [str(a), str(b)]

    return make


def run(coro):
    return asyncio.run(coro)


# --- shorten_names (upstream misc.py port) --------------------------------


def test_shorten_names_basenames():
    assert shorten_names("/tmp/foo1", "/tmp/foo2") == ["foo1", "foo2"]


def test_shorten_names_same_basename_gets_indicator():
    assert shorten_names("/a/b/c", "/a/d/c") == ["[b] c", "[d] c"]


# --- CLI validation --------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],  # nothing to compare
        ["one.txt"],  # 1 positional
        ["a", "b", "c", "d"],  # too many positionals
        ["--diff", "only-one"],  # short --diff group
        ["--diff", "a", "b", "c", "d"],  # long --diff group
        ["-o", "out", "--diff", "a", "b"],  # -o without positional 3-way
        ["-o", "out", "a", "b"],  # -o with 2-way
    ],
)
def test_cli_rejects(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


# --- Tab shell --------------------------------------------------------------


def test_single_comparison_hides_tab_bar(pair):
    files = pair(["a"], ["b"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.views) == 1
            assert not app.query_one(Tabs).display

    run(scenario())


def test_two_diffs_two_tabs(pair):
    files1 = pair(["a"], ["b"])
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.views) == 2
            assert app.query_one(Tabs).display
            assert app.view is app.views[0]
            # every view is fully wired, not just the active one
            for view in app.views:
                assert len(view.panes) == 2
                assert view.comparison.differ.diff_count() == 1

    run(scenario())


def test_actions_route_to_active_tab(pair):
    files1 = pair(["a", "same"], ["b", "same"])
    files2 = pair(["x", "same"], ["y", "same"])

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.panes[0].move_cursor((0, 0))
            app.action_push_right()
            await pilot.pause()
            assert app.views[0].comparison.lines[1][0] == "a"
            assert app.views[1].comparison.lines[1][0] == "y"

            app.query_one(TabbedContent).active = "tab1"
            await pilot.pause()
            assert app.view is app.views[1]
            app.panes[0].move_cursor((0, 0))
            app.action_push_right()
            await pilot.pause()
            assert app.views[1].comparison.lines[1][0] == "x"

    run(scenario())


def test_tab_labels_shorten_and_mark_dirty(pair):
    # both files share a parent dir, so labels reduce to basenames
    # (the "[dir]" indicator only appears when basenames collide —
    # covered by the shorten_names unit tests above)
    files1 = pair(["a"], ["b"], subdir="left")
    files2 = pair(["x"], ["y"], subdir="right")

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            tabs = app.query_one(TabbedContent)
            assert str(tabs.get_tab("tab0").label) == "a.txt — b.txt ✕"
            app.panes[0].focus()
            await pilot.pause()
            await pilot.press("Z")
            await pilot.pause()
            assert str(tabs.get_tab("tab0").label) == "a.txt* — b.txt ✕"

    run(scenario())


def test_close_tab(pair):
    files1 = pair(["a"], ["b"])
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_close_tab()
            await pilot.pause()
            assert app.view is app.views[1]
            assert len(app.query(FileDiffView)) == 1
            # closing the last tab exits the app
            app.action_close_tab()
            await pilot.pause()
            assert app.return_value is None and not app.is_running

    run(scenario())


def test_close_dirty_tab_needs_confirmation(pair):
    files1 = pair(["a"], ["b"])
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.panes[0].focus()
            await pilot.pause()
            await pilot.press("Z")
            await pilot.pause()
            assert any(app.view.dirty)
            app.action_close_tab()
            await pilot.pause()
            # still open: first Ctrl+W only warns
            assert len(app.query(FileDiffView)) == 2
            app.action_close_tab()
            await pilot.pause()
            assert len(app.query(FileDiffView)) == 1

    run(scenario())


def test_exit_status_counts_closed_merge_tabs(pair, tmp_path):
    # a 3-way merge tab closed without saving must fail the mergetool
    # contract even though another tab stays open
    three = []
    for name, text in (("local", "L"), ("base", "B"), ("remote", "R")):
        p = tmp_path / f"{name}.txt"
        p.write_text(text + "\n", encoding="utf-8")
        three.append(str(p))
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(three, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.exit_status() == 1
            app.action_close_tab()  # close the unsaved merge tab
            await pilot.pause()
            assert app.exit_status() == 1
            return app

    app = run(scenario())
    assert app.exit_status() == 1


def test_exit_status_ok_after_middle_save(pair, tmp_path):
    three = []
    for name, text in (("local", "L"), ("base", "B"), ("remote", "R")):
        p = tmp_path / f"{name}.txt"
        p.write_text(text + "\n", encoding="utf-8")
        three.append(str(p))
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(three, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.save_pane(1)
            await pilot.pause()
            assert app.exit_status() == 0

    run(scenario())


def test_close_button_action_closes_background_tab(pair):
    files1 = pair(["a"], ["b"])
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.view is app.views[0]
            # the ✕ of the inactive second tab closes it without
            # switching the active view
            app.action_close_tab_by_id("tab1")
            await pilot.pause()
            assert len(app.query(FileDiffView)) == 1
            assert app.view is app.views[0]

    run(scenario())


def test_close_button_respects_dirty_confirmation(pair):
    files1 = pair(["a"], ["b"])
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test() as pilot:
            await pilot.pause()
            app.panes[0].focus()
            await pilot.pause()
            await pilot.press("Z")
            await pilot.pause()
            app.action_close_tab_by_id("tab0")
            await pilot.pause()
            assert len(app.query(FileDiffView)) == 2  # warned, not closed
            app.action_close_tab_by_id("tab0")
            await pilot.pause()
            assert len(app.query(FileDiffView)) == 1

    run(scenario())


def test_clicking_close_button_closes_without_crash(pair):
    """Regression: the ✕ @click removes the tab while the Tab.Clicked
    event is still queued; unpatched, Textual's activation then raises
    on the stale tab id (see TmeldApp._harden_tab_activation)."""
    files1 = pair(["a"], ["b"])
    files2 = pair(["x"], ["y"])

    async def scenario():
        app = TmeldApp(diffs=[(files1, None), (files2, None)])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            # scan the tab row until the ✕ is hit (label widths vary)
            closed_at = None
            for x in range(0, 40):
                await pilot.click(Tabs, offset=(x, 0))
                await pilot.pause()
                if len(app.query(FileDiffView)) == 1:
                    closed_at = x
                    break
            assert closed_at is not None, "no click position closed the tab"
            # app survived the click (run_test re-raises app crashes)
            assert app.is_running

    run(scenario())


def test_tab_strip_overflow_arrows(pair):
    # eight tabs in 60 columns cannot fit; Meld-style shift arrows
    # appear top-right and scroll the strip
    diffs = [(pair([f"l{i}"], [f"r{i}"]), None) for i in range(8)]

    async def scenario():
        from tmeld.app import TabArrows

        app = TmeldApp(diffs=diffs)
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            arrows = app.query_one(TabArrows)
            assert arrows.display
            inner = app.query_one(Tabs).query_one("#tabs-scroll")
            assert inner.scroll_offset.x == 0
            app.action_scroll_tabs_right()
            await pilot.pause()
            assert inner.scroll_offset.x > 0
            app.action_scroll_tabs_left()
            await pilot.pause()
            assert inner.scroll_offset.x == 0

    run(scenario())


def test_tab_arrows_hidden_when_tabs_fit(pair):
    diffs = [(pair([f"l{i}"], [f"r{i}"]), None) for i in range(2)]

    async def scenario():
        from tmeld.app import TabArrows

        app = TmeldApp(diffs=diffs)
        async with app.run_test(size=(120, 20)) as pilot:
            await pilot.pause()
            assert not app.query_one(TabArrows).display

    run(scenario())


def test_active_tab_background_highlight(pair):
    diffs = [(pair([f"l{i}"], [f"r{i}"]), None) for i in range(3)]

    async def scenario():
        app = TmeldApp(diffs=diffs)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            svg = app.export_screenshot()
            assert "#3d3d3d" in svg  # active tab background

    run(scenario())
