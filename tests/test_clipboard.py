"""Clipboard round-trip: ctrl+c / ctrl+x / ctrl+v / ctrl+z in the panes."""

import asyncio

import pytest

from tmeld.app import TmeldApp


@pytest.fixture
def paths(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
    b.write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
    return [str(a), str(b)]


def run(coro):
    return asyncio.run(coro)


def test_copy_and_paste_within_app(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            await pilot.pause()
            pane.select_line(1)  # "bravo"
            await pilot.press("ctrl+c")
            clipboard = app.clipboard
            # Paste into the other pane at the end of line 2
            other = app.panes[1]
            other.focus()
            await pilot.pause()
            other.move_cursor((2, 7))
            await pilot.press("ctrl+v")
            await pilot.pause()
            return clipboard, other.text

    clipboard, text = run(scenario())
    assert clipboard == "bravo"
    assert "charliebravo" in text


def test_cut_removes_and_copies(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            await pilot.pause()
            pane.select_line(1)
            await pilot.press("ctrl+x")
            await pilot.pause()
            return app.clipboard, pane.text

    clipboard, text = run(scenario())
    assert clipboard == "bravo"
    assert "bravo" not in text


def test_undo_restores_after_cut(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            await pilot.pause()
            pane.select_line(1)
            await pilot.press("ctrl+x")
            await pilot.press("ctrl+z")
            await pilot.pause()
            return pane.text

    assert "bravo" in run(scenario())


def test_meld_nav_keys_still_win_over_textarea_defaults(paths):
    # Ctrl+D must be next-change (Meld), not TextArea's delete_right
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = app.panes[0]
            pane.focus()
            await pilot.pause()
            before = pane.text
            await pilot.press("ctrl+d")  # identical files: bell, no delete
            await pilot.pause()
            return before == pane.text

    assert run(scenario()) is True
