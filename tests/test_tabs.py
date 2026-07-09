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
        ["a", "b", "c", "d"],  # too many positionals
        ["--diff", "a", "b", "c", "d"],  # long --diff group
        ["-o", "out", "--diff", "a", "b"],  # -o without positional 3-way
        ["-o", "out", "a", "b"],  # -o with 2-way
    ],
)
def test_cli_rejects(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_cli_rejects_single_path_outside_vc(tmp_path):
    # a single path means the VC view; outside any repo it's an error
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    with pytest.raises(SystemExit) as exc:
        main([str(lonely)])
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
            # the slanted caps bracket the label (they replace Tab's padding)
            assert str(tabs.get_tab("tab0").label) == "◢a.txt — b.txt ✕◣"
            app.panes[0].focus()
            await pilot.pause()
            await pilot.press("Z")
            await pilot.pause()
            assert str(tabs.get_tab("tab0").label) == "◢a.txt* — b.txt ✕◣"

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


def test_tab_colours_follow_the_palette(pair):
    """Active tab = the page you are looking at; inactive = a step away from it.

    The bar itself is the gutter's grey. Asserted as relationships rather than
    hexes so both themes stay honest.
    """
    from textual.color import Color
    from textual.widgets import Tab

    diffs = [(pair([f"l{i}"], [f"r{i}"]), None) for i in range(3)]

    async def scenario(theme_name):
        app = TmeldApp(diffs=diffs, theme_name=theme_name)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            theme = app.theme_def
            tabs = list(app.query(Tab))
            active = [t for t in tabs if t.has_class("-active")]
            inactive = [t for t in tabs if not t.has_class("-active")]
            assert active and inactive

            page = Color.parse(theme.page_bg)
            assert active[0].styles.background == page
            assert inactive[0].styles.background == Color.parse(theme.tab_inactive_bg)
            # ...and the two are actually distinguishable
            assert active[0].styles.background != inactive[0].styles.background
            # the strip behind them is pushed away from the page, not the
            # gutter's grey (which sat 1.14:1 from the active tab)
            strip = app.query_one("ContentTabs")
            assert strip.styles.background == Color.parse(theme.tab_bar_bg)
            assert theme.tab_bar_bg != theme.gutter_bg

    for name in ("meld-base", "meld-dark"):
        run(scenario(name))


def test_underline_is_gone_and_its_row_reclaimed(pair):
    """The active tab's colour says enough; the Underline widget cost a row."""
    from textual.widgets._tabs import Underline

    diffs = [(pair([f"l{i}"], [f"r{i}"]), None) for i in range(2)]

    async def scenario():
        app = TmeldApp(diffs=diffs)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            for underline in app.query(Underline):
                assert underline.display is False
            strip = app.query_one("ContentTabs")
            assert strip.size.height == 1, "tab strip should be one row"

    run(scenario())


def test_tab_caps_are_the_tab_colour_on_the_bar(pair):
    """A cap cell is bar-coloured with a triangle of the tab's own colour, so
    the pair reads as the sloping edge of the sheet. Re-tinted on switch."""
    from textual.widgets import TabbedContent

    diffs = [(pair([f"l{i}"], [f"r{i}"]), None) for i in range(2)]

    def caps(app):
        row = app.screen._compositor.render_strips()[0]
        return [(s.text, s.style.color.name, s.style.bgcolor.name)
                for s in row if s.text in ("◢", "◣")]

    async def scenario(theme_name):
        app = TmeldApp(diffs=diffs, theme_name=theme_name)
        async with app.run_test(size=(80, 8)) as pilot:
            await pilot.pause()
            theme = app.theme_def
            bar = theme.tab_bar_bg
            page = theme.page_bg
            dull = theme.tab_inactive_bg

            got = caps(app)
            assert len(got) == 4, got
            assert all(bg == bar for _, _, bg in got), "caps must sit on the bar"
            # first tab active: its caps carry the page colour
            assert [fg for _, fg, _ in got] == [page, page, dull, dull]

            app.query_one(TabbedContent).active = "tab1"
            await pilot.pause()
            await pilot.pause()
            got = caps(app)
            assert [fg for _, fg, _ in got] == [dull, dull, page, page], \
                "caps did not follow the active tab"

    for name in ("meld-base", "meld-dark"):
        run(scenario(name))


def test_caps_cost_no_width():
    """They occupy the cells Tab's `padding: 0 1` used, not extra ones."""
    from rich.cells import cell_len
    from tmeld.app import TmeldApp

    assert cell_len(TmeldApp.TAB_CAP_LEFT) == 1
    assert cell_len(TmeldApp.TAB_CAP_RIGHT) == 1
    css = TmeldApp.CSS
    tab_rule = css[css.index("    Tab {"):css.index("    Tab:hover")]
    assert "padding: 0;" in tab_rule, tab_rule


def test_tab_ladder_meets_wcag_non_text_contrast():
    """The three surfaces must be tellable apart, in both themes.

    WCAG 1.4.11 asks 3:1 for non-text UI. The old ladder (gutter grey behind
    near-white tabs) gave 1.14:1, so two inactive tabs looked identical.
    """
    from tmeld.palette import THEMES

    for name, theme in THEMES.items():
        bar, inactive = theme.tab_bar_bg, theme.tab_inactive_bg
        active = theme.page_bg
        pairs = {
            "active/bar": theme.contrast(active, bar),
            "inactive/bar": theme.contrast(inactive, bar),   # separates the caps
            "active/inactive": theme.contrast(active, inactive),
        }
        for label, ratio in pairs.items():
            assert ratio >= 3.0, f"{name} {label} = {ratio:.2f}:1"


def test_tab_text_is_legible_on_every_surface():
    """The scheme's own fg is 1.23:1 on solarized's mid-grey inactive tab."""
    from tmeld.palette import THEMES

    for name, theme in THEMES.items():
        for surface in (theme.page_bg, theme.tab_inactive_bg, theme.tab_bar_bg):
            fg = theme.readable_on(surface)
            ratio = theme.contrast(fg, surface)
            assert ratio >= 4.5, f"{name}: {fg} on {surface} = {ratio:.2f}:1"


def test_dark_theme_bar_goes_light():
    """A near-black page has no headroom below it: the bar must lift instead."""
    from tmeld.palette import THEMES

    light, dark = THEMES["meld-base"], THEMES["meld-dark"]
    lum = light._relative_luminance
    assert lum(light.tab_bar_bg) < lum(light.page_bg), "light theme: bar darker"
    assert lum(dark.tab_bar_bg) > lum(dark.page_bg), "dark theme: bar lighter"


def test_lone_comparison_gets_a_corner_close_button(pair):
    """Meld's AdwTabBar autohides at one page, and so does ours -- but then the
    per-tab ✕ goes with it. Exactly one close affordance in each state."""
    from tmeld.app import CloseButton

    async def scenario():
        app = TmeldApp(pair(["a"], ["b"]))
        async with app.run_test(size=(80, 10)) as pilot:
            await pilot.pause()
            await pilot.pause()
            assert not app.query_one(Tabs).display, "tab strip should be hidden"
            assert app.query_one(CloseButton).display, "no way to close by mouse"

    run(scenario())


def test_corner_button_hides_when_the_tab_strip_appears(pair):
    from tmeld.app import CloseButton

    async def scenario():
        app = TmeldApp(diffs=[(pair(["a"], ["b"]), None), (pair(["x"], ["y"]), None)])
        async with app.run_test(size=(80, 10)) as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app.query_one(Tabs).display
            assert not app.query_one(CloseButton).display, "two ✕ for one tab"

            app.action_close_tab()          # down to one
            await pilot.pause()
            await pilot.pause()
            assert not app.query_one(Tabs).display
            assert app.query_one(CloseButton).display

    run(scenario())


def test_corner_button_closes_the_last_tab_and_quits(pair):
    """Meld quits when its last comparison closes; so does the ✕."""
    from tmeld.app import CloseButton

    async def scenario():
        app = TmeldApp(pair(["a"], ["b"]))
        async with app.run_test(size=(80, 10)) as pilot:
            await pilot.pause()
            assert app.query_one(CloseButton).display
            await pilot.click("#close-button")   # the mouse path, not the action
            await pilot.pause()
            await pilot.pause()
            assert not app.is_running, "clicking ✕ on the last tab must quit"

    run(scenario())


def test_corner_button_clears_the_overview_strips(pair, tmp_path):
    """Chunkmaps have no border row: their top cell is chunk data, not chrome."""
    from tmeld.app import CloseButton

    async def scenario(paths, expected_strips):
        app = TmeldApp(paths)
        async with app.run_test(size=(90, 10)) as pilot:
            await pilot.pause()
            await pilot.pause()
            strips = len(getattr(app.view, "chunkmaps", ()))
            assert strips == expected_strips
            # offset is a Scalar in cells, not a bare int
            assert app.query_one(CloseButton).styles.offset.x.value == -strips

    run(scenario(pair(["a"], ["b"]), 2))

    three = []
    for name in ("local", "base", "remote"):
        p = tmp_path / f"{name}.txt"
        p.write_text(f"{name}\n", encoding="utf-8")
        three.append(str(p))
    run(scenario(three, 3))

    left, right = tmp_path / "dl", tmp_path / "dr"
    for d in (left, right):
        d.mkdir()
        (d / "f.txt").write_text("x\n", encoding="utf-8")
    run(scenario([str(left), str(right)], 0))   # folder view: no strips


def test_escape_quits_when_clean(pair):
    async def scenario():
        app = TmeldApp(pair(["a"], ["b"]))
        async with app.run_test(size=(80, 10)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not app.is_running

    run(scenario())


def test_escape_warns_before_discarding_unsaved_edits(pair):
    """Esc must not silently drop work: first press warns, second quits."""
    async def scenario():
        app = TmeldApp(pair(["a"], ["b"]))
        async with app.run_test(size=(80, 10)) as pilot:
            await pilot.pause()
            app.panes[0].focus()
            await pilot.pause()
            await pilot.press("X")               # dirty the buffer
            await pilot.pause()
            assert any(app.views[0].dirty)

            await pilot.press("escape")          # first: warn only
            await pilot.pause()
            assert app.is_running, "one Esc discarded unsaved edits"
            assert app._quit_pending

            await pilot.press("escape")          # second: quit
            await pilot.pause()
            assert not app.is_running

    run(scenario())


def test_escape_quit_keeps_the_mergetool_exit_code(pair, tmp_path):
    """An abandoned 3-way merge must still fail (exit 1)."""
    three = []
    for name in ("local", "base", "remote"):
        p = tmp_path / name
        p.write_text(f"{name}\n", encoding="utf-8")
        three.append(str(p))

    async def scenario():
        app = TmeldApp([three[0], three[1], three[2]])
        async with app.run_test(size=(90, 12)) as pilot:
            await pilot.pause()
            await pilot.press("escape")          # clean tree -> quits at once
            await pilot.pause()
            return app

    app = run(scenario())
    assert app.exit_status() == 1


def test_escape_cancels_a_modal_instead_of_quitting(tmp_path):
    """The commit dialog binds Esc; the app binding is non-priority so the
    modal wins. Esc there must cancel the dialog, not exit tmeld."""
    import subprocess
    from textual.screen import ModalScreen

    repo = tmp_path / "repo"
    repo.mkdir()
    sh = lambda *c: subprocess.run(c, cwd=repo, check=True, capture_output=True)
    sh("git", "init", "-q")
    sh("git", "config", "user.email", "t@t")
    sh("git", "config", "user.name", "t")
    (repo / "f").write_text("x\n", encoding="utf-8")
    sh("git", "add", "-A")
    sh("git", "commit", "-qm", "init")
    (repo / "f").write_text("y\n", encoding="utf-8")

    async def scenario():
        app = TmeldApp([str(repo)])
        async with app.run_test(size=(80, 14)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("c")               # open the commit modal
            await pilot.pause()
            assert isinstance(app.screen, ModalScreen), "commit dialog didn't open"

            await pilot.press("escape")
            await pilot.pause()
            assert app.is_running, "Esc quit the app instead of the dialog"
            assert not isinstance(app.screen, ModalScreen), "dialog still open"

    run(scenario())
