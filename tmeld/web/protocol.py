"""bmeld wire protocol: Comparison/DirComparison/palette -> JSON.

Parity note: everything the client renders comes from the same engine
objects the TUI renders from (Comparison.single_changes/inline_ranges/
pair_changes, dircompare.DirEntry) — the browser is a different
painter of identical engine output.

Messages (see BMELD.md):
  server -> client: init, tab_added, chunks, tree, saved, merged, error
  client -> server: buffers, save, merge_all, open_file, close_tab,
                    scan, close

Every tab-scoped message carries a `tab` id.
"""

from typing import List, Optional, Sequence

from tmeld.comparison import Comparison, devnull_panes
from tmeld.dircompare import DirComparison, DirEntry
from tmeld.misc import shorten_names
from tmeld.palette import Theme


def palette_payload(theme: Theme) -> dict:
    return {
        "name": theme.name,
        "dark": theme.dark,
        "page_bg": theme.page_bg,
        "text_fg": theme.text_fg,
        "unknown_fg": theme.unknown_fg,
        "inline_bg": theme.inline_bg,
        "current_line_bg": theme.current_line_bg,
        "selection_bg": theme.selection_bg,
        "overlay_color": theme.overlay_color,
        "overlay_alpha": theme.overlay_alpha,
        "chunk": {
            tag: {
                "fg": style.fg,
                "fill": style.fill,
                "line": style.line,
                # current-chunk emphasis fill, blended server-side so
                # the client needs no color math
                "emphasis": theme.chunk_fill(tag, emphasized=True),
            }
            for tag, style in theme.chunk.items()
        },
    }


def file_tab_payload(
    tab_id: str,
    comparison: Comparison,
    labels: Optional[Sequence[Optional[str]]] = None,
    readonly: Sequence[int] = (),
    tab_title: Optional[str] = None,
) -> dict:
    n = comparison.num_panes
    labels = list(labels) if labels else [None] * n
    # A /dev/null side (p4/git's "absent file") is read-only: no content to save.
    readonly = sorted(set(readonly) | devnull_panes(comparison.paths))
    return {
        "id": tab_id,
        "kind": "file",
        "label": tab_title or " — ".join(shorten_names(*comparison.save_paths)),
        "num_panes": n,
        "paths": list(comparison.save_paths),
        "labels": [labels[i] or comparison.save_paths[i] for i in range(n)],
        "readonly": list(readonly),
        "texts": ["\n".join(lines) for lines in comparison.lines],
    }


def vc_tab_payload(tab_id: str, comparison) -> dict:
    """comparison: tmeld.vcview.VcComparison."""
    import os

    location = comparison.location
    return {
        "id": tab_id,
        "kind": "vc",
        "label": f"{os.path.basename(location.rstrip(os.sep))} "
                 f"[{comparison.vc.NAME}]",
        "num_panes": 1,
        "roots": [location],
        "commit_prefill": comparison.vc.get_commit_message_prefill() or "",
    }


def dir_tab_payload(tab_id: str, comparison: DirComparison) -> dict:
    return {
        "id": tab_id,
        "kind": "dir",
        "label": " — ".join(shorten_names(*comparison.roots)),
        "num_panes": comparison.num_panes,
        "roots": list(comparison.roots),
    }


def _entry_payload(entry: DirEntry) -> dict:
    return {
        "names": list(entry.names),
        "paths": list(entry.paths),
        "exists": list(entry.exists),
        "isdir": entry.isdir,
        "state": entry.state,
        "different": entry.different,
        "children": [_entry_payload(c) for c in entry.children],
    }


def tree_payload(tab_id: str, comparison) -> dict:
    """Works for DirComparison and VcComparison (both expose a
    root_entry DirEntry tree after scanning)."""
    root = comparison.root_entry
    differences = 0
    if root is not None:
        differences = sum(
            1 for e in root.walk() if e.different and not e.isdir
        )
    return {
        "type": "tree",
        "tab": tab_id,
        "root": _entry_payload(root) if root is not None else None,
        "differences": differences,
    }


def chunks_payload(tab_id: str, comparison: Comparison) -> dict:
    """The file-tab render model. Compact encodings:

    panes[i]  = [[tag, start, end], ...]        (single_changes, pane-
                oriented; fills, nav, emphasis, conflicts derive here)
    inline[i] = [[line, col_start, col_end], ...]
    pairs[k]  = [[tag, start_a, end_a, start_b, end_b], ...]
                (pair_changes k -> k+1; connectors, gutters, actions)
    """
    panes: List[list] = []
    for i in range(comparison.num_panes):
        panes.append(
            [[c.tag, c.start_a, c.end_a] for c in comparison.pane_chunks(i)]
        )
    inline_per_pane = []
    for ranges in comparison.inline_ranges():
        flat = []
        for line, spans in sorted(ranges.items()):
            for start, end in spans:
                flat.append([line, start, end])
        inline_per_pane.append(flat)
    pairs = []
    for k in range(comparison.num_panes - 1):
        pairs.append([
            [c.tag, c.start_a, c.end_a, c.start_b, c.end_b]
            for c in comparison.pair_chunks(k, k + 1)
        ])
    return {
        "type": "chunks",
        "tab": tab_id,
        "panes": panes,
        "inline": inline_per_pane,
        "pairs": pairs,
        "diff_count": comparison.differ.diff_count(),
        "conflict_count": len(comparison.differ.conflicts),
    }
