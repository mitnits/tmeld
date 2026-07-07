"""Small pure helpers ported from upstream meld/misc.py.

Ported (not vendored) because upstream misc.py imports gi widgets; these
functions are copied verbatim from upstream/meld/misc.py:120 with only
the gettext call dropped.
"""

from pathlib import PurePath
from typing import List, Sequence


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
