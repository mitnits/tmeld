"""bmeld server: one aiohttp app serving the UI and one WS session.

Security: binds 127.0.0.1 only; every route is gated by an
unguessable token; CSP restricts the page to same-origin; the process
can only read/write the files given on its command line.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Sequence

from aiohttp import WSMsgType, web

from tmeld.comparison import Comparison
from tmeld.palette import THEMES, Theme
from tmeld.web.protocol import chunks_payload, init_payload

log = logging.getLogger("bmeld")

STATIC_DIR = Path(__file__).parent / "static"

CSP = (
    "default-src 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'"
)


class BmeldSession:
    """One comparison being resolved in the browser."""

    def __init__(
        self,
        paths: Sequence[str],
        theme: Theme,
        output: Optional[str] = None,
        grace: float = 60.0,
    ) -> None:
        self.comparison = Comparison(paths, output=output)
        self.theme = theme
        self.grace = grace
        self.merged_saved = False
        self.closed = asyncio.Event()
        self._connections = 0
        self._grace_task: Optional[asyncio.Task] = None

    # --- lifecycle ---------------------------------------------------------

    def exit_status(self) -> int:
        if self.comparison.num_panes == 3:
            return 0 if self.merged_saved else 1
        return 0

    def _client_connected(self) -> None:
        self._connections += 1
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None

    def _client_disconnected(self) -> None:
        self._connections -= 1
        if self._connections <= 0 and not self.closed.is_set():
            self._grace_task = asyncio.get_event_loop().create_task(
                self._grace_expiry()
            )

    async def _grace_expiry(self) -> None:
        try:
            await asyncio.sleep(self.grace)
        except asyncio.CancelledError:
            return
        log.info("no client for %.0fs; treating session as abandoned",
                 self.grace)
        self.closed.set()

    # --- message handling ----------------------------------------------------

    async def handle_ws(self, ws: web.WebSocketResponse) -> None:
        self._client_connected()
        try:
            await ws.send_json(init_payload(self.comparison, self.theme))
            await ws.send_json(chunks_payload(self.comparison))
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                    reply = self._dispatch(data)
                except Exception as err:  # noqa: BLE001 - report to client
                    log.exception("bad message")
                    reply = {"type": "error", "message": str(err)}
                if reply is not None:
                    await ws.send_json(reply)
                if self.closed.is_set():
                    break
        finally:
            self._client_disconnected()

    def _dispatch(self, data: dict) -> Optional[dict]:
        kind = data.get("type")
        comparison = self.comparison
        if kind == "buffers":
            comparison.lines = [
                text.split("\n") for text in data["texts"]
            ]
            comparison.recompute()
            payload = chunks_payload(comparison)
            payload["version"] = data.get("version", 0)
            return payload
        if kind == "save":
            pane = int(data["pane"])
            comparison.lines[pane] = data["text"].split("\n")
            comparison.save(pane)
            if pane == 1 and comparison.num_panes == 3:
                self.merged_saved = True
            return {"type": "saved", "pane": pane,
                    "path": comparison.save_paths[pane]}
        if kind == "merge_all":
            # engine-side (vendored Merger) so merge semantics stay Meld's
            comparison.lines = [
                text.split("\n") for text in data["texts"]
            ]
            comparison.recompute()
            merged = comparison.merge_all_non_conflicting()
            return {"type": "merged", "text": merged}
        if kind == "close":
            self.closed.set()
            return None
        raise ValueError(f"unknown message type: {kind!r}")


def make_app(session: BmeldSession, token: str) -> web.Application:
    app = web.Application()

    def check(request: web.Request) -> None:
        if request.match_info.get("token") != token:
            raise web.HTTPNotFound()

    async def index(request: web.Request) -> web.Response:
        check(request)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(
            text=html, content_type="text/html",
            headers={"Content-Security-Policy": CSP},
        )

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        check(request)
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        await session.handle_ws(ws)
        return ws

    async def closed_beacon(request: web.Request) -> web.Response:
        check(request)
        session.closed.set()
        return web.Response(text="bye")

    app.router.add_get("/t/{token}", index)
    app.router.add_get("/ws/{token}", websocket)
    app.router.add_post("/close/{token}", closed_beacon)
    app.router.add_static("/assets", STATIC_DIR)
    return app


async def run_session(
    session: BmeldSession,
    token: str,
    port: int = 0,
    on_url=None,
) -> int:
    """Serve until the session closes; returns the exit status."""
    app = make_app(session, token)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    actual_port = runner.addresses[0][1]
    url = f"http://127.0.0.1:{actual_port}/t/{token}"
    if on_url is not None:
        on_url(url)
    try:
        await session.closed.wait()
    finally:
        await runner.cleanup()
    return session.exit_status()


def make_session(
    paths: Sequence[str],
    theme_name: str = "meld-base",
    output: Optional[str] = None,
    grace: float = 60.0,
) -> BmeldSession:
    return BmeldSession(
        paths, THEMES[theme_name], output=output, grace=grace
    )
