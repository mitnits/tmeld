"""ActionGutter: the divider-plus-push-arrows column between panes.

Three cells wide, doing double duty as the pane divider (user-designed
layout, rounds 3-4 feedback):

    │ │   no action on this row — the divider runs through
    ▶ │   chunk pushable left->right
    ▶ ◀   chunk with lines on both sides — pushable either way
    │ ◀   chunk pushable right->left

Each column is a continuous vertical line, broken only where a push
arrow replaces it at a chunk start row. Arrows are drawn in the chunk
foreground color on the plain page background and are click targets.

Panes keep a 1-row top border (title), so gutter row r corresponds to
pane document line r + scroll - 1.

Tier 2 (graphics terminals): the gutter widens to [▶][image area][◀]
and Meld's anti-aliased linkmap connectors are painted into the middle
as a kitty-graphics or sixel image after each refresh (tmeld/linkmap).
The escape write bypasses Textual via app._driver.write — private API,
same precedent as pane._set_theme; re-audit on textual bump.
"""

import itertools
from typing import Callable, Dict, List, Optional, Tuple

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from tmeld.linkmap import (
    connectors_for_chunks,
    kitty_delete_escape,
    kitty_place_escape,
    render_connectors,
    sixel_escape,
)
from tmeld.palette import Theme

PANE_BORDER_ROWS = 1

# Width of the pixel-linkmap area, in cells, between the arrow columns.
# Meld's own linkmap is ~50px ≈ 5-6 cells including arrows; 4 + 2 arrow
# columns matches that (7 was too much — user round 7).
GRAPHIC_IMAGE_COLS = 4

PushCallback = Callable[[int, int, int], None]  # (src, dst, chunk_index)


class ActionGutter(Widget):
    DEFAULT_CSS = """
    ActionGutter {
        width: 3;
    }
    """

    ARROWS = ("▶", "◀")
    BORDER = "│"

    _image_ids = itertools.count(100)

    def __init__(self, theme_def: Theme, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        # The adjacent DiffPanes this gutter sits between, and their app
        # pane indices — both set by the app after compose. Clicks push
        # between exactly this pair.
        self.panes: List = []
        self.pane_pair: Tuple[int, int] = (0, 1)
        # per column: {chunk start line: (merge-cache index, tag)}
        self._starts: List[Dict[int, tuple]] = [{}, {}]
        self.on_push: Optional[PushCallback] = None
        # Tier 2 wiring (set by the view / read from the app on mount)
        self.graphics = "none"
        self.cell_px: Tuple[int, int] = (8, 16)
        self.pair_changes: Optional[Callable[[], list]] = None
        self.current_chunk_starts: Optional[Callable[[], frozenset]] = None
        self._image_id = next(self._image_ids)
        self._overlay_scheduled = False

    def on_mount(self) -> None:
        mode = getattr(self.app, "graphics", "none")
        if mode in ("kitty", "sixel"):
            self.graphics = mode
            self.cell_px = getattr(self.app, "cell_px", (8, 16))
            self.styles.width = 2 + GRAPHIC_IMAGE_COLS

    def on_unmount(self) -> None:
        if self.graphics == "kitty":
            self._write(kitty_delete_escape(self._image_id))

    def set_starts(self, starts: List[Dict[int, tuple]]) -> None:
        """Per-column arrow positions from Comparison.action_starts:
        {start line: (chunk index, pane-oriented tag)}, entries only
        where that side has lines to push."""
        self._starts = starts
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
            elif in_pane[col] and self.graphics == "none":
                seg = Segment(self.BORDER, border_style)
            else:
                seg = Segment(" ", page)
            segments.append(seg)
        # Layout: [col0] [image area / spacer] [col1]
        spacer = Segment(" " * max(1, self.size.width - 2), page)
        segments.insert(1, spacer)
        return Strip(segments)

    def on_click(self, event: events.Click) -> None:
        if 0 < event.x < self.size.width - 1:
            return
        col = 0 if event.x == 0 else 1
        entry = self._starts[col].get(self._doc_line(col, event.y))
        if entry is None or self.on_push is None:
            return
        index, _tag = entry
        self.on_push(self.pane_pair[col], self.pane_pair[1 - col], index)

    # --- Tier 2 pixel-linkmap overlay ----------------------------------------

    def refresh_overlay(self) -> None:
        """Schedule an overlay repaint after the next Textual frame (so
        the image lands on top of freshly painted cells)."""
        if self.graphics == "none" or self._overlay_scheduled:
            return
        self._overlay_scheduled = True
        self.app.call_after_refresh(self._paint_overlay)

    def clear_overlay(self) -> None:
        if self.graphics == "kitty":
            self._write(kitty_delete_escape(self._image_id))

    def _paint_overlay(self) -> None:
        self._overlay_scheduled = False
        if (
            self.graphics == "none"
            or len(self.panes) != 2
            or self.pair_changes is None
            or not self.display
        ):
            return
        region = self.content_region
        image_cols = self.size.width - 2
        image_rows = self.size.height - PANE_BORDER_ROWS
        if image_cols <= 0 or image_rows <= 0 or not region.width:
            return
        cell_w, cell_h = self.cell_px
        width_px, height_px = image_cols * cell_w, image_rows * cell_h
        starts = (
            self.current_chunk_starts()
            if self.current_chunk_starts is not None else frozenset()
        )
        connectors = connectors_for_chunks(
            self.pair_changes(),
            scroll_f_px=int(self.panes[0].scroll_offset.y) * cell_h,
            scroll_t_px=int(self.panes[1].scroll_offset.y) * cell_h,
            line_height_px=cell_h,
            current_chunk_starts=starts,
        )
        # Cull connectors entirely outside the viewport
        connectors = [
            c for c in connectors
            if max(c.f1, c.t1) >= 0 and min(c.f0, c.t0) <= height_px
        ]
        background = (
            None if self.graphics == "kitty"
            else (self.theme_def.page_bg or "#000000")
        )
        rgba = render_connectors(
            connectors, width_px, height_px, self.theme_def,
            background=background,
        )
        if self.graphics == "kitty":
            payload = kitty_place_escape(
                self._image_id, rgba, width_px, height_px
            )
        else:
            payload = sixel_escape(rgba, width_px, height_px)
        # Save cursor, jump to the image cell, paint, restore
        row, col = region.y + PANE_BORDER_ROWS, region.x + 1
        self._write(f"\x1b7\x1b[{row + 1};{col + 1}H{payload}\x1b8")

    def _write(self, escape: str) -> None:
        driver = getattr(self.app, "_driver", None)
        if driver is not None:
            try:
                driver.write(escape)
            except Exception:
                pass

    def on_resize(self) -> None:
        self.refresh_overlay()
