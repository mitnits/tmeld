"""One command line, two front-ends.

`--web` says what you want by name; `--port` implies it, because a port has no
meaning in the terminal and so cannot be an accident. Options belonging to the
other front-end are rejected rather than silently ignored.
"""

import pytest

from tmeld.cli import build_parser, diff_specs, resolve


def parse(argv, prog="tmeld", default_web=False):
    parser = build_parser(prog, default_web)
    args = parser.parse_args(argv)
    return parser, args


def is_web(argv, prog="tmeld", default_web=False):
    parser, args = parse(argv, prog, default_web)
    return resolve(parser, args, default_web)


def rejects(argv, prog="tmeld", default_web=False):
    with pytest.raises(SystemExit) as exc:
        parser, args = parse(argv, prog, default_web)
        resolve(parser, args, default_web)
    assert exc.value.code == 2


# --- which front-end ---------------------------------------------------------


def test_bare_tmeld_is_the_terminal():
    assert is_web(["a", "b"]) is False


def test_web_flag_selects_the_browser():
    assert is_web(["--web", "a", "b"]) is True


def test_port_implies_web():
    assert is_web(["--port", "8731", "a", "b"]) is True


def test_port_zero_still_implies_web():
    """`--port 0` means a random port; it is not the same as no --port."""
    assert is_web(["--port", "0", "a", "b"]) is True


def test_bmeld_is_web_without_saying_so():
    assert is_web(["a", "b"], prog="bmeld", default_web=True) is True


def test_bmeld_has_no_web_flag():
    """It is implied, so offering it would be noise."""
    parser = build_parser("bmeld", default_web=True)
    with pytest.raises(SystemExit):
        parser.parse_args(["--web", "a", "b"])


# --- flags belonging to the other front-end ---------------------------------


@pytest.mark.parametrize("flag", [
    ["--bind", "0.0.0.0"], ["--advertise", "mini"],
    ["--grace", "5"], ["--no-open"],
])
def test_browser_flags_require_web(flag):
    rejects([*flag, "a", "b"])


def test_browser_flags_are_fine_with_web():
    assert is_web(["--web", "--bind", "0.0.0.0", "--no-open", "a", "b"]) is True


def test_graphics_is_terminal_only():
    rejects(["--web", "--graphics", "kitty", "a", "b"])
    rejects(["--graphics", "kitty", "a", "b"], prog="bmeld", default_web=True)


def test_graphics_defaults_to_probing_in_the_terminal():
    parser, args = parse(["a", "b"])
    resolve(parser, args, default_web=False)
    assert args.graphics == "auto"


def test_web_defaults_applied_only_once_the_front_end_is_known():
    parser, args = parse(["--web", "a", "b"])
    assert (args.port, args.bind, args.grace) == (None, None, None)
    resolve(parser, args, default_web=False)
    assert (args.port, args.bind, args.grace) == (0, "127.0.0.1", 60.0)


# --- shared validation, previously written out twice -------------------------


@pytest.mark.parametrize("argv", [
    [],                                  # nothing to compare
    ["a", "b", "c", "d"],                # too many positionals
    ["--diff", "a", "b", "c", "d"],      # long --diff group
    ["-o", "out", "--diff", "a", "b"],   # -o without a positional 3-way
    ["-o", "out", "a", "b"],             # -o with a 2-way
])
@pytest.mark.parametrize("prog,web", [("tmeld", False), ("bmeld", True)])
def test_both_front_ends_reject_the_same_arguments(argv, prog, web):
    rejects(argv, prog=prog, default_web=web)


def test_diff_specs_puts_positionals_first():
    """Upstream meldapp opens the positional comparison, then --diff groups."""
    parser, args = parse(["a", "b", "--diff", "x", "y", "--diff", "p", "q"])
    resolve(parser, args, default_web=False)
    assert diff_specs(args) == [
        (["a", "b"], None), (["x", "y"], None), (["p", "q"], None),
    ]


def test_shared_options_exist_in_both():
    for prog, web in (("tmeld", False), ("bmeld", True)):
        parser = build_parser(prog, web)
        args = parser.parse_args(
            ["--theme", "meld-dark", "--show-line-numbers", "a", "b"]
        )
        assert args.theme == "meld-dark" and args.show_line_numbers
