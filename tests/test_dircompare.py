"""Folder-comparison model tests (states, filters, nesting, symlinks)."""

import os

import pytest

from tmeld.dircompare import (
    STATE_ERROR,
    STATE_MODIFIED,
    STATE_NEW,
    STATE_NONEXIST,
    STATE_NORMAL,
    DirComparison,
    make_name_filters,
)


@pytest.fixture
def two_dirs(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    return left, right


def entries_by_name(root_entry):
    return {e.names[0]: e for e in root_entry.children}


def test_states(two_dirs):
    left, right = two_dirs
    (left / "same.txt").write_text("hello\n")
    (right / "same.txt").write_text("hello\n")
    (left / "changed.txt").write_text("aaa\n")
    (right / "changed.txt").write_text("bbb\n")
    (left / "left-only.txt").write_text("x\n")
    (right / "right-only.txt").write_text("y\n")

    comp = DirComparison([str(left), str(right)])
    rows = entries_by_name(comp.scan())

    assert rows["same.txt"].state == STATE_NORMAL
    assert not rows["same.txt"].different
    assert rows["changed.txt"].state == STATE_MODIFIED
    assert rows["left-only.txt"].state == STATE_NEW
    assert rows["left-only.txt"].pane_state(0) == STATE_NEW
    assert rows["left-only.txt"].pane_state(1) == STATE_NONEXIST
    assert rows["right-only.txt"].pane_state(0) == STATE_NONEXIST


def test_rows_sorted_dirs_first(two_dirs):
    left, right = two_dirs
    (left / "zz.txt").write_text("x\n")
    (right / "zz.txt").write_text("x\n")
    (left / "aa").mkdir()
    (right / "aa").mkdir()

    comp = DirComparison([str(left), str(right)])
    names = [e.names[0] for e in comp.scan().children]
    assert names == ["aa", "zz.txt"]


def test_nested_dir_states(two_dirs):
    left, right = two_dirs
    (left / "sub").mkdir()
    (right / "sub").mkdir()
    (left / "sub" / "deep.txt").write_text("one\n")
    (right / "sub" / "deep.txt").write_text("two\n")
    (left / "only-here").mkdir()
    (left / "only-here" / "new.txt").write_text("n\n")

    comp = DirComparison([str(left), str(right)])
    rows = entries_by_name(comp.scan())

    sub = rows["sub"]
    assert sub.isdir and sub.state == STATE_NORMAL  # dirs both present = same
    assert entries_by_name(sub)["deep.txt"].state == STATE_MODIFIED

    only = rows["only-here"]
    assert only.state == STATE_NEW
    assert only.pane_state(1) == STATE_NONEXIST
    # children of a one-sided dir still scan (missing pane skipped)
    assert entries_by_name(only)["new.txt"].state == STATE_NEW


def test_default_name_filters_hide_noise(two_dirs):
    left, right = two_dirs
    for d in (left, right):
        (d / "keep.py").write_text("k\n")
        (d / "junk.pyc").write_text("j\n")
        (d / "note~").write_text("b\n")
        (d / ".git").mkdir()
        (d / ".git" / "config").write_text("c\n")

    comp = DirComparison([str(left), str(right)])
    names = {e.names[0] for e in comp.scan().children}
    assert names == {"keep.py"}


def test_no_filters_shows_everything(two_dirs):
    left, right = two_dirs
    for d in (left, right):
        (d / "junk.pyc").write_text("j\n")

    comp = DirComparison([str(left), str(right)], name_filters=[])
    names = {e.names[0] for e in comp.scan().children}
    assert names == {"junk.pyc"}


def test_walk_and_different(two_dirs):
    left, right = two_dirs
    (left / "sub").mkdir()
    (right / "sub").mkdir()
    (left / "sub" / "diff.txt").write_text("a\n")
    (right / "sub" / "diff.txt").write_text("b\n")
    (left / "same.txt").write_text("s\n")
    (right / "same.txt").write_text("s\n")

    comp = DirComparison([str(left), str(right)])
    root = comp.scan()
    all_names = [e.names[0] for e in root.walk()]
    assert all_names == ["sub", "diff.txt", "same.txt"]
    differing = [e.names[0] for e in root.walk() if e.different and not e.isdir]
    assert differing == ["diff.txt"]


def test_dangling_symlink_is_error_row(two_dirs):
    left, right = two_dirs
    os.symlink(str(left / "nowhere"), str(left / "dangling"))
    (right / "x.txt").write_text("x\n")
    (left / "x.txt").write_text("x\n")

    comp = DirComparison([str(left), str(right)])
    rows = comp.scan().children
    errors = [e for e in rows if e.state == STATE_ERROR]
    assert len(errors) == 1 and "Dangling symlink" in errors[0].error


def test_three_way_states(tmp_path):
    dirs = []
    for name in ("a", "b", "c"):
        d = tmp_path / name
        d.mkdir()
        dirs.append(d)
    for d in dirs:
        (d / "same.txt").write_text("s\n")
    (dirs[0] / "two-of-three.txt").write_text("t\n")
    (dirs[1] / "two-of-three.txt").write_text("t\n")

    comp = DirComparison([str(d) for d in dirs])
    rows = entries_by_name(comp.scan())
    assert rows["same.txt"].state == STATE_NORMAL
    row = rows["two-of-three.txt"]
    # present pair identical -> NEW (upstream all_present_same logic)
    assert row.state == STATE_NEW
    assert row.pane_state(2) == STATE_NONEXIST


def test_rejects_missing_root(tmp_path):
    with pytest.raises(NotADirectoryError):
        DirComparison([str(tmp_path), str(tmp_path / "nope")])


def test_make_name_filters_patterns():
    filters = make_name_filters()
    active = {f.label for f in filters if f.active}
    assert "Backups" in active and "Media" not in active
