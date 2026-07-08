"""bmeld server: one aiohttp app; a session holding comparison tabs.

Tabs mirror the TUI shell: file tabs (2/3-way Comparison) and dir tabs
(DirComparison); Enter on a tree row asks the server to open a file
tab (`open_file`). Directory scans run in a thread executor so the
event loop stays responsive.

Security: binds 127.0.0.1 only; every route is gated by an
unguessable token; CSP restricts the page to same-origin; the process
only touches the paths given on its command line (plus files under
the compared directories, which is the point of a dir comparison).
"""

import asyncio
import hashlib
import itertools
import json
import logging
import os
import signal
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from aiohttp import WSMsgType, web

from tmeld import __version__
from tmeld.comparison import Comparison
from tmeld.dircompare import DirComparison
from tmeld.palette import THEMES, Theme
from tmeld.vcview import VcComparison, run_diff_spec, run_vc_command
from tmeld.web.protocol import (
    chunks_payload,
    dir_tab_payload,
    file_tab_payload,
    palette_payload,
    tree_payload,
    vc_tab_payload,
)

log = logging.getLogger("bmeld")

STATIC_DIR = Path(__file__).parent / "static"


def build_id() -> str:
    """Short hash of the assets actually on disk.

    Shown in the footer and used to bust the browser cache, so "am I running
    the build I think I am?" is answerable by looking at the page.
    """
    h = hashlib.sha256()
    for name in ("bmeld.js", "bmeld.css", "index.html"):
        h.update((STATIC_DIR / name).read_bytes())
    return h.hexdigest()[:8]

CSP = (
    "default-src 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'"
)

TabComparison = Union[Comparison, DirComparison, VcComparison]


def make_comparison(
    paths: Sequence[str], output: Optional[str] = None
) -> TabComparison:
    """Files -> Comparison, all-dirs -> DirComparison, a single path ->
    VC view (Meld-style auto-detection, like the TUI's make_view)."""
    if len(paths) == 1:
        if output:
            raise ValueError("--output requires a file comparison")
        return VcComparison(paths[0])
    dir_flags = [os.path.isdir(p) for p in paths]
    if all(dir_flags):
        if output:
            raise ValueError("--output requires a file comparison")
        return DirComparison(paths)
    if any(dir_flags):
        raise ValueError("cannot mix files and folders in one comparison")
    return Comparison(paths, output=output)


class BmeldSession:
    """One bmeld process: comparison tabs resolved in the browser."""

    def __init__(
        self,
        specs: Sequence,  # [(paths, output), ...]
        theme: Theme,
        grace: float = 60.0,
        line_numbers: bool = False,
    ) -> None:
        self._ids = itertools.count(0)
        self.tabs: Dict[str, TabComparison] = {}
        self.tab_order: List[str] = []
        for paths, output in specs:
            tab_id = f"tab{next(self._ids)}"
            self.tabs[tab_id] = make_comparison(paths, output=output)
            self.tab_order.append(tab_id)
        self.theme = theme
        self.grace = grace
        # Meld hides line numbers by default; the footer carries the cursor
        # position instead.
        self.line_numbers = line_numbers
        # every 3-way file tab ever opened must be saved for exit 0
        # (closing an unsaved merge tab still fails, like the TUI shell)
        self.merge_saved: Dict[str, bool] = {}
        for tab_id, comparison in self.tabs.items():
            if isinstance(comparison, Comparison) and comparison.num_panes == 3:
                self.merge_saved[tab_id] = False
        self.closed = asyncio.Event()
        self.interrupted = False
        self._connections = 0
        self._grace_task: Optional[asyncio.Task] = None
        self._websockets: "set[web.WebSocketResponse]" = set()

    # --- lifecycle ---------------------------------------------------------

    def exit_status(self) -> int:
        if self.interrupted:
            return 130  # 128 + SIGINT, so `git mergetool` sees a failure
        return 0 if all(self.merge_saved.values()) else 1

    def interrupt(self) -> None:
        """Ctrl-C / SIGTERM: stop now, don't wait on the browser."""
        self.interrupted = True
        self.closed.set()

    async def close_websockets(self) -> None:
        """Close live sockets so `AppRunner.cleanup()` doesn't block on them.

        `handle_ws` sits in `async for msg in ws`, which only returns when the
        peer goes away -- setting `closed` alone would leave the handler (and
        therefore shutdown) waiting for the socket's timeout.
        """
        if self._grace_task is not None:
            self._grace_task.cancel()
            self._grace_task = None
        for ws in list(self._websockets):
            await ws.close(code=1001, message=b"bmeld exiting")

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

    # --- payload helpers -----------------------------------------------------

    def _tab_payload(self, tab_id: str) -> dict:
        comparison = self.tabs[tab_id]
        if isinstance(comparison, VcComparison):
            return vc_tab_payload(tab_id, comparison)
        if isinstance(comparison, DirComparison):
            return dir_tab_payload(tab_id, comparison)
        return file_tab_payload(tab_id, comparison)

    async def _initial_messages(self):
        yield {
            "type": "init",
            "tabs": [self._tab_payload(t) for t in self.tab_order],
            "palette": palette_payload(self.theme),
            "line_numbers": self.line_numbers,
            "version": __version__,
            "build": build_id(),
        }
        for tab_id in list(self.tab_order):
            comparison = self.tabs[tab_id]
            if isinstance(comparison, (DirComparison, VcComparison)):
                yield await self._scan(tab_id, comparison)
            else:
                yield chunks_payload(tab_id, comparison)

    async def _scan(self, tab_id: str, comparison) -> dict:
        loop = asyncio.get_event_loop()

        def scan():
            if isinstance(comparison, VcComparison):
                for _progress in comparison.scan_iter():
                    pass
            else:
                comparison.scan()

        await loop.run_in_executor(None, scan)
        return tree_payload(tab_id, comparison)

    def _add_file_tab(self, comparison: Comparison, **payload_kwargs) -> dict:
        tab_id = f"tab{next(self._ids)}"
        self.tabs[tab_id] = comparison
        self.tab_order.append(tab_id)
        if comparison.num_panes == 3:
            self.merge_saved[tab_id] = False
        return {
            "type": "tab_added",
            "tab": file_tab_payload(tab_id, comparison, **payload_kwargs),
            "chunks": chunks_payload(tab_id, comparison),
        }

    # --- message handling ----------------------------------------------------

    async def handle_ws(self, ws: web.WebSocketResponse) -> None:
        self._client_connected()
        self._websockets.add(ws)
        try:
            async for payload in self._initial_messages():
                await ws.send_json(payload)
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                    reply = await self._dispatch(data)
                except Exception as err:  # noqa: BLE001 - report to client
                    log.exception("bad message")
                    reply = {"type": "error", "message": str(err)}
                if reply is not None:
                    await ws.send_json(reply)
                if self.closed.is_set():
                    break
        finally:
            self._websockets.discard(ws)
            self._client_disconnected()

    async def _dispatch(self, data: dict) -> Optional[dict]:
        kind = data.get("type")
        if kind == "close":
            self.closed.set()
            return None
        if kind == "open_file":
            paths = [str(p) for p in data["paths"]]
            if not (2 <= len(paths) <= 3):
                raise ValueError("open_file takes 2 or 3 paths")
            return self._add_file_tab(Comparison(paths))

        tab_id = data["tab"]
        comparison = self.tabs.get(tab_id)
        if comparison is None:
            raise ValueError(f"unknown tab: {tab_id!r}")

        if kind == "close_tab":
            del self.tabs[tab_id]
            self.tab_order.remove(tab_id)
            # merge_saved entry stays: closed-unsaved still fails
            return None
        if kind == "scan" and isinstance(
            comparison, (DirComparison, VcComparison)
        ):
            return await self._scan(tab_id, comparison)

        if isinstance(comparison, VcComparison):
            loop = asyncio.get_event_loop()
            vc = comparison.vc
            if kind == "vc_diff":
                path = str(data["path"])
                spec = await loop.run_in_executor(
                    None, run_diff_spec, vc, path)
                if spec is None:
                    raise ValueError(f"nothing to compare for {path!r}")
                return self._add_file_tab(
                    Comparison(spec["paths"], output=spec["output"]),
                    labels=spec["labels"],
                    readonly=spec["readonly"],
                    tab_title=spec["tab_title"],
                )
            if kind == "vc_commit":
                message = str(data.get("message", "")).strip()
                if not message:
                    raise ValueError("empty commit message")

                def commit():
                    files = vc.get_files_to_commit([comparison.location])
                    if not files:
                        return False, "nothing to commit"
                    result = {}

                    def runner(command, cfiles, refresh, working_dir):
                        ok, detail = run_vc_command(
                            command, cfiles, working_dir)
                        result["ok"], result["detail"] = ok, detail

                    vc.commit(runner, files, message)
                    return result.get("ok", False), result.get("detail", "")

                ok, detail = await loop.run_in_executor(None, commit)
                return {"type": "vc_result", "tab": tab_id,
                        "ok": ok, "detail": detail or "committed"}
            if kind == "vc_revert":
                path = str(data["path"])

                def revert():
                    result = {}

                    def runner(command, cfiles, refresh, working_dir):
                        ok, detail = run_vc_command(
                            command, cfiles, working_dir)
                        result["ok"], result["detail"] = ok, detail

                    vc.revert(runner, [path])
                    return result.get("ok", False), result.get("detail", "")

                ok, detail = await loop.run_in_executor(None, revert)
                return {"type": "vc_result", "tab": tab_id,
                        "ok": ok, "detail": detail or "reverted"}

        if not isinstance(comparison, Comparison):
            raise ValueError(f"{kind!r} needs a file tab")
        if kind == "buffers":
            comparison.lines = [t.split("\n") for t in data["texts"]]
            comparison.recompute()
            payload = chunks_payload(tab_id, comparison)
            payload["version"] = data.get("version", 0)
            return payload
        if kind == "save":
            pane = int(data["pane"])
            comparison.lines[pane] = data["text"].split("\n")
            comparison.save(pane)
            if pane == 1 and comparison.num_panes == 3:
                self.merge_saved[tab_id] = True
            return {"type": "saved", "tab": tab_id, "pane": pane,
                    "path": comparison.save_paths[pane]}
        if kind == "merge_all":
            # engine-side (vendored Merger) so merge semantics stay Meld's
            comparison.lines = [t.split("\n") for t in data["texts"]]
            comparison.recompute()
            merged = comparison.merge_all_non_conflicting()
            return {"type": "merged", "tab": tab_id, "text": merged}
        raise ValueError(f"unknown message type: {kind!r}")


def make_app(session: BmeldSession, token: str) -> web.Application:
    app = web.Application()

    def check(request: web.Request) -> None:
        if request.match_info.get("token") != token:
            raise web.HTTPNotFound()

    build = build_id()

    async def index(request: web.Request) -> web.Response:
        check(request)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        # Version the asset URLs: a stale bundle can then never be served from
        # the browser cache, and the footer's build id always names the code
        # that is actually running.
        html = html.replace("/assets/bmeld.js", f"/assets/bmeld.js?v={build}")
        html = html.replace("/assets/bmeld.css", f"/assets/bmeld.css?v={build}")
        return web.Response(
            text=html, content_type="text/html",
            headers={
                "Content-Security-Policy": CSP,
                "Cache-Control": "no-store",
            },
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
    # Don't let a wedged socket hold the process: one Ctrl-C must be enough.
    site = web.TCPSite(runner, "127.0.0.1", port, shutdown_timeout=1.0)
    await site.start()
    actual_port = runner.addresses[0][1]
    url = f"http://127.0.0.1:{actual_port}/t/{token}"
    if on_url is not None:
        on_url(url)

    loop = asyncio.get_running_loop()
    installed = []
    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, session.interrupt)
            installed.append(sig)
        except NotImplementedError:  # Windows proactor loop
            pass
    try:
        await session.closed.wait()
    except asyncio.CancelledError:
        session.interrupt()
        raise
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        await session.close_websockets()
        await runner.cleanup()
    return session.exit_status()


def make_session(
    paths: Sequence[str],
    theme_name: str = "meld-base",
    output: Optional[str] = None,
    grace: float = 60.0,
    diffs: Optional[Sequence] = None,
    line_numbers: bool = False,
) -> BmeldSession:
    specs = list(diffs) if diffs is not None else [(list(paths), output)]
    return BmeldSession(specs, THEMES[theme_name], grace=grace,
                        line_numbers=line_numbers)
