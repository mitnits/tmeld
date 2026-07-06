"""Engine fidelity tests for the vendored Meld matchers.

These pin down chunk behavior on inputs simple enough that the correct
answer is unambiguous, protecting against regressions in vendoring or
shimming. (True golden tests against a GTK Meld dump can be added later;
since this *is* Meld's code, these serve as the tripwire.)
"""

from tmeld.dump import chunks_as_pane_ranges, compute_differ


def chunks_for(*texts):
    differ = compute_differ(list(texts))
    return chunks_as_pane_ranges(differ, len(texts))


def test_identical_files_have_no_chunks():
    text = ["a", "b", "c"]
    differ = compute_differ([text, list(text)])
    assert differ.sequences_identical()
    assert differ.diff_count() == 0


def test_single_line_replace():
    chunks = chunks_for(["a", "x", "c"], ["a", "y", "c"])
    assert chunks == [{"tag": "replace", "ranges": [(1, 2), (1, 2)]}]


def test_insert_into_right_file():
    chunks = chunks_for(["a", "b"], ["a", "new", "b"])
    assert chunks == [{"tag": "insert", "ranges": [(1, 1), (1, 2)]}]


def test_delete_from_right_file():
    chunks = chunks_for(["a", "gone", "b"], ["a", "b"])
    assert chunks == [{"tag": "delete", "ranges": [(1, 2), (1, 1)]}]


def test_change_at_start_and_end():
    chunks = chunks_for(["x", "b", "c", "y"], ["X", "b", "c", "Y"])
    assert chunks == [
        {"tag": "replace", "ranges": [(0, 1), (0, 1)]},
        {"tag": "replace", "ranges": [(3, 4), (3, 4)]},
    ]


def test_three_way_no_conflict_left_change():
    # Only the left pane differs from base: exactly one chunk, no conflict
    base = ["a", "b", "c"]
    left = ["a", "B", "c"]
    right = list(base)
    differ = compute_differ([left, base, right])
    chunks = chunks_as_pane_ranges(differ, 3)
    assert differ.conflicts == []
    assert len(chunks) == 1
    assert chunks[0]["ranges"][0] == (1, 2)
    assert chunks[0]["ranges"][1] == (1, 2)


def test_three_way_conflict():
    # Both outer panes change the same base line differently: conflict
    base = ["a", "b", "c"]
    left = ["a", "LEFT", "c"]
    right = ["a", "RIGHT", "c"]
    differ = compute_differ([left, base, right])
    chunks = chunks_as_pane_ranges(differ, 3)
    assert differ.conflicts == [0]
    assert len(chunks) == 1
    assert chunks[0]["tag"] == "conflict"
    assert chunks[0]["ranges"] == [(1, 2), (1, 2), (1, 2)]


def test_three_way_same_change_both_sides_is_not_conflict():
    # Both outer panes made the *same* change: mergeable, not a conflict
    base = ["a", "b", "c"]
    left = ["a", "NEW", "c"]
    right = ["a", "NEW", "c"]
    differ = compute_differ([left, base, right])
    assert differ.conflicts == []


def test_line_cache_navigation():
    # locate_chunk gives (current, prev, next) — the basis for Alt+Up/Down
    left = ["a", "x", "c", "d", "y", "f"]
    right = ["a", "X", "c", "d", "Y", "f"]
    differ = compute_differ([left, right])
    current, prev, next_ = differ.locate_chunk(0, 1)
    assert current == 0 and prev is None and next_ == 1
    current, prev, next_ = differ.locate_chunk(0, 3)
    assert current is None and prev == 0 and next_ == 1
    current, prev, next_ = differ.locate_chunk(0, 4)
    assert current == 1 and prev == 0 and next_ is None


def test_ignore_blanks_mode():
    # A chunk that only adds blank lines disappears with ignore_blanks
    left = ["a", "b"]
    right = ["a", "", "", "b"]
    differ = compute_differ([left, right])
    assert differ.diff_count() == 1
    differ.ignore_blanks = True
    differ._update_merge_cache([left, right])
    assert differ.diff_count() == 0
