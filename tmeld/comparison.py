"""Comparison model: files -> Differ -> per-pane render instructions.

Pure logic, no UI imports. The pane widgets consume line_backgrounds and
inline_ranges dictionaries produced here; tags and chunk boundaries come
straight from the vendored Meld engine.
"""

from typing import Dict, List, Sequence, Tuple

from tmeld._vendor.meld.matchers.diffutil import Differ
from tmeld._vendor.meld.matchers.myers import InlineMyersSequenceMatcher

# Skip inline highlighting for pathological chunks, as Meld does (it uses
# a time-based cap; we use a size cap for determinism).
INLINE_CHAR_CAP = 20_000

InlineRanges = Dict[int, List[Tuple[int, int]]]


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


class Comparison:
    """A 2-way (later 3-way) comparison of in-memory line sequences.

    Lines are mutable: the UI updates them on edits and calls recompute()
    to rebuild the chunk model (a fresh matcher run — identical chunks to
    Meld's incremental change_sequence path, which can replace this later
    for very large files).
    """

    def __init__(self, paths: Sequence[str]):
        assert len(paths) == 2, "3-way arrives in Phase 5"
        self.paths = list(paths)
        texts = [read_text(p) for p in paths]
        self.lines: List[List[str]] = [t.splitlines() for t in texts]
        # Preserve on save; empty files count as newline-terminated
        self.trailing_newline = [t.endswith("\n") or not t for t in texts]
        self.differ = Differ()
        self.recompute()

    def recompute(self) -> None:
        differ = Differ()
        for _ in differ.set_sequences_iter(self.lines):
            pass
        self.differ = differ

    def save(self, pane: int) -> None:
        text = "\n".join(self.lines[pane])
        if self.trailing_newline[pane] and text:
            text += "\n"
        with open(self.paths[pane], "w", encoding="utf-8", newline="") as f:
            f.write(text)

    @property
    def num_panes(self) -> int:
        return len(self.paths)

    def pane_chunks(self, pane: int):
        """Chunks with coordinates and tag oriented to this pane."""
        return list(self.differ.single_changes(pane))

    def pair_chunks(self, from_pane: int, to_pane: int):
        """Chunks oriented from_pane -> to_pane, for scroll sync."""
        return list(self.differ.pair_changes(from_pane, to_pane))

    def line_tags(self, pane: int) -> Dict[int, str]:
        """Map document line -> chunk tag, for background painting."""
        tags: Dict[int, str] = {}
        for chunk in self.pane_chunks(pane):
            for line in range(chunk.start_a, chunk.end_a):
                tags[line] = chunk.tag
        return tags

    def inline_ranges(self) -> List[InlineRanges]:
        """Per-pane {line: [(col_start, col_end), ...]} intra-line diffs.

        Mirrors Meld's inline highlighting: for each replace chunk, run
        the inline Myers matcher over the chunk texts and mark non-equal
        opcode ranges on both panes.
        """
        per_pane: List[InlineRanges] = [{} for _ in range(self.num_panes)]
        # Stored chunks: a-side is pane 1, b-side is pane 0
        for c0, _c1 in self.differ.all_changes():
            chunk = c0
            if chunk is None or chunk.tag != "replace":
                continue
            a_lines = self.lines[1][chunk.start_a:chunk.end_a]
            b_lines = self.lines[0][chunk.start_b:chunk.end_b]
            text_a = "\n".join(a_lines)
            text_b = "\n".join(b_lines)
            if len(text_a) + len(text_b) > INLINE_CHAR_CAP:
                continue
            matcher = InlineMyersSequenceMatcher(None, text_a, text_b)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                _add_char_range(per_pane[1], a_lines, chunk.start_a, i1, i2)
                _add_char_range(per_pane[0], b_lines, chunk.start_b, j1, j2)
        return per_pane


def _add_char_range(
    ranges: InlineRanges,
    chunk_lines: List[str],
    first_line: int,
    lo: int,
    hi: int,
) -> None:
    """Convert a char-offset range within joined chunk text to per-line
    column ranges (the join uses single \\n separators)."""
    if hi <= lo:
        return
    offset = 0
    for i, line in enumerate(chunk_lines):
        line_start = offset
        line_end = offset + len(line)
        start = max(lo, line_start)
        end = min(hi, line_end)
        if start < end:
            ranges.setdefault(first_line + i, []).append(
                (start - line_start, end - line_start)
            )
        offset = line_end + 1  # the "\n"
        if offset > hi:
            break
