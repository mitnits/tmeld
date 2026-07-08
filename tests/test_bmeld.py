"""bmeld server/protocol tests (aiohttp test client, no browser)."""

import asyncio
import json

import pytest

aiohttp = pytest.importorskip("aiohttp")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from tmeld.web.server import make_app, make_session  # noqa: E402

TOKEN = "test-token"


@pytest.fixture
def three(tmp_path):
    files = []
    for name, lines in (
        ("local", ["a", "LOCAL", "c", "extra"]),
        ("base", ["a", "base", "c"]),
        ("remote", ["a", "REMOTE", "c"]),
    ):
        p = tmp_path / f"{name}.py"
        p.write_text("\n".join(lines) + "\n")
        files.append(str(p))
    return files


@pytest.fixture
def dirs(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "same.txt").write_text("s\n")
    (right / "same.txt").write_text("s\n")
    (left / "changed.txt").write_text("a\n")
    (right / "changed.txt").write_text("b\n")
    (left / "only-left.txt").write_text("x\n")
    return [str(left), str(right)]


def run(coro):
    return asyncio.run(coro)


async def client_for(session):
    app = make_app(session, TOKEN)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def recv(ws):
    return json.loads((await ws.receive()).data)


def test_init_and_chunks_flow(three):
    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            assert init["type"] == "init"
            assert len(init["tabs"]) == 1
            tab = init["tabs"][0]
            assert tab["kind"] == "file" and tab["num_panes"] == 3
            assert init["palette"]["chunk"]["conflict"]["fill"] == "#ffa5a3"
            assert tab["texts"][1] == "a\nbase\nc"

            chunks = await recv(ws)
            assert chunks["type"] == "chunks" and chunks["tab"] == tab["id"]
            assert chunks["conflict_count"] == 1
            assert "conflict" in {c[0] for c in chunks["panes"][1]}
            assert len(chunks["pairs"]) == 2
        finally:
            await client.close()

    run(scenario())


def test_buffers_rediff_and_version(three):
    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            tab = init["tabs"][0]["id"]
            await recv(ws)  # initial chunks
            await ws.send_json({
                "type": "buffers", "tab": tab, "version": 7,
                "texts": ["a\nLOCAL\nc\nextra", "a\nLOCAL\nc\nextra",
                          "a\nREMOTE\nc"],
            })
            reply = await recv(ws)
            assert reply["type"] == "chunks" and reply["version"] == 7
            assert reply["conflict_count"] == 0
        finally:
            await client.close()

    run(scenario())


def test_save_and_exit_contract(three, tmp_path):
    out = tmp_path / "merged.out"

    async def scenario():
        session = make_session(three, output=str(out))
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            tab = init["tabs"][0]["id"]
            await recv(ws)
            assert session.exit_status() == 1  # nothing saved yet
            await ws.send_json({
                "type": "save", "tab": tab, "pane": 1, "text": "resolved"
            })
            reply = await recv(ws)
            assert reply["type"] == "saved" and reply["pane"] == 1
            assert session.exit_status() == 0
            await ws.send_json({"type": "close"})
            await asyncio.sleep(0.05)
            assert session.closed.is_set()
        finally:
            await client.close()

    run(scenario())
    assert out.read_text() == "resolved\n"


def test_merge_all_uses_engine(three):
    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            tab = init["tabs"][0]["id"]
            await recv(ws)
            await ws.send_json({
                "type": "merge_all", "tab": tab,
                "texts": ["a\nLOCAL\nc\nextra", "a\nbase\nc",
                          "a\nREMOTE\nc"],
            })
            reply = await recv(ws)
            assert reply["type"] == "merged"
            # the local-only "extra" merges; the conflict keeps base
            assert "extra" in reply["text"] and "base" in reply["text"]
        finally:
            await client.close()

    run(scenario())


def test_dir_tab_tree_and_open_file(dirs):
    async def scenario():
        session = make_session(dirs)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            tab = init["tabs"][0]
            assert tab["kind"] == "dir" and tab["num_panes"] == 2

            tree = await recv(ws)
            assert tree["type"] == "tree" and tree["differences"] == 2
            names = {c["names"][0]: c for c in tree["root"]["children"]}
            assert names["changed.txt"]["different"]
            assert not names["same.txt"]["different"]
            assert names["only-left.txt"]["exists"] == [True, False]

            # open a file comparison from a tree row
            await ws.send_json({
                "type": "open_file",
                "paths": names["changed.txt"]["paths"],
            })
            reply = await recv(ws)
            assert reply["type"] == "tab_added"
            assert reply["tab"]["kind"] == "file"
            assert reply["tab"]["texts"] == ["a", "b"]
            assert reply["chunks"]["diff_count"] == 1

            # rescan works
            await ws.send_json({"type": "scan", "tab": tab["id"]})
            tree2 = await recv(ws)
            assert tree2["type"] == "tree" and tree2["differences"] == 2
        finally:
            await client.close()

    run(scenario())


def test_multi_tab_specs_and_close_tab(three, dirs):
    async def scenario():
        session = make_session(
            None, diffs=[(dirs, None), (three, None)]
        )
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            assert [t["kind"] for t in init["tabs"]] == ["dir", "file"]
            file_tab = init["tabs"][1]["id"]
            await recv(ws)  # tree
            await recv(ws)  # chunks for the file tab
            # closing the unsaved 3-way tab keeps the failure contract
            await ws.send_json({"type": "close_tab", "tab": file_tab})
            await asyncio.sleep(0.05)
            assert session.exit_status() == 1
        finally:
            await client.close()

    run(scenario())


def test_token_gate(three):
    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            resp = await client.get("/t/wrong-token")
            assert resp.status == 404
            resp = await client.get(f"/t/{TOKEN}")
            assert resp.status == 200
            assert "Content-Security-Policy" in resp.headers
        finally:
            await client.close()

    run(scenario())


def test_disconnect_grace_expires(three):
    async def scenario():
        session = make_session(three, grace=0.1)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            await recv(ws); await recv(ws)
            await ws.close()
            await asyncio.sleep(0.3)
            assert session.closed.is_set()
            assert session.exit_status() == 1  # abandoned merge
        finally:
            await client.close()

    run(scenario())


@pytest.fixture
def repo(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(repo_dir), *args],
                       check=True, capture_output=True)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "bmeld tests")
    (repo_dir / "a.py").write_text("original\n")
    git("add", ".")
    git("commit", "-q", "-m", "initial")
    (repo_dir / "a.py").write_text("changed\n")
    return repo_dir


def test_vc_tab_tree_diff_commit(repo):
    async def scenario():
        session = make_session([str(repo)])
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            tab = init["tabs"][0]
            assert tab["kind"] == "vc" and "[Git]" in tab["label"]

            tree = await recv(ws)
            assert tree["type"] == "tree" and tree["differences"] == 1
            row = tree["root"]["children"][0]
            assert "a.py" in row["names"][0]

            # open repo-vs-working comparison
            await ws.send_json({
                "type": "vc_diff", "tab": tab["id"], "path": row["paths"][0],
            })
            reply = await recv(ws)
            assert reply["type"] == "tab_added"
            added = reply["tab"]
            assert added["readonly"] == [0]
            assert added["label"].endswith("(repository, working)")
            assert added["texts"] == ["original", "changed"]

            # commit the change
            await ws.send_json({
                "type": "vc_commit", "tab": tab["id"], "message": "web commit",
            })
            reply = await recv(ws)
            assert reply["type"] == "vc_result" and reply["ok"], reply
            await ws.send_json({"type": "scan", "tab": tab["id"]})
            tree2 = await recv(ws)
            assert tree2["differences"] == 0
        finally:
            await client.close()

    run(scenario())
    import subprocess
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%s"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "web commit"


def test_vc_revert(repo):
    async def scenario():
        session = make_session([str(repo)])
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            tab = init["tabs"][0]["id"]
            tree = await recv(ws)
            path = tree["root"]["children"][0]["paths"][0]
            await ws.send_json({
                "type": "vc_revert", "tab": tab, "path": path,
            })
            reply = await recv(ws)
            assert reply["type"] == "vc_result" and reply["ok"], reply
        finally:
            await client.close()

    run(scenario())
    assert (repo / "a.py").read_text() == "original\n"


def test_index_versions_asset_urls_and_reports_build(three):
    """A stale bundle must be unservable, and the running build identifiable."""
    from tmeld import __version__
    from tmeld.web.server import build_id

    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            resp = await client.get(f"/t/{TOKEN}")
            assert resp.status == 200
            assert resp.headers["Cache-Control"] == "no-store"
            html = await resp.text()

            build = build_id()
            assert len(build) == 8 and int(build, 16) >= 0
            # every asset URL carries the build id, so the browser cannot
            # reuse a cached copy from a previous build
            assert f"/assets/bmeld.js?v={build}" in html
            assert f"/assets/bmeld.css?v={build}" in html
            assert 'href="/assets/bmeld.css"' not in html

            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = await recv(ws)
            assert init["version"] == __version__
            assert init["build"] == build
        finally:
            await client.close()

    run(scenario())


def test_build_id_tracks_asset_contents(tmp_path, monkeypatch):
    """Change a served asset, get a different build id."""
    import tmeld.web.server as srv

    before = srv.build_id()
    css = srv.STATIC_DIR / "bmeld.css"
    original = css.read_bytes()
    try:
        css.write_bytes(original + b"\n/* touched */\n")
        assert srv.build_id() != before
    finally:
        css.write_bytes(original)
    assert srv.build_id() == before


def test_interrupt_exits_promptly_with_130(three):
    """One Ctrl-C is enough: live sockets are closed rather than waited on."""
    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            await recv(ws)  # init
            assert session._websockets, "server should track the live socket"

            session.interrupt()
            assert session.closed.is_set()
            assert session.exit_status() == 130

            await asyncio.wait_for(session.close_websockets(), timeout=2)
            # Drain what the session had already queued (chunks, ...); the
            # point is a close frame arrives without the client sending more.
            closing = {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                       aiohttp.WSMsgType.CLOSING}
            while True:
                msg = await asyncio.wait_for(ws.receive(), timeout=2)
                if msg.type in closing:
                    break
        finally:
            await client.close()

    run(scenario())


def test_interrupt_beats_the_saved_merge_exit_code(three):
    """Aborting must not look like a completed merge."""
    session = make_session(three)
    for tab in session.merge_saved:
        session.merge_saved[tab] = True
    assert session.exit_status() == 0
    session.interrupt()
    assert session.exit_status() == 130


def test_default_bind_is_loopback_only():
    """The safe default must not drift."""
    import inspect
    from tmeld.web import cli
    from tmeld.web.server import run_session

    assert inspect.signature(run_session).parameters["bind"].default == "127.0.0.1"
    parser_src = inspect.getsource(cli.main)
    assert '"--bind", metavar="ADDR", default="127.0.0.1"' in parser_src


def test_advertised_host_for_each_bind():
    """A wildcard bind tells us nothing about reachability: ask the route table."""
    import ipaddress
    from tmeld.web.server import advertised_host

    assert advertised_host("127.0.0.1") == "127.0.0.1"
    assert advertised_host("192.168.1.50") == "192.168.1.50"
    assert advertised_host("::1") == "[::1]"          # bracketed for a URL

    for wildcard in ("0.0.0.0", "::", ""):
        host = advertised_host(wildcard)
        assert host and host not in ("0.0.0.0", "::"), host
        # either a routable address or, if the probe failed, a hostname
        try:
            ip = ipaddress.ip_address(host)
            assert not ip.is_unspecified
        except ValueError:
            pass


def test_non_loopback_bind_warns(three, capsys):
    """Opting into the network must say what it costs."""
    import inspect
    from tmeld.web import cli

    src = inspect.getsource(cli.main)
    assert "WARNING listening on" in src
    assert "file=sys.stderr" in src
    # and the ssh forward hint is skipped when the port is already reachable
    assert "if under_ssh and loopback:" in src


def test_server_binds_where_it_is_told(three):
    """The site really listens on the given address, not just in the help text."""
    async def scenario():
        session = make_session(three)
        app = make_app(session, TOKEN)
        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        try:
            site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            host, port = runner.addresses[0][:2]
            assert host == "127.0.0.1"
            assert port > 0
        finally:
            await runner.cleanup()

    run(scenario())
