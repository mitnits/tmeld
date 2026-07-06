"""DiffPane: a TextArea that renders Meld chunk styling.

Rendering hooks validated by spikes/spike_textarea.py: get_line() is the
seam for both whole-row chunk backgrounds (padded to widget width, styled
under) and inline highlight ranges (styled over). One private attribute
(wrapped_document._offset_to_line_info) is intentionally NOT needed here —
line indexes arrive directly in get_line. Textual is pinned to 8.x.
"""

from typing import Dict, List, Tuple

from rich.style import Style
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea
from textual.widgets.text_area import TextAreaTheme

from tmeld.palette import Theme


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


class DiffPane(TextArea):
    """One comparison pane. Editable since Phase 3."""

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
        kwargs.setdefault("show_line_numbers", True)
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

    def on_click(self, event) -> None:
        # Border/title row acts as the Save button while dirty
        if event.y == 0:
            save_pane = getattr(self.app, "save_pane", None)
            dirty = getattr(self.app, "dirty", None)
            if save_pane and dirty and dirty[self.pane_index]:
                save_pane(self.pane_index)
