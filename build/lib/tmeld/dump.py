"""tmeld-dump: print Meld's chunk model for 2 or 3 files as JSON.

This is the Phase 1 proof of engine fidelity: the chunks printed here come
from Meld's own vendored Differ, so they are byte-for-byte the chunks GTK
Meld would display for the same inputs.
"""

import argparse
import json
import sys
from typing import List, Optional, Sequence, Tuple

from tmeld._vendor.meld.matchers.diffutil import Differ, opcode_reverse

Range = Optional[Tuple[int, int]]


def read_lines(path: str) -> List[str]:
    # Match GtkTextBuffer semantics closely enough for chunking: lines
    # without terminators. Meld compares decoded text; we default to UTF-8
    # with replacement rather than dying on mixed encodings.
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read().splitlines()


def compute_differ(texts: Sequence[List[str]]) -> Differ:
    differ = Differ()
    # set_sequences_iter is a cooperative generator (the UI drives it in
    # idle chunks); here we just run it to completion.
    for _ in differ.set_sequences_iter(list(texts)):
        pass
    return differ


def chunks_as_pane_ranges(differ: Differ, num_panes: int) -> List[dict]:
    """Flatten the merge cache into pane-ordered line ranges.

    Internally Meld stores chunks as text1->text0 and text1->text2 pairs
    (start_a/end_a in the *middle* sequence — for 2-way, sequences are
    [left, right] and the stored chunk is right->left). We normalise to
    a list of [start, end) ranges indexed by pane, left to right.
    """
    out = []
    for c0, c1 in differ.all_changes():
        base = c0 or c1
        ranges: List[Range] = [None] * num_panes
        if num_panes == 2:
            # sequences[1] == right pane is the "a" side of the stored chunk;
            # present tags in the conventional left->right orientation
            # (an "insert" is lines added in the right file), which is what
            # reverse_chunk gives Meld's left pane at display time.
            ranges[0] = (base.start_b, base.end_b)
            ranges[1] = (base.start_a, base.end_a)
            tag = opcode_reverse[base.tag]
        else:
            ranges[1] = (base.start_a, base.end_a)
            if c0 is not None:
                ranges[0] = (c0.start_b, c0.end_b)
            if c1 is not None:
                ranges[2] = (c1.start_b, c1.end_b)
            # A conflict tag on either side makes the merged chunk a conflict
            tags = {c.tag for c in (c0, c1) if c is not None}
            tag = "conflict" if "conflict" in tags else "/".join(sorted(tags))
        out.append({"tag": tag, "ranges": ranges})
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tmeld-dump",
        description="Dump Meld's diff chunk model for 2 or 3 files as JSON",
    )
    parser.add_argument("files", nargs="+", help="2 or 3 files, left to right")
    parser.add_argument(
        "--indent", type=int, default=2, help="JSON indent (default 2)"
    )
    args = parser.parse_args(argv)

    if len(args.files) not in (2, 3):
        parser.error("expected 2 or 3 files")

    texts = [read_lines(p) for p in args.files]
    differ = compute_differ(texts)

    result = {
        "files": list(args.files),
        "num_panes": len(args.files),
        "line_counts": [len(t) for t in texts],
        "identical": differ.sequences_identical(),
        "chunk_count": differ.diff_count(),
        "chunks": chunks_as_pane_ranges(differ, len(args.files)),
    }
    json.dump(result, sys.stdout, indent=args.indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
