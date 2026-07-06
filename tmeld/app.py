"""tmeld application shell: two-pane editable file comparison (Phase 3).

Keybindings follow Meld exactly (PARITY.md §3). Alt+arrow availability
depends on the terminal sending ESC-prefixed sequences ("Option as Esc+"
in iTerm2); Ctrl+D/E are Meld's own alternates for chunk navigation.

Chunk action semantics ported from upstream meld/filediff.py:2611-2674
(replace_chunk/delete_chunk, including the EOF newline handling).
"""

import argparse
import sys
from typing import List, Optional, Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, TextArea

from tmeld import __version__
from tmeld.chunkmap import ChunkMap
from tmeld.comparison import Comparison
from tmeld.gutter import ActionGutter
from tmeld.palette import DEFAULT_THEME, THEMES
from tmeld.pane import DiffPane
from tmeld.scroll import sync_scroll_target

REDIFF_DEBOUNCE = 0.25  # seconds after last keystroke


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
        Binding("alt+up,ctrl+e", "previous_chunk", "Prev change", priority=True),
        Binding("alt+right", "push_right", "Push right", priority=True),
        Binding("alt+left", "push_left", "Push left", priority=True),
        Binding("alt+shift+right", "pull_left", "Pull from right", priority=True),
        Binding("alt+shift+left", "pull_right", "Pull from left", priority=True),
        Binding("alt+delete", "delete_chunk", "Delete", priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("alt+pagedown", "next_pane", "Next pane", show=False),
        Binding("alt+pageup", "previous_pane", "Prev pane", show=False),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, paths: Sequence[str], theme_name: str = DEFAULT_THEME):
        super().__init__()
        self.paths = list(paths)
        self.theme_def = THEMES[theme_name]
        self.comparison: Optional[Comparison] = None
        self.panes: List[DiffPane] = []
        self.current_chunk = -1
        self.dirty = [False, False]
        self._syncing_scroll = False
        self._rediff_timer = None

    def compose(self) -> ComposeResult:
        self.panes = [
            DiffPane(
                pane_index=i,
                theme_def=self.theme_def,
                id=f"pane{i}",
                read_only=False,
            )
            for i in range(2)
        ]
        with Horizontal(id="panes"):
            yield self.panes[0]
            yield ActionGutter(self.theme_def, id="gutter")
            yield self.panes[1]
            yield ChunkMap(self.theme_def, id="chunkmap")
        yield Footer()

    def on_mount(self) -> None:
        self.comparison = Comparison(self.paths)
        gutter = self.query_one(ActionGutter)
        gutter.panes = self.panes
        gutter.on_push = self._push_chunk
        chunkmap = self.query_one(ChunkMap)
        chunkmap.on_jump = self._jump_to_line
        for i, pane in enumerate(self.panes):
            pane.load_text("\n".join(self.comparison.lines[i]))
            self.dirty[i] = False
        self._refresh_styling()
        self.panes[0].focus()

    # --- Styling refresh --------------------------------------------------

    def _refresh_styling(self) -> None:
        comparison = self.comparison
        inline = comparison.inline_ranges()
        pane_chunks = [comparison.pane_chunks(i) for i in range(2)]
        for i, pane in enumerate(self.panes):
            pane.border_title = self.paths[i] + (" *" if self.dirty[i] else "")
            pane.set_chunk_styling(comparison.line_tags(i), inline[i])
        self.query_one(ActionGutter).set_chunks(pane_chunks)
        self.query_one(ChunkMap).set_chunks(
            [(c.tag, c.start_a, c.end_a) for c in pane_chunks[1]],
            len(comparison.lines[1]),
        )
        count = comparison.differ.diff_count()
        self.sub_title = "identical" if count == 0 else f"{count} changes"

    # --- Editing / re-diff ------------------------------------------------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        pane = event.text_area
        if not isinstance(pane, DiffPane) or self.comparison is None:
            return
        i = pane.pane_index
        # Programmatic loads/pushes keep comparison.lines in sync before
        # this message arrives; only genuine divergence marks dirty and
        # schedules a re-diff (chunk actions mark dirty explicitly).
        if pane.text.split("\n") == self.comparison.lines[i]:
            return
        self._mark_dirty(i)
        if self._rediff_timer is not None:
            self._rediff_timer.stop()
        self._rediff_timer = self.set_timer(REDIFF_DEBOUNCE, self._rediff)

    def _mark_dirty(self, i: int) -> None:
        if not self.dirty[i]:
            self.dirty[i] = True
            self.panes[i].border_title = self.paths[i] + " *"

    def _rediff(self) -> None:
        if self._rediff_timer is not None:
            self._rediff_timer.stop()
            self._rediff_timer = None
        comparison = self.comparison
        for i, pane in enumerate(self.panes):
            comparison.lines[i] = pane.text.split("\n")
        comparison.recompute()
        self._refresh_styling()

    def action_save(self) -> None:
        focused = self.focused
        pane = focused if isinstance(focused, DiffPane) else self.panes[0]
        i = pane.pane_index
        self._rediff()
        self.comparison.save(i)
        self.dirty[i] = False
        pane.border_title = self.paths[i]
        self.notify(f"Saved {self.paths[i]}", timeout=2)

    # --- Chunk actions (ported from filediff.py replace/delete_chunk) -----

    def _chunk_at_cursor(self) -> Optional[int]:
        focused = self.focused
        pane = focused if isinstance(focused, DiffPane) else self.panes[0]
        row = pane.cursor_location[0]
        index, _prev, _next = self.comparison.differ.locate_chunk(
            pane.pane_index, row
        )
        return index

    def _replace_lines(
        self, pane: DiffPane, start: int, end: int, new_lines: List[str]
    ) -> None:
        """Replace document lines [start, end) with new_lines."""
        doc = pane.document
        line_count = doc.line_count
        if end < line_count:
            text = "".join(line + "\n" for line in new_lines)
            pane.replace(text, (start, 0), (end, 0))
        else:
            # Chunk reaches EOF: the last line has no trailing newline, so
            # splice text (and, when deleting everything, the preceding
            # newline too) — mirrors get_iter_at_line_or_eof handling
            text = "\n".join(new_lines)
            if start > 0:
                prev_len = len(doc.get_line(start - 1))
                start_loc = (start - 1, prev_len)
                text = ("\n" + text) if new_lines else ""
            else:
                start_loc = (0, 0)
            end_loc = doc.end
            pane.replace(text, start_loc, end_loc)

    def _push_chunk(self, src: int, dst: int, chunk_index: int) -> None:
        chunk = self.comparison.differ.get_chunk(chunk_index, src, dst)
        if chunk is None:
            self.bell()
            return
        src_lines = self.comparison.lines[src][chunk.start_a:chunk.end_a]
        dst_pane = self.panes[dst]
        self._replace_lines(dst_pane, chunk.start_b, chunk.end_b, src_lines)
        dst_pane.move_cursor((chunk.start_b, 0))
        self._mark_dirty(dst)
        self._rediff()

    def _push_from_focused(self, reverse: bool = False) -> None:
        focused = self.focused
        pane = focused if isinstance(focused, DiffPane) else self.panes[0]
        src = pane.pane_index
        dst = 1 - src
        if reverse:
            src, dst = dst, src
        index = self._chunk_at_cursor()
        if index is None:
            self.bell()
            return
        self._push_chunk(src, dst, index)

    def action_push_right(self) -> None:
        # Meld semantics: push acts from the focused pane in the pressed
        # direction; pushing right from the right pane is a no-op bell
        focused = self.focused
        if isinstance(focused, DiffPane) and focused.pane_index == 1:
            self.bell()
            return
        self._push_from_focused()

    def action_push_left(self) -> None:
        focused = self.focused
        if isinstance(focused, DiffPane) and focused.pane_index == 0:
            self.bell()
            return
        self._push_from_focused()

    def action_pull_left(self) -> None:
        """Pull the current chunk from the other pane into the focused one."""
        self._push_from_focused(reverse=True)

    action_pull_right = action_pull_left

    def action_delete_chunk(self) -> None:
        focused = self.focused
        pane = focused if isinstance(focused, DiffPane) else self.panes[0]
        index = self._chunk_at_cursor()
        if index is None:
            self.bell()
            return
        chunk = self.comparison.differ.get_chunk(index, pane.pane_index)
        if chunk is None or chunk.start_a == chunk.end_a:
            self.bell()
            return
        self._replace_lines(pane, chunk.start_a, chunk.end_a, [])
        self._mark_dirty(pane.pane_index)
        self._rediff()

    # --- Synchronized scrolling (PARITY.md §4) ----------------------------

    def on_diff_pane_scrolled(self, message: DiffPane.Scrolled) -> None:
        self.query_one(ActionGutter).refresh()
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

    # --- Navigation ---------------------------------------------------------

    def _jump_to_line(self, line: int) -> None:
        pane = self.panes[1]
        page = pane.scrollable_content_region.height or 1
        pane.scroll_to(y=max(0, line - page // 2), animate=False)

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
