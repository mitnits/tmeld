"""Line numbers are off by default, as in Meld's default settings.

Nothing is lost: the status bar carries the cursor position, the way Meld's
does ("Ln {line}, Col {column}", ui/statusbar.py).
"""

import asyncio
import json

import pytest

from tmeld.app import TmeldApp
from tmeld.comparisonview import ComparisonView

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from tmeld.web.server import make_app, make_session  # noqa: E402

TOKEN = "tok"


@pytest.fixture
def paths(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("keep\nold\n", encoding="utf-8")
    b.write_text("keep\nnew\n", encoding="utf-8")
    return [str(a), str(b)]


def run(coro):
    return asyncio.run(coro)


def test_tui_hides_line_numbers_by_default(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            assert all(not p.show_line_numbers for p in app.views[0].panes)

    run(scenario())


def test_tui_flag_shows_line_numbers(paths):
    async def scenario():
        app = TmeldApp(paths, show_line_numbers=True)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            assert all(p.show_line_numbers for p in app.views[0].panes)

    run(scenario())


def test_tui_flag_reaches_tabs_opened_from_a_tree(paths):
    """A file tab opened from a folder/VC view must honour the flag too."""
    async def scenario():
        app = TmeldApp(paths, show_line_numbers=True)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            app.post_message(ComparisonView.OpenComparison(list(paths)))
            await pilot.pause()
            await pilot.pause()
            assert all(p.show_line_numbers for p in app.views[-1].panes)

    run(scenario())


def test_status_bar_carries_the_cursor_position(paths):
    async def scenario():
        app = TmeldApp(paths)
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            view = app.views[0]
            assert view.status_text.endswith("Ln 1, Col 1")
            assert "1 changes" in view.status_text

            pane = view.panes[0]
            pane.focus()
            pane.move_cursor((1, 3))
            await pilot.pause()
            assert view.status_text.endswith("Ln 2, Col 4"), view.status_text

    run(scenario())


def test_bmeld_reports_line_number_setting(paths):
    async def scenario(flag):
        session = make_session(paths, line_numbers=flag)
        app = make_app(session, TOKEN)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = json.loads((await ws.receive()).data)
            assert init["type"] == "init"
            return init["line_numbers"]
        finally:
            await client.close()

    assert run(scenario(False)) is False
    assert run(scenario(True)) is True
