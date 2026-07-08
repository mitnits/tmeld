"""ChunkMap: Meld's overview map as a thin colored column.

Maps document lines to widget rows and paints chunk positions with the
theme's chunk colors — the terminal analogue of Meld's sourcemap strip.

Cell rows are coarse: in a big file each row covers dozens of lines,
so small chunks vanish into whichever chunk claims the row first. On
graphics terminals the same strip is repainted as a pixel image
(render_chunk_map) at cell-height resolution — single-line chunks in
huge files stay visible, and the viewport lens is properly translucent.
The cell rendering stays underneath as the coarse fallback.
"""

from typing import Callable, List, Optional, Tuple

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from tmeld.linkmap import render_chunk_map
from tmeld.overlay import GraphicsOverlay, OverlayImage
from tmeld.palette import Theme


class ChunkMap(GraphicsOverlay, Widget):
    DEFAULT_CSS = """
    ChunkMap {
        width: 1;
    }
    """

    on_jump: Optional[Callable[[int], None]] = None  # doc line callback

    def __init__(self, theme_def: Theme, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        self._chunks: List[Tuple[str, int, int]] = []  # (tag, start, end)
        self._total_lines = 1
        self.pane = None  # DiffPane whose viewport is indicated (set by app)

    def on_mount(self) -> None:
        self._init_graphics()

    def set_chunks(self, chunks: List[Tuple[str, int, int]], total: int) -> None:
        self._chunks = chunks
        self._total_lines = max(total, 1)
        self.refresh()
        self.refresh_overlay()

    def _render_overlay(self) -> Optional[OverlayImage]:
        region = self.content_region
        if not region.width or not region.height:
            return None
        cell_w, cell_h = self.cell_px
        width_px = region.width * cell_w
        height_px = region.height * cell_h
        viewport = None
        if self.pane is not None:
            scroll = float(self.pane.scroll_offset.y)
            page = self.pane.scrollable_content_region.height or 1
            viewport = (scroll, scroll + page)
        rgba = render_chunk_map(
            self._chunks,
            self._total_lines,
            width_px,
            height_px,
            self.theme_def,
            viewport=viewport,
        )
        return (rgba, width_px, height_px, region.y, region.x)

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
