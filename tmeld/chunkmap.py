"""ChunkMap: Meld's overview map as a thin colored column.

Maps document lines to widget rows and paints chunk positions with the
theme's chunk colors — the terminal analogue of Meld's sourcemap strip.
"""

from typing import List, Tuple

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

from tmeld.palette import Theme


class ChunkMap(Widget):
    DEFAULT_CSS = """
    ChunkMap {
        width: 2;
    }
    """

    def __init__(self, theme_def: Theme, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        self._chunks: List[Tuple[str, int, int]] = []  # (tag, start, end)
        self._total_lines = 1

    def set_chunks(self, chunks: List[Tuple[str, int, int]], total: int) -> None:
        self._chunks = chunks
        self._total_lines = max(total, 1)
        self.refresh()

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
        if tag is None or tag not in self.theme_def.chunk:
            return Strip.blank(self.size.width)
        style = Style(bgcolor=self.theme_def.chunk[tag].line)
        return Strip([Segment(" " * self.size.width, style)])
