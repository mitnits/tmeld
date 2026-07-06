"""ChunkMap: Meld's overview map as a thin colored column.

Maps document lines to widget rows and paints chunk positions with the
theme's chunk colors — the terminal analogue of Meld's sourcemap strip.
"""

from typing import Callable, List, Optional, Tuple

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from tmeld.palette import Theme


class ChunkMap(Widget):
    DEFAULT_CSS = """
    ChunkMap {
        width: 2;
    }
    """

    on_jump: Optional[Callable[[int], None]] = None  # doc line callback

    def __init__(self, theme_def: Theme, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        self._chunks: List[Tuple[str, int, int]] = []  # (tag, start, end)
        self._total_lines = 1
        self.pane = None  # DiffPane whose viewport is indicated (set by app)

    def set_chunks(self, chunks: List[Tuple[str, int, int]], total: int) -> None:
        self._chunks = chunks
        self._total_lines = max(total, 1)
        self.refresh()

    def _viewport_rows(self, height: int) -> Tuple[float, float]:
        pane = self.pane
        if pane is None:
            return (-1.0, -1.0)
        scroll = float(pane.scroll_offset.y)
        page = pane.scrollable_content_region.height or 1
        top = scroll * height / self._total_lines
        bottom = (scroll + page) * height / self._total_lines
        return top, max(bottom, top + 1)

    def render_line(self, y: int) -> Strip:
        height = max(self.size.height, 1)
        # Which document lines does this row cover?
        row_start = y * self._total_lines / height
        row_end = (y + 1) * self._total_lines / height
        tag = None
        for chunk_tag, start, end in self._chunks:
            # insert chunks have start == end; give them presence
            end = max(end, start + 1)
            if start < row_end and end > row_start:
                tag = chunk_tag
                break
        if tag is not None and tag in self.theme_def.chunk:
            color = self.theme_def.chunk[tag].line
        else:
            color = self.theme_def.page_bg
        # Meld's map-overlay marks the visible region
        view_top, view_bottom = self._viewport_rows(height)
        if view_top <= y < view_bottom:
            color = self.theme_def.overlay(color)
        return Strip([Segment(" " * self.size.width, Style(bgcolor=color))])

    def on_click(self, event: events.Click) -> None:
        if self.on_jump is None:
            return
        height = max(self.size.height, 1)
        line = int(event.y * self._total_lines / height)
        self.on_jump(line)
