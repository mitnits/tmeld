"""Tests for the ported sync-scroll math (PARITY.md §4)."""

from collections import namedtuple

from tmeld.scroll import calc_syncpoint, sync_scroll_target

Chunk = namedtuple("Chunk", "tag start_a end_a start_b end_b")


def test_syncpoint_at_top_is_zero():
    assert calc_syncpoint(0, 40, 1000) == 0.0


def test_syncpoint_at_bottom_is_one():
    # max scroll = upper - page
    assert calc_syncpoint(960, 40, 1000) == 1.0


def test_syncpoint_mid_document_is_half():
    assert calc_syncpoint(500, 40, 1000) == 0.5


def test_syncpoint_ramps_through_first_half_screen():
    assert calc_syncpoint(10, 40, 1000) == 0.25


def test_identical_files_scroll_identically():
    for value in (0, 5, 50, 500, 960):
        assert sync_scroll_target(value, 40, 1000, 40, 1000, []) == value


def test_insert_shifts_lines_below():
    # Other pane has 10 extra lines inserted at line 100
    chunks = [Chunk("insert", 100, 100, 100, 110)]
    # Well below the insert (and away from doc edges): shifted by 10
    assert sync_scroll_target(480, 40, 1000, 40, 1010, chunks) == 490
    # Well above the insert: unshifted
    assert sync_scroll_target(30, 40, 1000, 40, 1010, chunks) == 30


def test_short_file_clamps_to_valid_scroll_range():
    chunks = [Chunk("delete", 500, 900, 500, 500)]
    target = sync_scroll_target(700, 40, 1000, 40, 600, chunks)
    assert 0 <= target <= 600 - 40
