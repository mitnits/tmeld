"""Phase 5 tests: three-way merge (engine wiring, actions, mergetool)."""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.comparison import Comparison
from tmeld.gutter import ActionGutter


@pytest.fixture
def paths3(tmp_path):
    def make(local, base, remote):
        files = []
        for name, lines in (("local", local), ("base", base), ("remote", remote)):
            p = tmp_path / f"{name}.txt"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            files.append(str(p))
        return files

    return make


def run(coro):
    return asyncio.run(coro)


# --- Comparison model ---------------------------------------------------


def test_conflict_detected(paths3):
    files = paths3(["a", "LOCAL", "c"], ["a", "base", "c"], ["a", "REMOTE", "c"])
    comp = Comparison(files)
    assert comp.differ.diff_count() == 1
    assert comp.differ.conflicts == [0]
    assert comp.line_tags(1) == {1: "conflict"}


def test_same_change_both_sides_auto_merges(paths3):
    # Both outer panes made the identical edit: not a conflict
    files = paths3(["a", "SAME", "c"], ["a", "base", "c"], ["a", "SAME", "c"])
    comp = Comparison(files)
    assert comp.differ.conflicts == []
    assert comp.line_tags(1) == {1: "replace"}


def test_one_sided_change_tags_only_its_pair(paths3):
    files = paths3(["a", "LOCAL", "c"], ["a", "base", "c"], ["a", "base", "c"])
    comp = Comparison(files)
    assert comp.differ.conflicts == []
    assert comp.line_tags(0) == {1: "replace"}
    assert comp.line_tags(1) == {1: "replace"}
    assert comp.line_tags(2) == {}


def test_inline_ranges_cover_both_diffs(paths3):
    # One-sided replaces on different lines: conflicts get no inline
    # highlight in Meld (filediff.py:1962 filters on tag == "replace")
    files = paths3(
        ["abXdef", "mid", "qrstuv"],
        ["abcdef", "mid", "qrstuv"],
        ["abcdef", "mid", "qrsTuv"],
    )
    comp = Comparison(files)
    inline = comp.inline_ranges()
    assert 0 in inline[0]  # local vs base
    assert 2 in inline[2]  # remote vs base
    assert 0 in inline[1] and 2 in inline[1]  # base marks both


def test_action_starts_orientation(paths3):
    # Chunk between base and remote only: arrows belong to gutter 1
    files = paths3(["a", "b"], ["a", "b"], ["a", "REMOTE-NEW", "b"])
    comp = Comparison(files)
    left_gutter = comp.action_starts(0)
    right_gutter = comp.action_starts(1)
    assert left_gutter == [{}, {}]
    # Right gutter: remote (its right column) has lines to push at line 1
    assert list(right_gutter[1]) == [1]
    index, tag = right_gutter[1][1]
    assert index == 0
    assert comp.differ.get_chunk(index, 2, 1) is not None


def test_merge_all_non_conflicting(paths3):
    files = paths3(
        ["a", "LOCAL", "c", "d", "e"],
        ["a", "b", "c", "d", "e"],
        ["a", "b", "c", "REMOTE", "e"],
    )
    comp = Comparison(files)
    merged = comp.merge_all_non_conflicting()
    assert merged.split("\n") == ["a", "LOCAL", "c", "REMOTE", "e"]


def test_merge_all_keeps_conflicts_as_base(paths3):
    files = paths3(["a", "LOCAL", "c"], ["a", "base", "c"], ["a", "REMOTE", "c"])
    comp = Comparison(files)
    merged = comp.merge_all_non_conflicting()
    assert merged.split("\n") == ["a", "base", "c"]


def test_output_redirects_middle_save(paths3, tmp_path):
    files = paths3(["a", "x"], ["a", "x"], ["a", "x"])
    out = tmp_path / "merged.txt"
    comp = Comparison(files, output=str(out))
    comp.save(1)
    assert out.read_text(encoding="utf-8") == "a\nx\n"
    # The original middle file is untouched
    assert comp.paths[1] != str(out)


# --- App: layout, actions, navigation ------------------------------------


def test_three_panes_and_two_gutters(paths3):
    files = paths3(["a"], ["a"], ["a"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            return len(app.panes), len(list(app.query(ActionGutter).results()))

    panes, gutters = run(scenario())
    assert panes == 3
    assert gutters == 2


def test_push_from_local_into_middle(paths3):
    files = paths3(["a", "LOCAL", "c"], ["a", "base", "c"], ["a", "base", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            app.panes[0].focus()
            await pilot.pause()
            app.panes[0].move_cursor((1, 0))
            app.action_push_right()
            await pilot.pause()
            remaining = list(app.comparison.pair_chunks(1, 0))
            return remaining, app.panes[1].text

    remaining, text = run(scenario())
    assert text.split("\n") == ["a", "LOCAL", "c"]
    # Middle now matches local (it newly differs from remote, which is fine)
    assert remaining == []


def test_push_from_remote_into_middle(paths3):
    files = paths3(["a", "base", "c"], ["a", "base", "c"], ["a", "REMOTE", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            app.panes[2].focus()
            await pilot.pause()
            app.panes[2].move_cursor((1, 0))
            app.action_push_left()
            await pilot.pause()
            remaining = list(app.comparison.pair_chunks(1, 2))
            return remaining, app.panes[1].text

    remaining, text = run(scenario())
    assert text.split("\n") == ["a", "REMOTE", "c"]
    # Middle now matches remote (it newly differs from local, which is fine)
    assert remaining == []


def test_middle_pane_pulls_from_both_sides(paths3):
    files = paths3(
        ["a", "LOCAL", "c", "d", "e"],
        ["a", "b", "c", "d", "e"],
        ["a", "b", "c", "REMOTE", "e"],
    )

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            app.panes[1].focus()
            await pilot.pause()
            app.panes[1].move_cursor((1, 0))
            app.action_pull_left()  # Alt+Shift+Right: from left neighbor
            await pilot.pause()
            app.panes[1].move_cursor((3, 0))
            app.action_pull_right()  # Alt+Shift+Left: from right neighbor
            await pilot.pause()
            return app.panes[1].text

    text = run(scenario())
    assert text.split("\n") == ["a", "LOCAL", "c", "REMOTE", "e"]


def test_push_off_the_edge_bells(paths3):
    files = paths3(["a", "X", "c"], ["a", "b", "c"], ["a", "b", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            app.panes[0].focus()
            await pilot.pause()
            app.panes[0].move_cursor((1, 0))
            before = app.panes[0].text
            app.action_push_left()  # no pane to the left of pane 0
            await pilot.pause()
            texts = [p.text for p in app.panes]
            return before, texts

    before, texts = run(scenario())
    assert texts[0] == before
    assert texts[1].split("\n") == ["a", "b", "c"]


def test_second_gutter_click_pushes_between_middle_and_remote(paths3):
    files = paths3(["a", "b", "c"], ["a", "b", "c"], ["a", "REMOTE", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            gutter = app.gutters[1]
            # Chunk starts at doc line 1; +1 for the pane border row.
            # Right column (x=2) is the remote side, which has the lines.
            await pilot.click(gutter, offset=(2, 2))
            await pilot.pause()
            remaining = list(app.comparison.pair_chunks(1, 2))
            return remaining, app.panes[1].text

    remaining, text = run(scenario())
    assert text.split("\n") == ["a", "REMOTE", "c"]
    assert remaining == []


def test_merge_all_action(paths3):
    # NB: changes on adjacent lines coalesce into one (conflict) chunk —
    # the one-sided edits need an equal-line gap from the conflict
    files = paths3(
        ["a", "LOCAL", "c", "d", "e", "CONF-L", "g"],
        ["a", "b", "c", "d", "e", "base", "g"],
        ["a", "b", "c", "REMOTE-d", "e", "CONF-R", "g"],
    )

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            app.action_merge_all()
            await pilot.pause()
            return (
                app.panes[1].text,
                app.dirty[:],
                len(app.comparison.differ.conflicts),
            )

    text, dirty, conflicts = run(scenario())
    assert text.split("\n") == ["a", "LOCAL", "c", "REMOTE-d", "e", "base", "g"]
    assert dirty == [False, True, False]
    assert conflicts == 1  # the conflict survives, untouched


def test_conflict_navigation(paths3):
    local = [f"l{i}" for i in range(60)]
    base = list(local)
    remote = list(local)
    local[10] = "LOCAL-only"  # non-conflict chunk
    local[40] = "CONFLICT-L"
    remote[40] = "CONFLICT-R"
    files = paths3(local, base, remote)

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            assert len(app.comparison.differ.conflicts) == 1
            pane = app.panes[1]
            pane.focus()
            await pilot.pause()
            pane.move_cursor((0, 0))
            app.action_next_conflict()
            await pilot.pause()
            row_after_next = pane.cursor_location[0]
            pane.move_cursor((59, 0))
            app.action_previous_conflict()
            await pilot.pause()
            row_after_prev = pane.cursor_location[0]
            return row_after_next, row_after_prev

    row_next, row_prev = run(scenario())
    assert row_next == 40
    assert row_prev == 40


def test_three_way_sync_scroll_moves_all_panes(paths3):
    local = [f"line {i}" for i in range(200)]
    base = list(local)
    remote = list(local)
    local.insert(50, "extra local")
    remote.insert(150, "extra remote")
    files = paths3(local, base, remote)

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            app.panes[0].scroll_to(y=100, animate=False)
            await pilot.pause(0.1)
            return [p.scroll_offset.y for p in app.panes]

    y0, y1, y2 = run(scenario())
    assert y0 == 100
    # All panes land in the same document region (±2 for interpolation)
    assert abs(y1 - y0) <= 2
    assert abs(y2 - y0) <= 2


def test_conflict_colors_reach_the_screen(paths3):
    files = paths3(["a", "LOCAL", "c"], ["a", "base", "c"], ["a", "REMOTE", "c"])

    async def scenario():
        app = TmeldApp(files)
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            return app.export_screenshot().upper()

    svg = run(scenario())
    # meld:conflict fill (pale red) must appear in all three panes
    assert svg.count("FFA5A3") >= 3


# --- Mergetool contract ---------------------------------------------------


def test_exit_status_zero_only_after_middle_save(paths3, tmp_path):
    files = paths3(["a", "LOCAL", "c"], ["a", "base", "c"], ["a", "base", "c"])
    out = tmp_path / "merged.txt"

    async def scenario():
        app = TmeldApp(files, output=str(out))
        async with app.run_test(size=(140, 24)) as pilot:
            await pilot.pause()
            before = app.exit_status()
            app.panes[0].focus()
            await pilot.pause()
            app.panes[0].move_cursor((1, 0))
            app.action_push_right()
            await pilot.pause()
            app.save_pane(1)
            await pilot.pause()
            return before, app.exit_status()

    before, after = run(scenario())
    assert before == 1
    assert after == 0
    assert out.read_text(encoding="utf-8") == "a\nLOCAL\nc\n"


def test_two_way_exit_status_is_zero(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("x\n", encoding="utf-8")
    b.write_text("y\n", encoding="utf-8")

    async def scenario():
        app = TmeldApp([str(a), str(b)])
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            return app.exit_status()

    assert run(scenario()) == 0
