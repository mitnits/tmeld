"""Alt+Z / Alt+X / Alt+C alias undo / cut / copy.

Terminals hand Ctrl+C to job control more often than to the application, so the
Alt trio gives the edit actions a reliable home. None of them collide with
Meld's keymap.
"""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.comparisonview import ComparisonView


@pytest.fixture
def paths(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha\nbravo\ncharlie\n", encoding="utf-8")
    b.write_text("alpha\nBRAVO\ncharlie\n", encoding="utf-8")
    return [str(a), str(b)]


def run(coro):
    return asyncio.run(coro)


def test_alt_bindings_do_not_collide_with_melds_keymap():
    from tmeld.app import TmeldApp
    from tmeld.filediff import FileDiffView
    from tmeld.pane import DiffPane

    def keys(cls):
        out = set()
        for b in cls.BINDINGS:
            for key in getattr(b, "key", b).split(","):
                out.add(key.strip())
        return out

    new = {"alt+z", "alt+x", "alt+c"}
    assert new <= keys(DiffPane)
    assert not (new & keys(TmeldApp)), "shadowed by a window-level binding"
    assert not (new & keys(FileDiffView)), "shadowed by the view"


def test_alt_z_undoes(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            pane = app.views[0].panes[0]
            pane.focus()
            pane.move_cursor((0, 0))
            await pilot.press("X")
            await pilot.pause()
            assert pane.text.startswith("Xalpha")

            await pilot.press("alt+z")
            await pilot.pause()
            assert pane.text.startswith("alpha"), pane.text[:20]

    run(scenario())


def test_alt_c_copies_and_alt_x_cuts(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            pane = app.views[0].panes[0]
            pane.focus()
            pane.selection = ((0, 0), (0, 5))   # "alpha"
            await pilot.pause()

            await pilot.press("alt+c")
            await pilot.pause()
            assert app.clipboard == "alpha", repr(app.clipboard)
            assert pane.text.startswith("alpha"), "copy must not modify"

            await pilot.press("alt+x")
            await pilot.pause()
            assert app.clipboard == "alpha"
            assert pane.text.startswith("\nbravo"), repr(pane.text[:12])

    run(scenario())


def test_alt_x_refuses_a_readonly_pane(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            app.post_message(ComparisonView.OpenComparison(list(paths), readonly=(0,)))
            await pilot.pause()
            await pilot.pause()
            pane = app.views[-1].panes[0]
            pane.focus()
            pane.selection = ((0, 0), (0, 5))
            before = pane.text
            await pilot.press("alt+x")
            await pilot.pause()
            assert pane.text == before, "cut wrote into a read-only pane"

    run(scenario())
