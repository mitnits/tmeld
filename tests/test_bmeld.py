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


def run(coro):
    return asyncio.run(coro)


async def client_for(session):
    app = make_app(session, TOKEN)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def test_init_and_chunks_flow(three):
    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            init = json.loads((await ws.receive()).data)
            assert init["type"] == "init"
            assert init["num_panes"] == 3
            assert init["palette"]["chunk"]["conflict"]["fill"] == "#ffa5a3"
            assert init["texts"][1] == "a\nbase\nc"

            chunks = json.loads((await ws.receive()).data)
            assert chunks["type"] == "chunks"
            assert chunks["conflict_count"] == 1
            tags = {c[0] for c in chunks["panes"][1]}
            assert "conflict" in tags
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
            await ws.receive()  # init
            await ws.receive()  # initial chunks
            # resolve the conflict: make middle identical to local
            await ws.send_json({
                "type": "buffers", "version": 7,
                "texts": ["a\nLOCAL\nc\nextra", "a\nLOCAL\nc\nextra",
                          "a\nREMOTE\nc"],
            })
            reply = json.loads((await ws.receive()).data)
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
            await ws.receive(); await ws.receive()
            assert session.exit_status() == 1  # nothing saved yet
            await ws.send_json({
                "type": "save", "pane": 1, "text": "resolved\n"
            })
            reply = json.loads((await ws.receive()).data)
            assert reply["type"] == "saved" and reply["pane"] == 1
            assert session.exit_status() == 0
            await ws.send_json({"type": "close"})
            await asyncio.sleep(0.05)
            assert session.closed.is_set()
        finally:
            await client.close()

    run(scenario())
    assert out.read_text() == "resolved\n\n" or out.read_text() == "resolved\n"


def test_merge_all_uses_engine(three):
    async def scenario():
        session = make_session(three)
        client = await client_for(session)
        try:
            ws = await client.ws_connect(f"/ws/{TOKEN}")
            await ws.receive(); await ws.receive()
            await ws.send_json({
                "type": "merge_all",
                "texts": ["a\nLOCAL\nc\nextra", "a\nbase\nc",
                          "a\nREMOTE\nc"],
            })
            reply = json.loads((await ws.receive()).data)
            assert reply["type"] == "merged"
            # the local-only "extra" merges; the conflict keeps base
            assert "extra" in reply["text"] and "base" in reply["text"]
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
            await ws.receive(); await ws.receive()
            await ws.close()
            await asyncio.sleep(0.3)
            assert session.closed.is_set()
            assert session.exit_status() == 1  # abandoned merge
        finally:
            await client.close()

    run(scenario())
