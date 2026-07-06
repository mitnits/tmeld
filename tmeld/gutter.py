"""ActionGutter: the divider-plus-push-arrows column between panes.

Three cells wide, doing double duty as the pane divider (user-designed
layout, round 3 feedback):

    │ │   no diff on this row — the divider runs through
    ▶     chunk pushable left->right (border opens across the chunk)
    ▶ ◀   chunk with lines on both sides — pushable either way
      ◀   chunk pushable right->left

The vertical border is *interrupted* wherever a chunk touches the row on
either side, echoing GTK Meld's open linkmap region. Arrows sit at chunk
start rows, drawn in the chunk foreground color on the plain page
background, and are click targets for the push.

Panes keep a 1-row top border (title), so gutter row r corresponds to
pane document line r + scroll - 1.
"""

from typing import Callable, Dict, List, Optional, Set

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from tmeld.palette import Theme

PANE_BORDER_ROWS = 1

PushCallback = Callable[[int, int, int], None]  # (src, dst, chunk_index)


class ActionGutter(Widget):
    DEFAULT_CSS = """
    ActionGutter {
        width: 3;
    }
    """

    ARROWS = ("▶", "◀")
    BORDER = "│"

    def __init__(self, theme_def: Theme, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        self.panes: List = []  # DiffPanes, set by the app after compose
        # per pane: {chunk start line: (chunk_index, tag)}
        self._starts: List[Dict[int, tuple]] = [{}, {}]
        # per pane: document lines any chunk touches (incl. insert points)
        self._covered: List[Set[int]] = [set(), set()]
        self.on_push: Optional[PushCallback] = None

    def set_chunks(self, pane_chunks: List[list]) -> None:
        """pane_chunks[i] are pane-oriented chunks from single_changes(i);
        arrows appear only where the pane actually has lines to push."""
        starts: List[Dict[int, tuple]] = [{}, {}]
        covered: List[Set[int]] = [set(), set()]
        for pane, chunks in enumerate(pane_chunks):
            for index, c in enumerate(chunks):
                if c.start_a != c.end_a:
                    starts[pane][c.start_a] = (index, c.tag)
                    covered[pane].update(range(c.start_a, c.end_a))
                else:
                    # Insert point: no lines to push from this side, but
                    # the divider still opens at the splice row
                    covered[pane].add(c.start_a)
        self._starts = starts
        self._covered = covered
        self.refresh()

    def _doc_line(self, pane_index: int, y: int) -> int:
        pane = self.panes[pane_index]
        return y + int(pane.scroll_offset.y) - PANE_BORDER_ROWS

    def render_line(self, y: int) -> Strip:
        page = Style(bgcolor=self.theme_def.page_bg)
        if len(self.panes) != 2:
            return Strip.blank(self.size.width, page)
        border_style = Style(
            color=self.theme_def.unknown_fg, bgcolor=self.theme_def.page_bg
        )
        doc_line = [self._doc_line(0, y), self._doc_line(1, y)]
        in_pane = [
            0 <= doc_line[i] < self.panes[i].document.line_count
            for i in (0, 1)
        ]
        chunk_row = any(doc_line[i] in self._covered[i] for i in (0, 1))

        segments = []
        for col in (0, 1):
            entry = self._starts[col].get(doc_line[col])
            if entry is not None:
                _index, tag = entry
                chunk_style = self.theme_def.chunk.get(tag)
                fg = chunk_style.fg if chunk_style else None
                seg = Segment(
                    self.ARROWS[col],
                    Style(color=fg, bgcolor=self.theme_def.page_bg, bold=True),
                )
            elif chunk_row or not in_pane[col]:
                seg = Segment(" ", page)
            else:
                seg = Segment(self.BORDER, border_style)
            segments.append(seg)
        # Layout: [col0] [spacer] [col1]
        segments.insert(1, Segment(" ", page))
        return Strip(segments)

    def on_click(self, event: events.Click) -> None:
        if event.x == 1:
            return
        col = 0 if event.x == 0 else 1
        entry = self._starts[col].get(self._doc_line(col, event.y))
        if entry is None or self.on_push is None:
            return
        index, _tag = entry
        self.on_push(col, 1 - col, index)
