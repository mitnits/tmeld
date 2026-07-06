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
    # apply_css() backfills every *non-configured* attribute from CSS on
    # each render, and __post_init__ only marks non-None fields as
    # configured — so our deliberate None above would be replaced by the
    # `.text-area--cursor-line { background: $boost }` component style,
    # wiping chunk backgrounds under the cursor. Mark it configured so
    # None sticks. (Private API; textual pinned <9.)
    theme._theme_configured_attributes.add("cursor_line_style")
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
        self._inline_styles: Dict[int, List[Tuple[int, int]]] = {}
        self._inline_style = Style(bgcolor=theme_def.inline_bg)
        ta_theme = build_textarea_theme(theme_def)
        self.register_theme(ta_theme)
        self.theme = ta_theme.name

    def set_chunk_styling(
        self,
        line_tags: Dict[int, str],
        inline_ranges: Dict[int, List[Tuple[int, int]]],
    ) -> None:
        chunk = self.theme_def.chunk
        # Row fill uses the pale scheme "background" — the saturated
        # "line-background" is only for chunk boundary lines in GTK Meld
        self._line_styles = {
            line: Style(bgcolor=chunk[tag].fill)
            for line, tag in line_tags.items()
            if tag in chunk
        }
        self._inline_styles = inline_ranges
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

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self.post_message(self.Scrolled(self, new_value))
