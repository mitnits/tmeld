"""DirDiffView tests: rendering, states on screen, nav, open-tab, copy,
delete, CLI routing."""

import asyncio

import pytest

from tmeld.app import TmeldApp, main, make_view
from tmeld.dirdiff import DirDiffView, DirTree
from tmeld.filediff import FileDiffView
from tmeld.palette import MELD_BASE


@pytest.fixture
def dirs(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "same.txt").write_text("hello\n")
    (right / "same.txt").write_text("hello\n")
    (left / "changed.txt").write_text("aaa\n")
    (right / "changed.txt").write_text("bbb\n")
    (left / "left-only.txt").write_text("mine\n")
    return str(left), str(right)


def run(coro):
    return asyncio.run(coro)


async def scanned(app):
    """Wait for the scan worker and the resulting UI messages."""
    await app.workers.wait_for_complete()


def row_index(tree: DirTree, name: str) -> int:
    for i, (entry, _depth, _key) in enumerate(tree.rows):
        if entry.names[0] == name:
            return i
    raise AssertionError(f"row {name!r} not visible in tree")


def test_dir_view_scans_and_reports(dirs):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            view = app.view
            assert isinstance(view, DirDiffView)
            assert view.status_text == "2 differences"
            tree = view.dirtree
            names = [e.names[0] for e, _d, _k in tree.rows]
            assert names[0] == "left"  # root row
            assert "changed.txt" in names and "left-only.txt" in names

    run(scenario())


def test_state_colors_on_screen(dirs):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test(size=(100, 30)) as pilot:
            await scanned(app)
            await pilot.pause()
            svg = app.export_screenshot()
            insert_fg = MELD_BASE.chunk["insert"].fg.lstrip("#").upper()
            replace_fg = MELD_BASE.chunk["replace"].fg.lstrip("#").upper()
            # left-only.txt (NEW, both columns share the row state) and
            # changed.txt (MODIFIED) must paint in Meld's exact colors
            assert svg.upper().count(insert_fg) >= 1
            assert svg.upper().count(replace_fg) >= 1

    run(scenario())


def test_next_chunk_moves_to_differences(dirs):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            tree = app.view.dirtree
            assert tree.cursor == 0
            app.action_next_chunk()
            first = tree.rows[tree.cursor][0]
            assert first.different
            app.action_next_chunk()
            second = tree.rows[tree.cursor][0]
            assert second.different and second is not first
            # past the last difference: cursor stays put
            app.action_next_chunk()
            assert tree.rows[tree.cursor][0] is second
            app.action_previous_chunk()
            assert tree.rows[tree.cursor][0] is first

    run(scenario())


def test_enter_on_file_row_opens_comparison_tab(dirs):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            tree = app.view.dirtree
            tree._move_cursor(row_index(tree, "changed.txt"))
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.view, FileDiffView)
            assert len(app.views) == 2
            assert app.view.comparison.differ.diff_count() == 1
            # Ctrl+W returns to the folder tab
            app.action_close_tab()
            await pilot.pause()
            assert isinstance(app.view, DirDiffView)

    run(scenario())


def test_enter_on_one_sided_row_bells(dirs):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            tree = app.view.dirtree
            tree._move_cursor(row_index(tree, "left-only.txt"))
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.views) == 1  # no tab opened

    run(scenario())


def test_copy_right(dirs, tmp_path):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            tree = app.view.dirtree
            tree._move_cursor(row_index(tree, "left-only.txt"))
            assert tree.focus_pane == 0
            app.action_push_right()
            await scanned(app)  # rescan worker
            await pilot.pause()
            assert (tmp_path / "right" / "left-only.txt").read_text() == "mine\n"
            assert app.view.status_text == "1 differences"

    run(scenario())


def test_delete_needs_confirmation(dirs, tmp_path):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test() as pilot:
            await scanned(app)
            await pilot.pause()
            tree = app.view.dirtree
            tree._move_cursor(row_index(tree, "left-only.txt"))
            await pilot.press("delete")
            await pilot.pause()
            assert (tmp_path / "left" / "left-only.txt").exists()
            await pilot.press("delete")
            await scanned(app)
            await pilot.pause()
            assert not (tmp_path / "left" / "left-only.txt").exists()
            assert app.view.status_text == "1 differences"

    run(scenario())


def test_dir_tab_label(dirs):
    async def scenario():
        app = TmeldApp(list(dirs))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.view.tab_label == "left — right"

    run(scenario())


def test_mixed_file_and_dir_rejected(dirs, tmp_path):
    afile = tmp_path / "f.txt"
    afile.write_text("x\n")
    with pytest.raises(ValueError):
        make_view([dirs[0], str(afile)], MELD_BASE)
    with pytest.raises(SystemExit) as exc:
        main([dirs[0], str(afile)])
    assert exc.value.code == 2


def test_output_with_dirs_rejected(dirs, tmp_path):
    third = tmp_path / "third"
    third.mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["-o", "out", dirs[0], dirs[1], str(third)])
    assert exc.value.code == 2
