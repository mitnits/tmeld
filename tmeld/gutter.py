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
pane document line r + scroll - 1. Gutter row 0 is that border: it carries
each pane's title rule across, the two halves meeting in the middle, rather
than showing the gutter's own grey above the ditch.

Tier 2 (graphics terminals): the gutter widens to [▶][image area][◀]
and Meld's anti-aliased linkmap connectors are painted into the middle
as a kitty-graphics or sixel image after each refresh (tmeld/linkmap).
The escape write bypasses Textual via app._driver.write — private API,
same precedent as pane._set_theme; re-audit on textual bump.
"""

from typing import Callable, Dict, List, Optional, Tuple

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from tmeld.linkmap import (
    GutterArrow,
    _hex_rgb,
    connectors_for_chunks,
    render_connectors,
)
from tmeld.overlay import GraphicsOverlay, OverlayImage
from tmeld.palette import Theme

PANE_BORDER_ROWS = 1

# Graphics mode draws the whole gutter as one image, arrows included: the icons
# are rasterized onto the connector fill exactly as Meld's ActionGutter paints
# its button over the chunk background. That frees the two cells the ▶/◀
# columns used to occupy, and one goes back into the curves (user, round 9).
GRAPHIC_IMAGE_COLS = 5

PushCallback = Callable[[int, int, int], None]  # (src, dst, chunk_index)
DeleteCallback = Callable[[int, int], None]  # (src, chunk_index)


class ActionGutter(GraphicsOverlay, Widget):
    DEFAULT_CSS = """
    ActionGutter {
        width: 3;
    }
    """

    ARROWS = ("▶", "◀")
    # U+2718 heavy ballot X: matches Meld's heavy meld-change-delete icon,
    # one cell wide, and (unlike ✖ U+2716 or ❌) has no emoji presentation, so
    # terminals render it as text in the chunk colour rather than a wide glyph.
    DELETE = "✘"
    BORDER = "│"
    BORDER_TOP = "─"

    def __init__(self, theme_def: Theme, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        # App pane indices that can't be written to. Drives the same rule as
        # Meld's ActionGutter._classify_change_actions: you can copy *out* of
        # a read-only pane, but never into one -- there, the only offer is to
        # delete the chunk from the source side, so the two sides agree.
        self.readonly: frozenset = frozenset()
        self.on_delete: Optional[DeleteCallback] = None
        # The adjacent DiffPanes this gutter sits between, and their app
        # pane indices — both set by the app after compose. Clicks push
        # between exactly this pair.
        self.panes: List = []
        self.pane_pair: Tuple[int, int] = (0, 1)
        # per column: {chunk start line: (merge-cache index, tag)}
        self._starts: List[Dict[int, tuple]] = [{}, {}]
        self.on_push: Optional[PushCallback] = None
        # Tier 2 wiring (set by the view)
        self.pair_changes: Optional[Callable[[], list]] = None
        self.current_chunk_starts: Optional[Callable[[], frozenset]] = None

    def on_mount(self) -> None:
        self._init_graphics()
        if self.graphics != "none":
            self.styles.width = GRAPHIC_IMAGE_COLS

    def set_starts(self, starts: List[Dict[int, tuple]]) -> None:
        """Per-column arrow positions from Comparison.action_starts:
        {start line: (chunk index, pane-oriented tag)}, entries only
        where that side has lines to push."""
        self._starts = starts
        self.refresh()

    def _doc_line(self, pane_index: int, y: int) -> int:
        pane = self.panes[pane_index]
        return y + int(pane.scroll_offset.y) - PANE_BORDER_ROWS

    def _action(self, col: int) -> Optional[str]:
        """"replace", "delete" or None for this column's button.

        Port of Meld's ActionGutter._classify_change_actions. Column 0 copies
        left->right, column 1 right->left; `_starts` already omits chunks with
        no lines on the source side, which is Meld's "insert" case.
        """
        src, dst = self.pane_pair[col], self.pane_pair[1 - col]
        if src in self.readonly and dst in self.readonly:
            return None
        if dst in self.readonly:
            return "delete"
        return "replace"

    def _border_row(self) -> Strip:
        """Carry the panes' title rule across the gutter.

        Gutter row 0 lines up with the panes' top border. Painting it in the
        gutter's grey left a notch above the ditch; instead each half of the
        span continues its own pane's rule, and the two meet in the middle.
        The colour is read from the pane, so a focused pane's rule stays lit
        all the way to the join.
        """
        width = self.size.width
        page = self.theme_def.page_bg
        left, right = (
            self.panes[i].styles.border_top[1].hex for i in (0, 1)
        )
        half = (width + 1) // 2
        return Strip([
            Segment(self.BORDER_TOP * half, Style(color=left, bgcolor=page)),
            Segment(self.BORDER_TOP * (width - half),
                    Style(color=right, bgcolor=page)),
        ])

    def render_line(self, y: int) -> Strip:
        # Meld's linkmap sits on the window background, not the page.
        page = Style(bgcolor=self.theme_def.gutter_bg)
        if len(self.panes) != 2:
            return Strip.blank(self.size.width, page)
        if y < PANE_BORDER_ROWS:
            return self._border_row()
        if self.graphics != "none":
            # The overlay image covers every cell, arrows and all.
            return Strip.blank(self.size.width, page)
        border_style = Style(
            color=self.theme_def.unknown_fg, bgcolor=self.theme_def.gutter_bg
        )
        doc_line = [self._doc_line(0, y), self._doc_line(1, y)]
        in_pane = [
            0 <= doc_line[i] < self.panes[i].document.line_count
            for i in (0, 1)
        ]

        segments = []
        for col in (0, 1):
            # Gutter row 0 lines up with the panes' title border, and maps to
            # the document line just above the viewport: never an action there.
            entry = (
                None if y < PANE_BORDER_ROWS
                else self._starts[col].get(doc_line[col])
            )
            action = self._action(col) if entry is not None else None
            if action is not None:
                _index, tag = entry
                fg = self.theme_def.chunk_fg(tag)
                glyph = self.DELETE if action == "delete" else self.ARROWS[col]
                seg = Segment(
                    glyph,
                    Style(color=fg, bgcolor=self.theme_def.gutter_bg, bold=True),
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
        if event.y < PANE_BORDER_ROWS:
            return
        col = 0 if event.x == 0 else 1
        entry = self._starts[col].get(self._doc_line(col, event.y))
        if entry is None:
            return
        index, _tag = entry
        action = self._action(col)
        if action == "replace" and self.on_push is not None:
            self.on_push(self.pane_pair[col], self.pane_pair[1 - col], index)
        elif action == "delete" and self.on_delete is not None:
            self.on_delete(self.pane_pair[col], index)

    # --- Tier 2 pixel-linkmap overlay ----------------------------------------

    def _render_overlay(self) -> Optional[OverlayImage]:
        if len(self.panes) != 2 or self.pair_changes is None:
            return None
        region = self.content_region
        image_cols = self.size.width
        image_rows = self.size.height - PANE_BORDER_ROWS
        if image_cols <= 0 or image_rows <= 0 or not region.width:
            return None
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
            None if self.graphics == "kitty" else self.theme_def.gutter_bg
        )
        # Meld's icon is 13x12 -- a shade wider than tall. Sizing the box to
        # one cell wide made it look spindly; drive the width off the height.
        arrow_h = max(8, min(cell_h - 4, 13))
        arrow_w = max(7, min(round(arrow_h * 1.1), width_px // 3))
        rgba = render_connectors(
            connectors, width_px, height_px, self.theme_def,
            background=background,
            arrows=self._arrows(cell_h, arrow_h, image_rows),
            arrow_size=(arrow_w, arrow_h),
        )
        return (
            rgba, width_px, height_px,
            region.y + PANE_BORDER_ROWS, region.x,
        )

    def _arrows(self, cell_h: int, arrow_h: int, image_rows: int):
        """Push/delete icons for the chunks currently on screen.

        The same rule as render_line: an entry exists only where that side has
        lines to push, and _action() decides between an arrow, a cross, and
        nothing at all.
        """
        out = []
        for col in (0, 1):
            action = self._action(col)
            if action is None:
                continue
            scroll = int(self.panes[col].scroll_offset.y)
            for doc_line, (_index, tag) in self._starts[col].items():
                row = doc_line - scroll
                if not 0 <= row < image_rows:
                    continue
                if tag not in self.theme_def.chunk:
                    continue
                out.append(GutterArrow(
                    kind="delete" if action == "delete" else "push",
                    on_right=(col == 1),
                    top=row * cell_h + (cell_h - arrow_h) / 2.0,
                    rgb=_hex_rgb(self.theme_def.chunk_fg(tag)),
                ))
        return out
