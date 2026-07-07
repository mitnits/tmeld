"""bmeld CLI: start the server, print the clickable URL, wait, exit
with the mergetool contract (BMELD.md)."""

import argparse
import asyncio
import os
import secrets
import sys
import webbrowser
from typing import Optional, Sequence

from tmeld import __version__
from tmeld.palette import DEFAULT_THEME, THEMES


def osc8(url: str) -> str:
    """Wrap a URL in an OSC 8 hyperlink so terminals make it clickable."""
    return f"\x1b]8;;{url}\x1b\\{url}\x1b]8;;\x1b\\"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bmeld", description="Meld, in your browser"
    )
    parser.add_argument(
        "files", nargs="+",
        help="two or three files (3-way: LOCAL MERGED REMOTE) or folders; "
             "a single path opens the version-control view",
    )
    parser.add_argument(
        "--diff", action="append", nargs="+", default=[], metavar="PATH",
        help="open an extra comparison tab for 2 or 3 paths (repeatable)",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="write middle-pane saves to FILE (3-way only, like meld -o)",
    )
    parser.add_argument(
        "--theme", choices=sorted(THEMES), default=DEFAULT_THEME
    )
    parser.add_argument(
        "--port", type=int, default=0,
        help="fixed port (default: random). A fixed port plus a "
             "LocalForward line in ~/.ssh/config makes remote links "
             "just work.",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="don't try to open a local browser",
    )
    parser.add_argument(
        "--grace", type=float, default=60.0,
        help="seconds to wait for a browser reconnect before treating "
             "the session as abandoned (exit 1 for unsaved merges)",
    )
    parser.add_argument(
        "--version", action="version", version=f"bmeld (tmeld) {__version__}"
    )
    args = parser.parse_args(argv)

    if len(args.files) > 3:
        parser.error("expected 1-3 paths")
    for group in args.diff:
        if len(group) > 3:
            parser.error("--diff takes 1-3 paths")
    if args.output and len(args.files) != 3:
        parser.error("--output requires a 3-way comparison")

    try:
        from tmeld.web.server import make_session, run_session
    except ImportError:
        parser.error(
            "bmeld needs aiohttp — install with: pip install 'tmeld[web]'"
        )

    diffs = [(args.files, args.output)]
    diffs.extend((group, None) for group in args.diff)
    try:
        session = make_session(
            args.files, theme_name=args.theme, grace=args.grace,
            diffs=diffs,
        )
    except (OSError, ValueError) as err:
        parser.error(str(err))

    token = secrets.token_urlsafe(16)
    under_ssh = "SSH_CONNECTION" in os.environ

    def announce(url: str) -> None:
        print(f"bmeld: {osc8(url)}", flush=True)
        if under_ssh:
            port = url.rsplit(":", 1)[1].split("/", 1)[0]
            print(
                f"bmeld: (remote session — forward the port first, e.g. "
                f"ssh -O forward -L {port}:localhost:{port} <this-host>, "
                f"then open the link locally)",
                flush=True,
            )
        elif not args.no_open:
            webbrowser.open(url)

    return asyncio.run(
        run_session(session, token, port=args.port, on_url=announce)
    )


if __name__ == "__main__":
    sys.exit(main())
