"""Read-only panes: Meld's gutter reclassification and the lock indicator.

Meld's ActionGutter._classify_change_actions: you may copy *out* of a
read-only pane, but never into one. When the target is read-only the button
becomes a delete, which removes the chunk from the source side instead.
"""

import asyncio

import pytest

from tmeld.app import TmeldApp
from tmeld.comparisonview import ComparisonView
from tmeld.gutter import ActionGutter
from tmeld.palette import THEMES


@pytest.fixture
def paths(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("keep\nold\ntail\n", encoding="utf-8")
    b.write_text("keep\nnew\nextra\ntail\n", encoding="utf-8")
    return [str(a), str(b)]


def run(coro):
    return asyncio.run(coro)


def _gutter(readonly):
    g = ActionGutter(THEMES["meld-base"])
    g.pane_pair = (0, 1)
    g.readonly = frozenset(readonly)
    return g


def test_action_classification_matches_meld():
    # nothing read-only: both directions copy
    g = _gutter(())
    assert g._action(0) == "replace" and g._action(1) == "replace"

    # right pane read-only: can't copy into it, so ◀ becomes ✕ on the right
    # column (deleting from its source, the right pane)
    g = _gutter({1})
    assert g._action(0) == "delete", "▶ would write into the read-only pane"
    assert g._action(1) == "replace", "copying out of read-only is allowed"

    # left pane read-only: mirror image
    g = _gutter({0})
    assert g._action(0) == "replace"
    assert g._action(1) == "delete"

    # both read-only: no action at all
    g = _gutter({0, 1})
    assert g._action(0) is None and g._action(1) is None


def _open_readonly(app, paths, readonly):
    app.post_message(ComparisonView.OpenComparison(list(paths), readonly=readonly))


def test_gutter_draws_delete_glyph_toward_readonly_pane(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            _open_readonly(app, paths, readonly=(0,))
            await pilot.pause()
            await pilot.pause()
            view = app.views[-1]
            assert view.readonly == (0,)
            gutter = view.gutters[0]
            assert gutter.readonly == frozenset({0})

            glyphs = set()
            for y in range(gutter.size.height):
                strip = gutter.render_line(y)
                for seg in strip:
                    glyphs.add(seg.text)
            # ▶ copies out of the read-only left pane: still offered.
            # ◀ would write into it: replaced by ✕.
            assert ActionGutter.DELETE in glyphs, glyphs
            assert "◀" not in glyphs, glyphs
            assert "▶" in glyphs, glyphs

    run(scenario())


def test_readonly_pane_shows_a_lock_and_never_takes_a_push(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            _open_readonly(app, paths, readonly=(0,))
            await pilot.pause()
            await pilot.pause()
            view = app.views[-1]

            assert view.panes[0].read_only is True
            assert "🔒" in view.panes[0].border_title
            assert "🔒" not in view.panes[1].border_title

            before = view.panes[0].text
            # pushing right->left targets the read-only pane: refused
            view._push_chunk(1, 0, 0)
            await pilot.pause()
            assert view.panes[0].text == before
            assert view.dirty[0] is False

            # the ✕ deletes from the editable source instead
            view._delete_chunk(1, 0)
            await pilot.pause()
            assert view.panes[0].text == before, "read-only pane untouched"
            assert view.dirty[1] is True

    run(scenario())


def test_delete_chunk_refuses_to_touch_a_readonly_source(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            _open_readonly(app, paths, readonly=(0,))
            await pilot.pause()
            await pilot.pause()
            view = app.views[-1]
            before = view.panes[0].text
            view._delete_chunk(0, 0)  # source itself is read-only
            await pilot.pause()
            assert view.panes[0].text == before
            assert view.dirty[0] is False

    run(scenario())
