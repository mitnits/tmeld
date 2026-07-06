"""Spike: can a TextArea subclass render Meld-style chunk backgrounds?

Success criteria:
  1. whole-row line background per line (chunk line-background)
  2. character-range inline highlight within a line (meld:inline)
  3. both verified to actually hit the screen, via SVG screenshot export

Uses one private attribute (wrapped_document._offset_to_line_info) to map
render y -> document line, same mapping TextArea itself uses.
"""

from rich.style import Style
from textual.app import App, ComposeResult
from textual.strip import Strip
from textual.widgets import TextArea

REPLACE_LINE_BG = "#65b2ff"  # meld:replace line-background
INSERT_LINE_BG = "#a5ff4c"   # meld:insert line-background
INLINE_BG = "#8ac2ff"        # meld:inline background


class SpikePane(TextArea):
    """TextArea with per-line backgrounds and inline range highlights."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.line_bg: dict[int, Style] = {}
        self.inline_ranges: dict[int, list[tuple[int, int]]] = {}

    def get_line(self, line_index: int):
        text = super().get_line(line_index)
        line_style = self.line_bg.get(line_index)
        if line_style is not None:
            # Pad to full widget width so the chunk background paints the
            # whole row (Meld line-background semantics), then style under
            # any inline highlights added below.
            text.set_length(max(len(text), self.size.width))
            text.stylize(line_style)
        for start, end in self.inline_ranges.get(line_index, ()):
            text.stylize(Style(bgcolor=INLINE_BG), start, end)
        return text


class SpikeApp(App):
    def compose(self) -> ComposeResult:
        pane = SpikePane(
            "\n".join(f"line number {i}" for i in range(30)),
            read_only=True,
            show_line_numbers=True,
        )
        pane.line_bg = {
            2: Style(bgcolor=REPLACE_LINE_BG),
            5: Style(bgcolor=INSERT_LINE_BG),
        }
        pane.inline_ranges = {2: [(5, 11)]}
        yield pane


async def run_spike() -> dict:
    app = SpikeApp()
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
        # Scroll down past the styled lines and confirm styling follows
        # document lines, not screen rows
        app.query_one(SpikePane).scroll_to(y=10, animate=False)
        await pilot.pause()
        svg_scrolled = app.export_screenshot()
    return {
        "replace_bg_present": REPLACE_LINE_BG.lstrip("#").upper() in svg.upper(),
        "insert_bg_present": INSERT_LINE_BG.lstrip("#").upper() in svg.upper(),
        "inline_bg_present": INLINE_BG.lstrip("#").upper() in svg.upper(),
        "styles_scroll_away": (
            REPLACE_LINE_BG.lstrip("#").upper() not in svg_scrolled.upper()
        ),
    }


if __name__ == "__main__":
    import asyncio
    import json

    print(json.dumps(asyncio.run(run_spike()), indent=2))
