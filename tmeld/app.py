"""tmeld application shell: two-pane file comparison (Phase 2, read-only).

Keybindings follow Meld exactly (PARITY.md §3): Alt+Down/Up or Ctrl+D/E
for chunk navigation. Alt+arrow availability depends on the terminal
sending ESC-prefixed sequences ("Option as Esc+" in iTerm2); the Ctrl
variants are Meld's own alternates and work everywhere.
"""

import argparse
import sys
from typing import List, Optional, Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer

from tmeld import __version__
from tmeld.chunkmap import ChunkMap
from tmeld.comparison import Comparison
from tmeld.palette import DEFAULT_THEME, THEMES
from tmeld.pane import DiffPane
from tmeld.scroll import sync_scroll_target


class TmeldApp(App):
    TITLE = "tmeld"

    CSS = """
    #panes {
        layout: horizontal;
    }
    DiffPane {
        width: 1fr;
        border: round $border;
    }
    DiffPane:focus {
        border: round $accent;
    }
    """

    BINDINGS = [
        Binding("alt+down,ctrl+d", "next_chunk", "Next change", priority=True),
        Binding("alt+up,ctrl+e", "previous_chunk", "Previous change", priority=True),
        Binding("alt+pagedown", "next_pane", "Next pane"),
        Binding("alt+pageup", "previous_pane", "Prev pane"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, paths: Sequence[str], theme_name: str = DEFAULT_THEME):
        super().__init__()
        self.paths = list(paths)
        self.theme_def = THEMES[theme_name]
        self.comparison: Optional[Comparison] = None
        self.panes: List[DiffPane] = []
        self.current_chunk = -1
        self._syncing_scroll = False

    def compose(self) -> ComposeResult:
        self.panes = [
            DiffPane(pane_index=i, theme_def=self.theme_def, id=f"pane{i}")
            for i in range(2)
        ]
        with Horizontal(id="panes"):
            yield self.panes[0]
            yield self.panes[1]
            yield ChunkMap(self.theme_def, id="chunkmap")
        yield Footer()

    def on_mount(self) -> None:
        comparison = Comparison(self.paths)
        self.comparison = comparison
        inline = comparison.inline_ranges()
        for i, pane in enumerate(self.panes):
            pane.border_title = self.paths[i]
            pane.load_text("\n".join(comparison.lines[i]))
            pane.set_chunk_styling(comparison.line_tags(i), inline[i])
        chunks = [
            (c.tag, c.start_a, c.end_a) for c in comparison.pane_chunks(1)
        ]
        self.query_one(ChunkMap).set_chunks(chunks, len(comparison.lines[1]))
        self.sub_title = f"{comparison.differ.diff_count()} changes"
        self.panes[0].focus()

    # --- Synchronized scrolling (PARITY.md §4) ---------------------------

    def on_diff_pane_scrolled(self, message: DiffPane.Scrolled) -> None:
        if self._syncing_scroll or self.comparison is None:
            return
        master = message.pane.pane_index
        other = 1 - master
        master_pane, other_pane = self.panes[master], self.panes[other]
        page = master_pane.scrollable_content_region.height or 1
        target = sync_scroll_target(
            message.value,
            page,
            len(self.comparison.lines[master]),
            other_pane.scrollable_content_region.height or 1,
            len(self.comparison.lines[other]),
            self.comparison.pair_chunks(master, other),
        )
        self._syncing_scroll = True
        try:
            other_pane.scroll_to(y=target, animate=False)
        finally:
            self._syncing_scroll = False

    # --- Chunk navigation -------------------------------------------------

    def _go_to_chunk(self, index: int) -> None:
        comparison = self.comparison
        if comparison is None or not (0 <= index < comparison.differ.diff_count()):
            self.bell()
            return
        self.current_chunk = index
        focused = self.focused
        pane = focused if isinstance(focused, DiffPane) else self.panes[0]
        chunk = comparison.pane_chunks(pane.pane_index)[index]
        page = pane.scrollable_content_region.height or 1
        target = max(0, chunk.start_a - page // 2)
        pane.scroll_to(y=target, animate=False)
        pane.move_cursor((chunk.start_a, 0))

    def action_next_chunk(self) -> None:
        self._go_to_chunk(self.current_chunk + 1)

    def action_previous_chunk(self) -> None:
        self._go_to_chunk(max(self.current_chunk - 1, 0))

    def action_next_pane(self) -> None:
        self.panes[1 if self.focused is self.panes[0] else 0].focus()

    action_previous_pane = action_next_pane


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tmeld", description="Meld, in your terminal"
    )
    parser.add_argument("files", nargs=2, help="two files to compare")
    parser.add_argument(
        "--theme", choices=sorted(THEMES), default=DEFAULT_THEME
    )
    parser.add_argument(
        "--version", action="version", version=f"tmeld {__version__}"
    )
    args = parser.parse_args(argv)

    app = TmeldApp(args.files, theme_name=args.theme)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
