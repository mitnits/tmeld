"""DiffPane: a TextArea that renders Meld chunk styling.

Rendering hooks validated by spikes/spike_textarea.py: get_line() is the
seam for both whole-row chunk backgrounds (padded to widget width, styled
under) and inline highlight ranges (styled over). One private attribute
(wrapped_document._offset_to_line_info) is intentionally NOT needed here —
line indexes arrive directly in get_line. Textual is pinned to 8.x.
"""

from typing import Dict, List, Optional, Sequence, Tuple

from rich.style import Style
from textual.binding import Binding
from textual.message import Message
from textual.geometry import Region, Spacing
from textual.widget import Widget
from textual.widgets import TextArea
from textual.widgets.text_area import TextAreaTheme

from tmeld.linkmap import INSERT_MARKER_PX, _hex_rgb, render_insert_marker
from tmeld.overlay import GraphicsOverlay, OverlayImage
from tmeld.palette import Theme

PANE_BORDER_ROWS = 1


def build_textarea_theme(theme_def: Theme) -> TextAreaTheme:
    """TextArea styling from the Meld palette.

    cursor_line_style is deliberately None: TextArea paints it over our
    chunk backgrounds, making changed lines look unchanged when the
    cursor lands on them. Instead the current line's *number* gets
    Meld's current-line tint (user feedback from the first test drive).
    """
    theme = TextAreaTheme(
        name=f"tmeld-{theme_def.name}",
        base_style=Style(color=theme_def.text_fg, bgcolor=theme_def.page_bg),
        gutter_style=Style(color=theme_def.unknown_fg, bgcolor=theme_def.page_bg),
        cursor_style=Style(color=theme_def.page_bg, bgcolor=theme_def.text_fg),
        cursor_line_style=None,
        cursor_line_gutter_style=Style(
            color=theme_def.current_line_fg,
            bgcolor=theme_def.current_line_bg,
            bold=True,
        ),
        selection_style=Style(
            color=theme_def.selection_fg, bgcolor=theme_def.selection_bg
        ),
    )
    return theme


class DiffPane(GraphicsOverlay, TextArea):
    """One comparison pane. Editable since Phase 3."""

    # Leftmost pane: the scrollbar goes on the far left so it never lands
    # between a chunk fill and the linkmap it connects to. A class attribute,
    # because scrollbar_gutter is consulted from Widget.__init__.
    scrollbar_on_left: bool = False

    BINDINGS = [
        # Meld's redo (TextArea ships ctrl+z/ctrl+y; Meld muscle memory
        # expects ctrl+shift+z as well)
        Binding("ctrl+shift+z", "redo", "Redo", show=False),
    ]

    class Scrolled(Message):
        def __init__(self, pane: "DiffPane", value: float) -> None:
            super().__init__()
            self.pane = pane
            self.value = value

    def __init__(self, pane_index: int, theme_def: Theme, **kwargs) -> None:
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("soft_wrap", False)
        super().__init__(**kwargs)
        self.pane_index = pane_index
        self.theme_def = theme_def
        self._line_styles: Dict[int, Style] = {}
        self._line_tags: Dict[int, str] = {}
        self._emphasized: set = set()
        self._inline_styles: Dict[int, List[Tuple[int, int]]] = {}
        self._inline_style = Style(bgcolor=theme_def.inline_bg)
        ta_theme = build_textarea_theme(theme_def)
        self.register_theme(ta_theme)
        self.theme = ta_theme.name
        self._sync_target: int | None = None
        # (line, tag) for chunks with no lines on this side -- the other pane
        # is adding text there. Drawn as Meld's thin line, Tier 2 only.
        self._insert_markers: List[Tuple[int, str]] = []

    def on_mount(self) -> None:
        # TextArea has its own on_mount; Textual dispatches only the most
        # derived handler, so it must be chained explicitly.
        super().on_mount()
        self._init_graphics()
        theme = self.theme_def
        self.styles.scrollbar_size_vertical = 1
        self.styles.scrollbar_size_horizontal = 1
        self.styles.scrollbar_background = theme.gutter_bg
        self.styles.scrollbar_background_hover = theme.gutter_bg
        self.styles.scrollbar_background_active = theme.gutter_bg
        self.styles.scrollbar_color = theme.unknown_fg
        self.styles.scrollbar_color_hover = theme.text_fg
        self.styles.scrollbar_color_active = theme.text_fg
        self.styles.scrollbar_corner_color = theme.gutter_bg
        self._left_scrollbar_padding()

    # --- left-hand scrollbar ------------------------------------------------
    #
    # Textual hardcodes the vertical scrollbar to the right edge: both
    # Widget.scrollbar_gutter (which reserves the space) and
    # Widget._arrange_scrollbars (which places the widget) assume it. Mirror
    # both, so the leftmost pane's bar sits against the window edge instead of
    # against the linkmap.

    @property
    def scrollbar_gutter(self) -> Spacing:
        """No vertical reserve when the bar is on the left.

        Textual composites scrollbars over `content_region`, which is shaped by
        `styles.gutter` (padding + border) and *not* by this. So a left-hand bar
        cannot be made room for here -- it would simply paint over the first
        column of text. `_left_scrollbar_padding()` shifts the content instead,
        and this stops the space being reserved twice.
        """
        if not self.scrollbar_on_left:
            return Widget.scrollbar_gutter.fget(self)
        return Spacing(0, 0, self.scrollbar_size_horizontal, 0)

    def _left_scrollbar_padding(self) -> None:
        """Reserve the bar's column as padding, unconditionally.

        `Widget.scrollbar_size_vertical` is 0 until the scrollbar is actually
        shown, so sizing the padding from it would make the text jump sideways
        the moment a file grows past one screen. Reserve it always, like CSS's
        `scrollbar-gutter: stable`.
        """
        pad = self.styles.scrollbar_size_vertical if self.scrollbar_on_left else 0
        self.styles.padding = (0, 0, 0, pad)

    def _arrange_scrollbars(self, region: Region):
        if not self.scrollbar_on_left:
            yield from super()._arrange_scrollbars(region)
            return

        # `region` here is the *container* region: the compositor has already
        # shrunk it by styles.gutter, so the padding column reserved by
        # `_left_scrollbar_padding()` sits just outside it, at region.x - size.
        show_vertical, show_horizontal = self.scrollbars_enabled
        size_v = self.styles.scrollbar_size_vertical
        size_h = self.scrollbar_size_horizontal if show_horizontal else 0

        window, h_region = (
            region.split_horizontal(-size_h) if size_h else (region, None)
        )
        if show_vertical and size_v:
            bar = Region(region.x - size_v, region.y, size_v, window.height)
            scrollbar = self.vertical_scrollbar
            scrollbar.window_virtual_size = self.virtual_size.height
            scrollbar.window_size = window.height
            yield scrollbar, bar
        if h_region:
            scrollbar = self.horizontal_scrollbar
            scrollbar.window_virtual_size = self.virtual_size.width
            scrollbar.window_size = window.width
            yield scrollbar, h_region

    def set_insert_markers(self, markers: Sequence[Tuple[int, str]]) -> None:
        if list(markers) != self._insert_markers:
            self._insert_markers = list(markers)
        self.refresh_overlay()

    def _render_overlays(self) -> List[OverlayImage]:
        """One thin image per visible insert marker.

        kitty only. Sixel pixels *become* cell content, so a marker drawn over
        a text row would erase its glyphs -- the gutter and chunkmap get away
        with sixel because the cells beneath them are blank. kitty images float
        above the cells and carry alpha, so they composite.

        One thin opaque image per marker rather than a single whole-pane
        translucent one: measured 0.12ms for one marker and 1.5ms for forty,
        against 7ms to build and encode a 640x640 pane image on every scroll.
        """
        if self.graphics != "kitty" or not self._insert_markers:
            return []
        region = self.content_region
        if not region.width or not region.height:
            return []
        cell_w, cell_h = self.cell_px
        gutter = self.gutter_width
        width_px = max(1, (region.width - gutter) * cell_w)
        scroll_y = int(self.scroll_offset.y)

        images: List[OverlayImage] = []
        for line, tag in self._insert_markers:
            row = line - scroll_y
            if not 0 <= row < region.height:
                continue
            chunk_style = self.theme_def.chunk.get(tag)
            if chunk_style is None:
                continue
            images.append((
                render_insert_marker(width_px, _hex_rgb(chunk_style.line)),
                width_px, INSERT_MARKER_PX,
                region.y + row, region.x + gutter,
            ))
        return images

    def _set_theme(self, theme: str) -> None:
        # apply_css() runs per-render and backfills every *non-configured*
        # theme attribute from CSS. Our deliberately-None cursor_line_style
        # (chunk backgrounds must survive the cursor; the gutter highlight
        # carries the current-line signal instead) would be backfilled with
        # `.text-area--cursor-line { background: $boost }`. Marking it
        # configured at construction doesn't survive either: _set_theme
        # copies via dataclasses.replace(), whose __post_init__ rebuilds
        # the configured set from non-None fields. Enforce on the live
        # copy every time it's installed. (Private API; textual pinned <9.)
        super()._set_theme(theme)
        if self._theme is not None and self._theme.name.startswith("tmeld-"):
            self._theme.cursor_line_style = None
            self._theme._theme_configured_attributes.add("cursor_line_style")

    def set_chunk_styling(
        self,
        line_tags: Dict[int, str],
        inline_ranges: Dict[int, List[Tuple[int, int]]],
    ) -> None:
        self._line_tags = dict(line_tags)
        self._inline_styles = inline_ranges
        self._rebuild_line_styles()

    def set_emphasis(self, emphasized_lines) -> None:
        """Mark the current chunk's lines (Meld current-chunk-highlight)."""
        emphasized = set(emphasized_lines)
        if emphasized == self._emphasized:
            return
        self._emphasized = emphasized
        self._rebuild_line_styles()

    def _rebuild_line_styles(self) -> None:
        theme = self.theme_def
        # Row fill uses the pale scheme "background" — the saturated
        # "line-background" is only for chunk boundary lines in GTK Meld
        self._line_styles = {
            line: Style(
                bgcolor=theme.chunk_fill(tag, emphasized=line in self._emphasized)
            )
            for line, tag in self._line_tags.items()
            if tag in theme.chunk
        }
        # TextArea caches rendered strips keyed on text/selection state
        # only — our chunk styling isn't in the key, so stale highlights
        # survive a re-diff unless we drop the cache (textual pinned <9)
        self._line_cache.clear()
        self.refresh()

    def get_line(self, line_index: int):
        text = super().get_line(line_index)
        line_style = self._line_styles.get(line_index)
        if line_style is not None:
            # Pad so the chunk background paints the whole row, matching
            # Meld's line-background semantics
            text.set_length(max(len(text), self.size.width))
            text.stylize(line_style)
        for start, end in self._inline_styles.get(line_index, ()):
            text.stylize(self._inline_style, start, end)
        return text

    def sync_scroll_to(self, y: int) -> None:
        """Programmatic scroll from the sync algorithm.

        The resulting watch_scroll_y is swallowed instead of posting a
        Scrolled message: Scrolled handlers run asynchronously, so a
        simple in-progress flag cannot guard against the echo, and the
        panes end up re-syncing each other in an endless jitter loop
        whenever the chunk interpolation isn't perfectly symmetric.
        """
        self._sync_target = y
        self.scroll_to(y=y, animate=False)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        expected, self._sync_target = self._sync_target, None
        if expected is not None and int(new_value) == expected:
            return  # echo of a sync scroll — don't sync back
        self.post_message(self.Scrolled(self, new_value))

    def action_copy(self) -> None:
        # Copy is silent by default; over SSH it lands on the *local*
        # clipboard via OSC 52, which users can't see happen — notify.
        text = self.selected_text
        super().action_copy()
        if text:
            lines = text.count("\n") + 1
            what = f"{lines} lines" if lines > 1 else f"{len(text)} chars"
            self.notify(f"Copied {what}", timeout=1.5)

    def action_cut(self) -> None:
        text = self.selected_text
        super().action_cut()
        if text:
            lines = text.count("\n") + 1
            what = f"{lines} lines" if lines > 1 else f"{len(text)} chars"
            self.notify(f"Cut {what}", timeout=1.5)

    def on_click(self, event) -> None:
        # Border/title row acts as the Save button while dirty
        if event.y == 0:
            save_pane = getattr(self.app, "save_pane", None)
            dirty = getattr(self.app, "dirty", None)
            if save_pane and dirty and dirty[self.pane_index]:
                save_pane(self.pane_index)
