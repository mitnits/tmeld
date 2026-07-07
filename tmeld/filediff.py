"""FileDiffView: one two- or three-pane editable file comparison.

The widget owns everything a single comparison needs — DiffPanes, the
ActionGutters between them, the ChunkMap, the Comparison model, dirty
state — so the app shell can hold several in tabs (Meld's notebook).
Named after upstream meld/filediff.py, which plays the same role.

Chunk action semantics ported from upstream meld/filediff.py:2611-2674
(replace_chunk/delete_chunk, including the EOF newline handling); action
pane resolution from filediff.get_action_panes/action_push_change_*
(2-way push ignores focus; 3-way acts focused pane -> neighbor).

3-way: middle pane is the merged file (git mergetool convention:
`tmeld $LOCAL $MERGED $REMOTE`; output= redirects middle-pane saves,
mirroring meld -o). The shell's exit code is 0 only if every 3-way
view's middle pane was saved.
"""

from typing import Dict, List, Optional, Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import TextArea

from tmeld.chunkmap import ChunkMap
from tmeld.comparison import Comparison
from tmeld.gutter import ActionGutter
from tmeld.misc import shorten_names
from tmeld.palette import Theme
from tmeld.pane import DiffPane
from tmeld.scroll import calc_syncpoint, interpolate_line, scroll_offset_for_line

REDIFF_DEBOUNCE = 0.25  # seconds after last keystroke

# Direction offsets for chunk actions (upstream filediff.py:102)
PANE_LEFT, PANE_RIGHT = -1, +1

# 3-way scroll influence flows through the middle pane (PARITY.md §4):
# masters left/middle/right influence these panes, in order. When the
# middle pane is influenced first, it becomes the master for the rest.
SCROLL_INFLUENCE: Dict[int, tuple] = {
    2: ((1,), (0,)),
    3: ((1, 2), (0, 2), (1, 0)),
}


class FileDiffView(Horizontal):
    DEFAULT_CSS = """
    FileDiffView DiffPane {
        width: 1fr;
        border: none;
        border-top: round $border;
    }
    FileDiffView DiffPane:focus {
        border-top: round $accent;
    }
    """

    class StatusChanged(Message):
        """Diff stats or dirty flags changed; the shell refreshes the
        window subtitle and this view's tab label."""

        def __init__(self, view: "FileDiffView") -> None:
            self.view = view
            super().__init__()

    def __init__(
        self,
        paths: Sequence[str],
        theme_def: Theme,
        output: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        # Comparison reads the files here, so a bad path fails before
        # the shell mounts the view (clean CLI error, no TUI teardown)
        self.comparison = Comparison(paths, output=output)
        self.panes: List[DiffPane] = []
        self.gutters: List[ActionGutter] = []
        self.current_chunk = None  # merge-cache index of chunk at cursor
        self.dirty = [False] * self.num_panes
        self.merged_saved = False  # 3-way: middle pane saved at least once
        self.status_text = ""
        self._rediff_timer = None

    @property
    def num_panes(self) -> int:
        return self.comparison.num_panes

    @property
    def tab_label(self) -> str:
        """Meld's notebook label (filediff.recompute_label): shortened
        names joined with an em dash, '*' marking modified panes."""
        shortnames = shorten_names(*self.comparison.save_paths)
        for i, dirty in enumerate(self.dirty):
            if dirty:
                shortnames[i] += "*"
        return " — ".join(shortnames)

    def compose(self) -> ComposeResult:
        self.panes = [
            DiffPane(
                pane_index=i,
                theme_def=self.theme_def,
                id=f"pane{i}",
                read_only=False,
            )
            for i in range(self.num_panes)
        ]
        self.gutters = [
            ActionGutter(self.theme_def, id=f"gutter{k}")
            for k in range(self.num_panes - 1)
        ]
        for i, pane in enumerate(self.panes):
            if i:
                yield self.gutters[i - 1]
            yield pane
        yield ChunkMap(self.theme_def, id="chunkmap")

    def on_mount(self) -> None:
        for k, gutter in enumerate(self.gutters):
            gutter.panes = [self.panes[k], self.panes[k + 1]]
            gutter.pane_pair = (k, k + 1)
            gutter.on_push = self._push_chunk
        chunkmap = self.query_one(ChunkMap)
        chunkmap.on_jump = self._jump_to_line
        chunkmap.pane = self.panes[1]
        for i, pane in enumerate(self.panes):
            pane.load_text("\n".join(self.comparison.lines[i]))
            self.dirty[i] = False
        self._refresh_styling()

    # --- Styling refresh --------------------------------------------------

    def _pane_title(self, i: int) -> str:
        path = self.comparison.save_paths[i].replace("[", r"\[")
        if self.dirty[i]:
            return f"{path} [b]*[/b] [reverse b] Save [/reverse b]"
        return path

    def _refresh_styling(self) -> None:
        comparison = self.comparison
        inline = comparison.inline_ranges()
        for i, pane in enumerate(self.panes):
            pane.border_title = self._pane_title(i)
            pane.set_chunk_styling(comparison.line_tags(i), inline[i])
        self._apply_emphasis()
        for k, gutter in enumerate(self.gutters):
            gutter.set_starts(comparison.action_starts(k))
        mid_chunks = comparison.pane_chunks(1)
        self.query_one(ChunkMap).set_chunks(
            [(c.tag, c.start_a, c.end_a) for c in mid_chunks],
            len(comparison.lines[1]),
        )
        count = comparison.differ.diff_count()
        conflicts = len(comparison.differ.conflicts)
        subtitle = "identical" if count == 0 else f"{count} changes"
        if conflicts:
            subtitle += f", {conflicts} conflicts"
        self.status_text = subtitle
        self.post_message(self.StatusChanged(self))

    # --- Current chunk (Meld: the chunk containing the cursor) ------------

    def _apply_emphasis(self) -> None:
        index = self.current_chunk
        differ = self.comparison.differ
        in_range = index is not None and 0 <= index < differ.diff_count()
        for i, pane in enumerate(self.panes):
            # Merge-cache indices don't map 1:1 onto per-pane chunk lists
            # in 3-way; get_chunk orients (or drops) the chunk per pane
            chunk = differ.get_chunk(index, i) if in_range else None
            if chunk is not None:
                pane.set_emphasis(range(chunk.start_a, chunk.end_a))
            else:
                pane.set_emphasis(())

    def on_text_area_selection_changed(
        self, event: TextArea.SelectionChanged
    ) -> None:
        pane = event.text_area
        if not isinstance(pane, DiffPane):
            return
        row = event.selection.end[0]
        index, _prev, _next = self.comparison.differ.locate_chunk(
            pane.pane_index, row
        )
        if index != self.current_chunk:
            self.current_chunk = index
            self._apply_emphasis()

    # --- Editing / re-diff ------------------------------------------------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        pane = event.text_area
        if not isinstance(pane, DiffPane):
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
            self.panes[i].border_title = self._pane_title(i)
            self.post_message(self.StatusChanged(self))

    def _rediff(self) -> None:
        if self._rediff_timer is not None:
            self._rediff_timer.stop()
            self._rediff_timer = None
        comparison = self.comparison
        for i, pane in enumerate(self.panes):
            comparison.lines[i] = pane.text.split("\n")
        comparison.recompute()
        self._refresh_styling()

    def save_pane(self, i: int) -> None:
        self._rediff()
        self.comparison.save(i)
        self.dirty[i] = False
        if i == 1 and self.num_panes == 3:
            self.merged_saved = True
        self.panes[i].border_title = self._pane_title(i)
        self.post_message(self.StatusChanged(self))
        self.notify(f"Saved {self.comparison.save_paths[i]}", timeout=2)

    def action_save(self) -> None:
        self.save_pane(self._focused_pane().pane_index)

    # --- Chunk actions (ported from filediff.py replace/delete_chunk) -----

    def _focused_pane(self) -> DiffPane:
        focused = self.app.focused
        if isinstance(focused, DiffPane) and focused in self.panes:
            return focused
        return self.panes[0]

    def _chunk_at_cursor(self) -> Optional[int]:
        pane = self._focused_pane()
        row = pane.cursor_location[0]
        index, _prev, _next = self.comparison.differ.locate_chunk(
            pane.pane_index, row
        )
        return index

    def _action_panes(self, direction: int, reverse: bool = False):
        """(src, dst) for a chunk action from the focused pane; the caller
        validates the range (upstream disables the action instead)."""
        src = self._focused_pane().pane_index
        dst = src + direction
        return (dst, src) if reverse else (src, dst)

    def _valid_pair(self, src: int, dst: int) -> bool:
        return 0 <= src < self.num_panes and 0 <= dst < self.num_panes

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
            self.app.bell()
            return
        src_lines = self.comparison.lines[src][chunk.start_a:chunk.end_a]
        dst_pane = self.panes[dst]
        self._replace_lines(dst_pane, chunk.start_b, chunk.end_b, src_lines)
        dst_pane.move_cursor((chunk.start_b, 0))
        self._mark_dirty(dst)
        self._rediff()

    def _replace_action(self, src: int, dst: int) -> None:
        if not self._valid_pair(src, dst):
            self.app.bell()
            return
        index = self._chunk_at_cursor()
        if index is None:
            self.app.bell()
            return
        self._push_chunk(src, dst, index)

    def _push_action(self, direction: int) -> None:
        if self.num_panes == 2:
            # Meld 2-way pushes ignore focus (action_push_change_left/right)
            src, dst = (0, 1) if direction == PANE_RIGHT else (1, 0)
        else:
            src, dst = self._action_panes(direction)
        self._replace_action(src, dst)

    def action_push_right(self) -> None:
        self._push_action(PANE_RIGHT)

    def action_push_left(self) -> None:
        self._push_action(PANE_LEFT)

    def action_pull_left(self) -> None:
        """Pull the current chunk from the left neighbor (Alt+Shift+Right)."""
        self._replace_action(*self._action_panes(PANE_LEFT, reverse=True))

    def action_pull_right(self) -> None:
        """Pull the current chunk from the right neighbor (Alt+Shift+Left)."""
        self._replace_action(*self._action_panes(PANE_RIGHT, reverse=True))

    def _copy_chunk(self, src: int, dst: int, index: int, up: bool) -> None:
        """Port of filediff.copy_chunk: insert without deleting."""
        chunk = self.comparison.differ.get_chunk(index, src, dst)
        if chunk is None:
            self.app.bell()
            return
        src_lines = self.comparison.lines[src][chunk.start_a:chunk.end_a]
        if not src_lines:
            self.app.bell()
            return
        at = chunk.start_b if up else chunk.end_b
        self._replace_lines(self.panes[dst], at, at, src_lines)
        self._mark_dirty(dst)
        self._rediff()

    def _copy_action(self, direction: int, up: bool) -> None:
        src, dst = self._action_panes(direction)
        if not self._valid_pair(src, dst):
            self.app.bell()
            return
        index = self._chunk_at_cursor()
        if index is None:
            self.app.bell()
            return
        self._copy_chunk(src, dst, index, up)

    def action_copy_up_left(self) -> None:
        self._copy_action(PANE_LEFT, up=True)

    def action_copy_up_right(self) -> None:
        self._copy_action(PANE_RIGHT, up=True)

    def action_copy_down_left(self) -> None:
        self._copy_action(PANE_LEFT, up=False)

    def action_copy_down_right(self) -> None:
        self._copy_action(PANE_RIGHT, up=False)

    def action_delete_chunk(self) -> None:
        pane = self._focused_pane()
        index = self._chunk_at_cursor()
        if index is None:
            self.app.bell()
            return
        chunk = self.comparison.differ.get_chunk(index, pane.pane_index)
        if chunk is None or chunk.start_a == chunk.end_a:
            self.app.bell()
            return
        self._replace_lines(pane, chunk.start_a, chunk.end_a, [])
        self._mark_dirty(pane.pane_index)
        self._rediff()

    def action_merge_all(self) -> None:
        """3-way: merge every non-conflicting chunk into the middle pane."""
        if self.num_panes != 3:
            self.app.bell()
            return
        merged = self.comparison.merge_all_non_conflicting()
        if merged.split("\n") == self.comparison.lines[1]:
            self.app.bell()  # nothing mergeable
            return
        pane = self.panes[1]
        # replace (not load_text) so the merge stays undoable
        pane.replace(merged, (0, 0), pane.document.end)
        self._mark_dirty(1)
        self._rediff()

    # --- Synchronized scrolling (PARITY.md §4) ----------------------------

    def on_diff_pane_scrolled(self, message: DiffPane.Scrolled) -> None:
        for gutter in self.gutters:
            gutter.refresh()
        self.query_one(ChunkMap).refresh()
        master = message.pane.pane_index
        master_pane = self.panes[master]
        page = master_pane.scrollable_content_region.height or 1
        totals = [len(lines) for lines in self.comparison.lines]
        syncpoint = calc_syncpoint(message.value, page, totals[master])
        target_line = message.value + page * syncpoint
        # 3-way influence cascades through the middle pane: once pane 1
        # is synced it becomes the master (upstream _sync_vscroll)
        for i in SCROLL_INFLUENCE[self.num_panes][master]:
            other_pane = self.panes[i]
            other_page = other_pane.scrollable_content_region.height or 1
            other_line = interpolate_line(
                target_line,
                totals[master],
                totals[i],
                self.comparison.pair_chunks(master, i),
            )
            # Echo suppression lives in sync_scroll_to (the Scrolled
            # message arrives async, so a flag around this call can't
            # guard the loop)
            other_pane.sync_scroll_to(
                scroll_offset_for_line(other_line, other_page, totals[i], syncpoint)
            )
            if i == 1:
                master, target_line = 1, other_line

    # --- Navigation ---------------------------------------------------------

    def _jump_to_line(self, line: int) -> None:
        pane = self.panes[1]
        page = pane.scrollable_content_region.height or 1
        pane.scroll_to(y=max(0, line - page // 2), animate=False)

    def _go_to_chunk(self, index: int) -> None:
        comparison = self.comparison
        if not (0 <= index < comparison.differ.diff_count()):
            self.app.bell()
            return
        pane = self._focused_pane()
        chunk = comparison.differ.get_chunk(index, pane.pane_index)
        if chunk is None:
            # Chunk doesn't touch the focused pane (3-way): follow it in
            # the middle pane, which is part of every chunk
            pane = self.panes[1]
            chunk = comparison.differ.get_chunk(index, 1)
            if chunk is None:
                self.app.bell()
                return
        page = pane.scrollable_content_region.height or 1
        target = max(0, chunk.start_a - page // 2)
        pane.scroll_to(y=target, animate=False)
        pane.move_cursor((chunk.start_a, 0))

    def _nav_chunk(self, which: int) -> None:
        """Meld semantics: next/prev relative to the cursor's line.

        locate_chunk gives (current, previous, next); which is 1 or 2.
        """
        pane = self._focused_pane()
        row = pane.cursor_location[0]
        located = self.comparison.differ.locate_chunk(pane.pane_index, row)
        target = located[which]
        if target is None:
            self.app.bell()
        else:
            self._go_to_chunk(target)

    def action_next_chunk(self) -> None:
        self._nav_chunk(2)

    def action_previous_chunk(self) -> None:
        self._nav_chunk(1)

    def _nav_conflict(self, forward: bool) -> None:
        """Next/prev conflict relative to the cursor (Meld Ctrl+K/Ctrl+J).

        Conflict chunks always involve the middle pane, so they exist in
        every pane's line cache; upstream picks the first conflict >= the
        next chunk / last conflict <= the previous chunk (filediff.py:683).
        """
        pane = self._focused_pane()
        row = pane.cursor_location[0]
        _cur, prev, next_ = self.comparison.differ.locate_chunk(
            pane.pane_index, row
        )
        conflicts = self.comparison.differ.conflicts
        target = None
        if forward and next_ is not None:
            target = next((c for c in conflicts if c >= next_), None)
        elif not forward and prev is not None:
            target = next((c for c in reversed(conflicts) if c <= prev), None)
        if target is None:
            self.app.bell()
        else:
            self._go_to_chunk(target)

    def action_next_conflict(self) -> None:
        self._nav_conflict(forward=True)

    def action_previous_conflict(self) -> None:
        self._nav_conflict(forward=False)

    def action_next_pane(self) -> None:
        current = self._focused_pane().pane_index
        self.panes[(current + 1) % self.num_panes].focus()

    def action_previous_pane(self) -> None:
        current = self._focused_pane().pane_index
        self.panes[(current - 1) % self.num_panes].focus()

    def merge_resolved(self) -> bool:
        """Mergetool contract: a 3-way view succeeds only if saved."""
        return self.num_panes != 3 or self.merged_saved
