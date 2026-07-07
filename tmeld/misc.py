"""Small pure helpers ported from upstream meld/misc.py.

Ported (not vendored) because upstream misc.py imports gi widgets;
all_same/shorten_names (misc.py:123), copy2/copytree (misc.py:259) and
merge_intervals/apply_text_filters (misc.py:315) are copied verbatim,
with only the gettext call in shorten_names dropped.
"""

import collections
import errno
import os
import shutil
from pathlib import PurePath
from typing import (
    AnyStr,
    Callable,
    List,
    Optional,
    Pattern,
    Sequence,
    Tuple,
)


def all_same(iterable: Sequence) -> bool:
    """Return True if all elements of the list are equal"""
    sample, has_no_sample = None, True
    for item in iterable or ():
        if has_no_sample:
            sample, has_no_sample = item, False
        elif sample != item:
            return False
    return True


def shorten_names(*names: str) -> List[str]:
    """Remove common parts of a list of paths

    For example, `('/tmp/foo1', '/tmp/foo2')` would be summarised as
    `('foo1', 'foo2')`. Paths that share a basename are distinguished
    by prepending an indicator, e.g., `('/a/b/c', '/a/d/c')` would be
    summarised to `['[b] c', '[d] c']`.
    """

    paths = [PurePath(n) for n in names]

    # Identify the longest common path among the list of path
    common = set(paths[0].parents)
    common = common.intersection(*(p.parents for p in paths))
    if not common:
        return list(names)
    common_parent = sorted(common, key=lambda p: -len(p.parts))[0]

    paths = [p.relative_to(common_parent) for p in paths]
    basenames = [p.name for p in paths]

    if all_same(basenames):

        def firstpart(path: PurePath) -> str:
            if len(path.parts) > 1 and path.parts[0]:
                return "[%s] " % path.parts[0]
            else:
                return ""

        return [firstpart(p) + p.name for p in paths]

    return [name or "[None]" for name in basenames]


def copy2(src: str, dst: str) -> None:
    """Like shutil.copy2 but ignores chmod errors, and copies symlinks as links
    See [Bug 568000] Copying to NTFS fails
    """
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))

    if os.path.islink(src) and os.path.isfile(src):
        if os.path.lexists(dst):
            os.unlink(dst)
        os.symlink(os.readlink(src), dst)
    elif os.path.isfile(src):
        shutil.copyfile(src, dst)
    else:
        raise OSError("Not a file")

    try:
        shutil.copystat(src, dst)
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.ENOTSUP):
            raise


def copytree(src: str, dst: str) -> None:
    """Similar to shutil.copytree, but always copies symlinks and doesn't
    error out if the destination path already exists.
    """
    # If the source tree is a symlink, duplicate the link and we're done.
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
        return

    try:
        os.mkdir(dst)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    names = os.listdir(src)
    for name in names:
        srcname = os.path.join(src, name)
        dstname = os.path.join(dst, name)
        if os.path.islink(srcname):
            os.symlink(os.readlink(srcname), dstname)
        elif os.path.isdir(srcname):
            copytree(srcname, dstname)
        else:
            copy2(srcname, dstname)

    try:
        shutil.copystat(src, dst)
    except OSError as e:
        if e.errno != errno.EPERM:
            raise


def merge_intervals(interval_list: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge a list of intervals

    Returns a list of itervals as 2-tuples with all overlapping
    intervals merged.

    interval_list must be a list of 2-tuples of integers representing
    the start and end of an interval.
    """

    if len(interval_list) < 2:
        return interval_list

    interval_deque = collections.deque(sorted(interval_list))
    merged_intervals = [interval_deque.popleft()]
    current_start, current_end = merged_intervals[-1]

    while interval_deque:
        new_start, new_end = interval_deque.popleft()

        if current_end >= new_end:
            continue

        if current_end < new_start:
            # Intervals do not overlap; create a new one
            merged_intervals.append((new_start, new_end))
        elif current_end < new_end:
            # Intervals overlap; extend the current one
            merged_intervals[-1] = (current_start, new_end)

        current_start, current_end = merged_intervals[-1]

    return merged_intervals


def apply_text_filters(
    txt: AnyStr,
    regexes: Sequence[Pattern],
    apply_fn: Optional[Callable[[int, int], None]] = None,
) -> AnyStr:
    """Apply text filters

    Text filters "regexes", resolved as regular expressions are applied
    to "txt". "txt" may be either strings or bytes, but the supplied
    regexes must match the type.

    "apply_fn" is a callable run for each filtered interval
    """
    empty_string = b"" if isinstance(txt, bytes) else ""
    newline = b"\n" if isinstance(txt, bytes) else "\n"

    filter_ranges = []
    for r in regexes:
        if not r:
            continue

        for match in r.finditer(txt):
            # If there are no groups in the match, use the whole match
            if not r.groups:
                span = match.span()
                if span[0] != span[1]:
                    filter_ranges.append(span)
                continue

            # If there are groups in the regex, include all groups that
            # participated in the match
            for i in range(r.groups):
                span = match.span(i + 1)
                if span != (-1, -1) and span[0] != span[1]:
                    filter_ranges.append(span)

    filter_ranges = merge_intervals(filter_ranges)

    if apply_fn:
        for start, end in reversed(filter_ranges):
            apply_fn(start, end)

    offset = 0
    result_txts = []
    for start, end in filter_ranges:
        assert txt[start:end].count(newline) == 0
        result_txts.append(txt[offset:start])
        offset = end
    result_txts.append(txt[offset:])
    return empty_string.join(result_txts)
