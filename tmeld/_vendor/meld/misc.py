"""Hand-written shim: vendored code's `meld.misc` imports resolve here.

Upstream misc.py drags in gi widgets, so the pure helpers vendored code
actually uses are ported verbatim in tmeld/misc.py and re-exported.
"""

from tmeld.misc import (  # noqa: F401
    all_same,
    apply_text_filters,
    copy2,
    copytree,
    get_hide_window_startupinfo,
    merge_intervals,
    shorten_names,
)
