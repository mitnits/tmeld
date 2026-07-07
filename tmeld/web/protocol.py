"""bmeld wire protocol: Comparison/palette -> JSON payloads.

Parity note: everything the client renders comes from the same
Comparison methods the TUI renders from (single_changes,
inline_ranges, pair_changes) — the browser is a different painter of
identical engine output.

Messages (see BMELD.md):
  server -> client: init, chunks, saved, merged, error
  client -> server: buffers, save, merge_all, close
"""

from typing import List, Optional, Sequence

from tmeld.comparison import Comparison
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


def init_payload(
    comparison: Comparison,
    theme: Theme,
    labels: Optional[Sequence[Optional[str]]] = None,
) -> dict:
    n = comparison.num_panes
    labels = list(labels) if labels else [None] * n
    return {
        "type": "init",
        "num_panes": n,
        "paths": list(comparison.save_paths),
        "labels": [
            labels[i] or comparison.save_paths[i] for i in range(n)
        ],
        "texts": ["\n".join(lines) for lines in comparison.lines],
        "palette": palette_payload(theme),
    }


def chunks_payload(comparison: Comparison) -> dict:
    """The render model. Compact encodings:

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
        "panes": panes,
        "inline": inline_per_pane,
        "pairs": pairs,
        "diff_count": comparison.differ.diff_count(),
        "conflict_count": len(comparison.differ.conflicts),
    }
