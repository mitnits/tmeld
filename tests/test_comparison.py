"""Tests for the comparison model (line tags + inline ranges)."""

import pytest

from tmeld.comparison import Comparison


@pytest.fixture
def files(tmp_path):
    def make(a_lines, b_lines):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("\n".join(a_lines) + "\n", encoding="utf-8")
        b.write_text("\n".join(b_lines) + "\n", encoding="utf-8")
        return Comparison([str(a), str(b)])

    return make


def test_line_tags_replace(files):
    comp = files(["a", "xxx", "c"], ["a", "yyy", "c"])
    assert comp.line_tags(0) == {1: "replace"}
    assert comp.line_tags(1) == {1: "replace"}


def test_line_tags_orientation(files):
    # Line added in the right file: the engine's stored orientation is
    # right->left, so the right pane tags its extra line "delete" and the
    # left pane sees nothing (empty range). This is Meld's own model —
    # visually it still paints green, because delete fills are aliased
    # to insert colors (upstream style.py get_common_theme).
    comp = files(["a", "b"], ["a", "new", "b"])
    assert comp.line_tags(0) == {}
    assert comp.line_tags(1) == {1: "delete"}

    # And the mirror case: line only in the left file
    comp = files(["a", "gone", "b"], ["a", "b"])
    assert comp.line_tags(0) == {1: "delete"}
    assert comp.line_tags(1) == {}


def test_inline_ranges_single_line_replace(files):
    comp = files(["abcdef"], ["abXdef"])
    inline = comp.inline_ranges()
    # Both panes highlight the changed character region on line 0
    assert 0 in inline[0] and 0 in inline[1]
    for pane in (0, 1):
        (start, end), = inline[pane][0]
        assert start <= 2 < end


def test_inline_ranges_multiline_chunk(files):
    comp = files(["aaa", "bXb", "ccc"], ["aaa", "bYb", "cZc"])
    inline = comp.inline_ranges()
    # Changes on document lines 1 and 2, columns within each line
    assert set(inline[0]) == {1, 2}
    assert set(inline[1]) == {1, 2}
    for pane in (0, 1):
        for line, ranges in inline[pane].items():
            for start, end in ranges:
                assert 0 <= start < end <= 3


def test_inline_skips_equal_chunks(files):
    comp = files(["same", "same2"], ["same", "same2"])
    assert comp.inline_ranges() == [{}, {}]
