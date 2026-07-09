"""/dev/null as the absent side of an add/delete (p4, git difftool).

Two things must hold: the empty side is not falsely reported as modified, and
the /dev/null pane is read-only -- there is nothing to save there.
"""

import asyncio
import os

import pytest

from tmeld.app import TmeldApp
from tmeld.comparison import Comparison, devnull_panes, is_devnull


@pytest.fixture
def added(tmp_path):
    """A file that exists only on the right -- p4 passes /dev/null on the left."""
    f = tmp_path / "new.py"
    f.write_text("def added():\n    return 1\n", encoding="utf-8")
    return ["/dev/null", str(f)]


def run(coro):
    return asyncio.run(coro)


def test_is_devnull_detects_the_null_device():
    assert is_devnull("/dev/null")
    assert is_devnull(os.devnull)
    assert not is_devnull("/tmp/not-null")


def test_devnull_panes_indices(tmp_path):
    f = str(tmp_path / "a")
    open(f, "w").close()
    assert devnull_panes(["/dev/null", f]) == frozenset({0})
    assert devnull_panes([f, "/dev/null", f]) == frozenset({1})
    assert devnull_panes([f, f]) == frozenset()


def test_empty_side_is_not_falsely_modified(added):
    """The core bug: '' .splitlines() is [] but ''.split(chr(10)) is [''], so a
    naive dirty check flagged every empty pane."""
    async def scenario():
        app = TmeldApp(added)
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause()
            view = app.views[0]
            assert view.comparison.lines[0] == []       # /dev/null is empty
            assert view.dirty == [False, False], "empty side reads as modified"

    run(scenario())


def test_empty_real_file_is_not_dirty_either(tmp_path):
    """Same class of bug, without /dev/null: an ordinary empty file."""
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    other = tmp_path / "other"
    other.write_text("x\n", encoding="utf-8")

    async def scenario():
        app = TmeldApp([str(empty), str(other)])
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause()
            assert app.views[0].dirty == [False, False]

    run(scenario())


def test_devnull_pane_is_read_only(added):
    async def scenario():
        app = TmeldApp(added)
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause()
            view = app.views[0]
            assert view.readonly == (0,)
            assert view.panes[0].read_only is True
            assert view.panes[1].read_only is False
            assert "🔒" in view.panes[0].border_title

            # typing into it must not dirty it
            view.panes[0].focus()
            await pilot.pause()
            await pilot.press("Z")
            await pilot.pause()
            assert view.dirty == [False, False]

    run(scenario())


def test_devnull_is_read_only_on_the_right_too(tmp_path):
    """A delete: the file exists on the left, /dev/null on the right."""
    f = tmp_path / "gone.py"
    f.write_text("removed\n", encoding="utf-8")

    async def scenario():
        app = TmeldApp([str(f), "/dev/null"])
        async with app.run_test(size=(80, 12)) as pilot:
            await pilot.pause()
            view = app.views[0]
            assert view.readonly == (1,)
            assert view.panes[1].read_only is True
            assert view.dirty == [False, False]

    run(scenario())


def test_bmeld_payload_marks_devnull_read_only(tmp_path):
    from tmeld.web.protocol import file_tab_payload

    f = tmp_path / "new.py"
    f.write_text("x\n", encoding="utf-8")
    payload = file_tab_payload("t0", Comparison(["/dev/null", str(f)]))
    assert payload["readonly"] == [0]

    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    # an empty *real* file is editable, unlike /dev/null
    assert file_tab_payload("t1", Comparison([str(empty), str(f)]))["readonly"] == []
