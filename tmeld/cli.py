"""One command line for both front-ends.

    tmeld a b               terminal
    tmeld --web a b         browser, random port
    tmeld --port 8731 a b   browser (--port implies --web)
    bmeld a b               alias for `tmeld --web`

The arguments that describe *what to compare* -- paths, `--diff`, `-o`,
`--theme`, `--show-line-numbers` -- are identical in both, and were previously
written out twice; they had already drifted. The front-end-specific flags are
kept in their own groups and rejected when they do not apply, rather than
silently ignored.

`--port` may imply `--web` because it has no meaning in the terminal, so it
cannot be an accident. It is sugar, not the only door: `--web` says what you
want by name.
"""

import argparse
import os
import secrets
import sys
import webbrowser
from typing import List, Optional, Sequence, Tuple

from tmeld import __version__
from tmeld.palette import DEFAULT_THEME, THEMES

# (paths, output-override-for-middle-pane)
DiffSpec = Tuple[List[str], Optional[str]]

GRAPHICS_MODES = ("auto", "none", "sixel", "kitty")


def osc8(url: str) -> str:
    """Wrap a URL in an OSC 8 hyperlink so terminals make it clickable."""
    return f"\x1b]8;;{url}\x1b\\{url}\x1b]8;;\x1b\\"


def build_parser(prog: str, default_web: bool) -> argparse.ArgumentParser:
    """Every flag, in one place.

    Front-end-specific options default to None so that "was it given?" is
    answerable -- `--port 0` means a random port, and is not the same as no
    `--port` at all.
    """
    description = (
        "Meld, in your browser" if default_web
        else "Meld, in your terminal (--web serves it to a browser instead)"
    )
    parser = argparse.ArgumentParser(prog=prog, description=description)

    parser.add_argument(
        "files", nargs="*",
        help="two or three files (3-way: LOCAL MERGED REMOTE) or folders "
             "to compare; a single path opens the version-control view",
    )
    parser.add_argument(
        "--diff", action="append", nargs="+", default=[], metavar="PATH",
        help="open an extra comparison tab for 1-3 paths (repeatable)",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="write middle-pane saves to FILE (3-way only, like meld -o)",
    )
    parser.add_argument(
        "--theme", choices=sorted(THEMES), default=DEFAULT_THEME
    )
    parser.add_argument(
        "--show-line-numbers", action="store_true",
        help="show line numbers in the panes (Meld hides them by default; "
             "the status bar always shows the cursor position)",
    )
    version = (
        f"tmeld {__version__}" if prog == "tmeld"
        else f"{prog} (tmeld) {__version__}"
    )
    parser.add_argument("--version", action="version", version=version)

    terminal = parser.add_argument_group("terminal options")
    terminal.add_argument(
        "--graphics", choices=GRAPHICS_MODES, default=None,
        help="pixel linkmap protocol (default: probe the terminal at startup)",
    )

    web = parser.add_argument_group(
        "browser options" if default_web else "browser options (imply --web)"
    )
    if not default_web:
        web.add_argument(
            "--web", action="store_true",
            help="serve the comparison to a browser instead of drawing it "
                 "in this terminal",
        )
    web.add_argument(
        "--port", type=int, default=None,
        help="fixed port (default: random). A fixed port plus a LocalForward "
             "line in ~/.ssh/config makes remote links just work.",
    )
    web.add_argument(
        "--bind", metavar="ADDR", default=None,
        help="address to listen on (default: 127.0.0.1, loopback only). "
             "Use 0.0.0.0 to accept connections from other machines: the "
             "token in the URL is then the only thing protecting a process "
             "that reads and writes your files, over plain HTTP.",
    )
    web.add_argument(
        "--advertise", metavar="HOST", default=None,
        help="hostname or address to put in the printed URL (default: the "
             "bind address, or this machine's outbound IP when binding a "
             "wildcard)",
    )
    web.add_argument(
        "--no-open", action="store_true",
        help="don't try to open a local browser",
    )
    web.add_argument(
        "--grace", type=float, default=None,
        help="seconds to wait for a browser reconnect before treating the "
             "session as abandoned (exit 1 for unsaved merges)",
    )
    return parser


# flag name -> the attribute that carries it, for the "wrong front-end" errors
WEB_ONLY = (("--port", "port"), ("--bind", "bind"),
            ("--advertise", "advertise"), ("--grace", "grace"))


def resolve(parser, args, default_web: bool) -> bool:
    """Validate, and answer: are we serving to a browser?"""
    if args.files and len(args.files) > 3:
        parser.error("expected 1-3 paths")
    for group in args.diff:
        if len(group) > 3:
            parser.error("--diff takes 1-3 paths")
    if not args.files and not args.diff:
        parser.error("expected 1-3 paths to compare")
    if args.output and len(args.files) != 3:
        parser.error("--output requires a 3-way comparison")

    web = default_web or getattr(args, "web", False) or args.port is not None
    if not web:
        # --no-open is a store_true, so it has no None sentinel
        for flag, attr in WEB_ONLY:
            if getattr(args, attr) is not None:
                parser.error(f"{flag} requires --web")
        if args.no_open:
            parser.error("--no-open requires --web")
    elif args.graphics is not None:
        # bmeld has no --web to point at: name the other front-end instead
        where = "the terminal front-end (tmeld)" if default_web else "the terminal, not --web"
        parser.error(f"--graphics applies to {where}")

    # front-end defaults, applied only once the front-end is known
    if web:
        if args.port is None:
            args.port = 0
        if args.bind is None:
            args.bind = "127.0.0.1"
        if args.grace is None:
            args.grace = 60.0
    elif args.graphics is None:
        args.graphics = "auto"
    return web


def diff_specs(args) -> List[DiffSpec]:
    diffs: List[DiffSpec] = []
    if args.files:
        diffs.append((args.files, args.output))
    diffs.extend((group, None) for group in args.diff)
    return diffs


def run_terminal(parser, args) -> int:
    from tmeld.app import TmeldApp

    graphics = args.graphics
    if graphics == "auto":
        from tmeld.term import probe_graphics

        graphics = probe_graphics()
    cell_px = None
    if graphics != "none":
        from tmeld.term import cell_pixel_size

        cell_px = cell_pixel_size()

    try:
        app = TmeldApp(
            theme_name=args.theme, diffs=diff_specs(args),
            graphics=graphics, cell_px=cell_px,
            show_line_numbers=args.show_line_numbers,
        )
    except (OSError, ValueError) as err:
        parser.error(str(err))
    app.run()
    return app.exit_status()


def run_web(parser, args) -> int:
    import asyncio

    try:
        from tmeld.web.server import make_session, run_session
    except ImportError:
        # Distro packages bundle Textual but not aiohttp (it has C extensions);
        # point at the right installer for how this copy was installed.
        packaged = __file__.startswith("/usr/share/")
        how = ("apt install python3-aiohttp" if packaged
               else "pip install 'tmeld[web]'")
        parser.error(f"the browser front-end needs aiohttp — install with: {how}")

    try:
        session = make_session(
            args.files, theme_name=args.theme, grace=args.grace,
            diffs=diff_specs(args), line_numbers=args.show_line_numbers,
        )
    except (OSError, ValueError) as err:
        parser.error(str(err))

    token = secrets.token_urlsafe(16)
    loopback = args.bind in ("127.0.0.1", "::1", "localhost")
    under_ssh = "SSH_CONNECTION" in os.environ

    if not loopback:
        print(
            f"{parser.prog}: WARNING listening on {args.bind} — anyone who can "
            f"reach this port and guess the URL can read and write the files "
            f"under comparison. The token is unguessable but travels in "
            f"cleartext over HTTP; do not use this on an untrusted network.",
            file=sys.stderr, flush=True,
        )

    def announce(url: str) -> None:
        print(f"{parser.prog}: {osc8(url)}", flush=True)
        if under_ssh and loopback:
            # reachable only from this host, so the link needs a tunnel
            port = url.rsplit(":", 1)[1].split("/", 1)[0]
            print(
                f"{parser.prog}: (remote session — forward the port first, "
                f"e.g. ssh -O forward -L {port}:localhost:{port} <this-host>, "
                f"then open the link locally)",
                flush=True,
            )
        elif not under_ssh and not args.no_open:
            webbrowser.open(url)

    try:
        return asyncio.run(
            run_session(session, token, port=args.port, on_url=announce,
                        bind=args.bind, advertise=args.advertise)
        )
    except KeyboardInterrupt:
        # run_session normally handles SIGINT itself and returns 130; this is
        # the fallback where add_signal_handler is unavailable (Windows).
        return 130


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    prog: str = "tmeld",
    default_web: bool = False,
) -> int:
    parser = build_parser(prog, default_web)
    args = parser.parse_args(argv)
    web = resolve(parser, args, default_web)
    return run_web(parser, args) if web else run_terminal(parser, args)
