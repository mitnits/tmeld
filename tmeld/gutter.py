"""ActionGutter: the clickable push-arrow column between panes.

Terminal analogue of Meld's action gutter: an arrow at each chunk start,
on the side that has the lines, pointing at the pane the chunk can be
pushed into. Click an arrow to push that chunk.

Column 0 aligns with the left pane (arrows push left->right), column 1
with the right pane (arrows push right->left). Panes have a 1-row border,
so gutter row r corresponds to pane document line r + scroll - 1.
"""

from typing import Callable, Dict, List, Optional

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
        width: 2;
    }
    """

    ARROWS = ("▶", "◀")

    def __init__(self, theme_def: Theme, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        self.panes: List = []  # DiffPanes, set by the app after compose
        # per pane: {chunk start line: (chunk_index, tag)}
        self._starts: List[Dict[int, tuple]] = [{}, {}]
        self.on_push: Optional[PushCallback] = None

    def set_chunks(self, pane_chunks: List[list]) -> None:
        """pane_chunks[i] are pane-oriented chunks from single_changes(i);
        arrows appear only where the pane actually has lines to push."""
        self._starts = [
            {
                c.start_a: (index, c.tag)
                for index, c in enumerate(chunks)
                if c.start_a != c.end_a
            }
            for chunks in pane_chunks
        ]
        self.refresh()

    def _doc_line(self, pane_index: int, y: int) -> int:
        pane = self.panes[pane_index]
        return y + int(pane.scroll_offset.y) - PANE_BORDER_ROWS

    def render_line(self, y: int) -> Strip:
        if len(self.panes) != 2:
            return Strip.blank(self.size.width)
        segments = []
        for col in (0, 1):
            entry = self._starts[col].get(self._doc_line(col, y))
            if entry is None:
                segments.append(Segment(" "))
            else:
                _index, tag = entry
                chunk_style = self.theme_def.chunk.get(tag)
                fg = chunk_style.fg if chunk_style else None
                segments.append(Segment(self.ARROWS[col], Style(color=fg)))
        return Strip(segments)

    def on_click(self, event: events.Click) -> None:
        col = 0 if event.x == 0 else 1
        entry = self._starts[col].get(self._doc_line(col, event.y))
        if entry is None or self.on_push is None:
            return
        index, _tag = entry
        self.on_push(col, 1 - col, index)
