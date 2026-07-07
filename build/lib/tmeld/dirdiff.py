"""DirDiffView: side-by-side folder comparison (Meld's folder view).

One shared tree model rendered as N name columns with │ dividers —
rows align 1:1 across panes by construction (CanonicalListing), so a
single cursor/expansion state serves all panes, like Meld's synced
treeviews. Column text is painted in the pane's state style from
PARITY.md §2 (fg/bold/italic/strike, upstream tree.py:95).

Keys follow upstream accelerators (dirdiff.py bindings):
  Return       compare a file row (the shell opens a FileDiffView tab)
               or expand/collapse a folder row
  Alt+Left/Right   copy the row from the focused pane to that neighbor
                   (folder-copy-left/right; copy_selected port)
  Delete       delete the row in the focused pane (press twice to
               confirm — TUI stand-in for Meld's trash + dialog)
  Alt+Down/Up, Ctrl+D/E   next/previous differing row
  Alt+PgDn/PgUp           switch focused pane (column)

Scanning runs in a thread worker; rescans after copy/delete preserve
expansion and cursor by row key (the chain of canonical names).

Not ported (yet): state-based row filtering (F8), "Compare selected"
marking, newest-file emblems, symlink target display.
"""

import os
import shutil
import time
from typing import Callable, List, Optional, Sequence, Set, Tuple

from rich.style import Style
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from tmeld.comparisonview import ComparisonView
from tmeld.dircompare import (
    STATE_EMPTY,
    STATE_ERROR,
    STATE_MISSING,
    STATE_MODIFIED,
    STATE_NEW,
    STATE_NOCHANGE,
    STATE_NONE,
    STATE_NONEXIST,
    STATE_NORMAL,
    STATE_REMOVED,
    STATE_CONFLICT,
    STATE_IGNORED,
    STATE_RENAMED,
    DirComparison,
    DirEntry,
)
from tmeld.misc import copy2, copytree, shorten_names
from tmeld.palette import Theme

# How often scan progress updates flow to the UI thread
SCAN_PROGRESS_INTERVAL = 0.1

RowKey = Tuple[str, ...]


def state_style(theme: Theme, state: int) -> Style:
    """PARITY.md §2: (fg source, italic, bold, strike) per tree state."""
    chunk = theme.chunk
    if state in (STATE_IGNORED, STATE_NONE):
        return Style(color=theme.unknown_fg)
    if state == STATE_NOCHANGE:
        return Style(color=theme.text_fg, italic=True)
    if state == STATE_ERROR:
        return Style(color=chunk["error"].fg, bold=True)
    if state == STATE_EMPTY:
        return Style(color=theme.unknown_fg, italic=True)
    if state == STATE_NEW:
        return Style(color=chunk["insert"].fg, bold=True)
    if state == STATE_MODIFIED:
        return Style(color=chunk["replace"].fg, bold=True)
    if state == STATE_RENAMED:
        return Style(color=chunk["replace"].fg)
    if state == STATE_CONFLICT:
        return Style(color=chunk["conflict"].fg, bold=True)
    if state in (STATE_REMOVED, STATE_MISSING):
        return Style(color=chunk["delete"].fg, bold=True, strike=True)
    if state == STATE_NONEXIST:
        return Style(color=theme.unknown_fg, strike=True)
    return Style(color=theme.text_fg)  # STATE_NORMAL, STATE_SPINNER


def _empty_placeholder(num_panes: int) -> DirEntry:
    return DirEntry(
        tuple("(Empty folder)" for _ in range(num_panes)),
        tuple("" for _ in range(num_panes)),
        tuple(True for _ in range(num_panes)),
        isdir=False,
        state=STATE_EMPTY,
    )


class DirTree(ScrollView):
    """The synced N-column tree renderer with cursor and expansion."""

    can_focus = True

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("home", "cursor_home", "Home", show=False),
        Binding("end", "cursor_end", "End", show=False),
        Binding("enter", "activate", "Compare", show=False),
        Binding("space", "toggle_dir", "Expand/collapse", show=False),
        Binding("delete", "delete_row", "Delete", show=False),
    ]

    class Activated(Message):
        """Return on a file row (upstream folder-compare)."""

        def __init__(self, entry: DirEntry) -> None:
            self.entry = entry
            super().__init__()

    class DeleteRequested(Message):
        def __init__(self, entry: DirEntry, pane: int) -> None:
            self.entry = entry
            self.pane = pane
            super().__init__()

    class CursorMoved(Message):
        pass

    def __init__(self, theme_def: Theme, num_panes: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        self.num_panes = num_panes
        self.root: Optional[DirEntry] = None
        self.expanded: Set[RowKey] = set()
        self.rows: List[Tuple[DirEntry, int, RowKey]] = []
        self.cursor = 0
        self.focus_pane = 0

    # --- Model wiring -------------------------------------------------------

    def set_root(self, root: DirEntry, cursor_key: Optional[RowKey] = None) -> None:
        """Install a (re)scanned tree. Expansion carries over by key;
        difference paths auto-expand (upstream expands rows with
        differences after a scan)."""
        self.root = root
        self.expanded.add(())
        self.expanded |= self._keys_with_differences(root)
        self._rebuild()
        if cursor_key is not None:
            for i, (_e, _d, key) in enumerate(self.rows):
                if key == cursor_key:
                    self.cursor = i
                    break
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))
        self._scroll_cursor_into_view()
        self.refresh()

    def _keys_with_differences(self, root: DirEntry) -> Set[RowKey]:
        result: Set[RowKey] = set()

        def walk(entry: DirEntry, key: RowKey) -> bool:
            has_difference = False
            for child in entry.children:
                child_key = key + (child.names[0],)
                in_subtree = walk(child, child_key) if child.isdir else False
                has_difference |= child.different or in_subtree
            if has_difference:
                result.add(key)
            return has_difference

        walk(root, ())
        return result

    def _rebuild(self) -> None:
        rows: List[Tuple[DirEntry, int, RowKey]] = []

        def add(entry: DirEntry, depth: int, key: RowKey) -> None:
            rows.append((entry, depth, key))
            if entry.isdir and key in self.expanded:
                if entry.children:
                    for child in entry.children:
                        add(child, depth + 1, key + (child.names[0],))
                else:
                    rows.append(
                        (_empty_placeholder(self.num_panes), depth + 1,
                         key + ("",))
                    )

        if self.root is not None:
            add(self.root, 0, ())
        self.rows = rows
        self.virtual_size = Size(self.size.width, len(rows))

    def on_resize(self) -> None:
        self.virtual_size = Size(self.size.width, len(self.rows))

    # --- Cursor -------------------------------------------------------------

    @property
    def cursor_row(self) -> Optional[Tuple[DirEntry, int, RowKey]]:
        if 0 <= self.cursor < len(self.rows):
            return self.rows[self.cursor]
        return None

    def _move_cursor(self, index: int) -> None:
        index = max(0, min(index, len(self.rows) - 1))
        if index != self.cursor:
            self.cursor = index
            self._scroll_cursor_into_view()
            self.refresh()
            self.post_message(self.CursorMoved())

    def _scroll_cursor_into_view(self) -> None:
        page = self.scrollable_content_region.height or 1
        top = int(self.scroll_offset.y)
        if self.cursor < top:
            self.scroll_to(y=self.cursor, animate=False)
        elif self.cursor >= top + page:
            self.scroll_to(y=self.cursor - page + 1, animate=False)

    def action_cursor_up(self) -> None:
        self._move_cursor(self.cursor - 1)

    def action_cursor_down(self) -> None:
        self._move_cursor(self.cursor + 1)

    def action_cursor_home(self) -> None:
        self._move_cursor(0)

    def action_cursor_end(self) -> None:
        self._move_cursor(len(self.rows) - 1)

    def move_to_difference(self, direction: int) -> bool:
        """Next/previous differing visible row from the cursor."""
        indices = (
            range(self.cursor + 1, len(self.rows))
            if direction > 0
            else range(self.cursor - 1, -1, -1)
        )
        for i in indices:
            entry = self.rows[i][0]
            if entry.different and entry.state != STATE_EMPTY:
                self._move_cursor(i)
                return True
        return False

    # --- Expansion / activation ----------------------------------------------

    def toggle_dir(self, row_index: int) -> bool:
        entry, _depth, key = self.rows[row_index]
        if not entry.isdir:
            return False
        if key in self.expanded:
            self.expanded.discard(key)
        else:
            self.expanded.add(key)
        self._rebuild()
        self.refresh()
        return True

    def action_toggle_dir(self) -> None:
        row = self.cursor_row
        if row is None or not self.toggle_dir(self.cursor):
            self.app.bell()

    def action_activate(self) -> None:
        row = self.cursor_row
        if row is None:
            self.app.bell()
            return
        entry = row[0]
        if entry.isdir:
            self.toggle_dir(self.cursor)
        elif entry.state in (STATE_EMPTY, STATE_ERROR):
            self.app.bell()
        else:
            self.post_message(self.Activated(entry))

    def action_delete_row(self) -> None:
        row = self.cursor_row
        if row is None or row[0].state in (STATE_EMPTY, STATE_ERROR):
            self.app.bell()
            return
        self.post_message(self.DeleteRequested(row[0], self.focus_pane))

    def on_click(self, event: events.Click) -> None:
        self.focus()
        row_index = event.y + int(self.scroll_offset.y)
        if not (0 <= row_index < len(self.rows)):
            return
        # Clicking a column moves pane focus there, like Meld treeviews
        divider_width = 1
        col_width = (self.size.width - (self.num_panes - 1) * divider_width
                     ) // self.num_panes
        pane = min(event.x // (col_width + divider_width), self.num_panes - 1)
        self.focus_pane = pane
        self._move_cursor(row_index)
        entry = self.rows[row_index][0]
        if entry.isdir:
            # Single click expands/collapses a folder row (Meld's
            # expander is single-click; user round 6 extends it to the
            # whole line). chain==2 is the tail of a double click whose
            # first click already toggled — swallow it, don't re-toggle.
            if event.chain == 1:
                self.toggle_dir(row_index)
            return
        if event.chain == 2:
            self.action_activate()
        else:
            self.refresh()

    # --- Rendering ------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        theme = self.theme_def
        width = self.size.width
        page = Style(bgcolor=theme.page_bg, color=theme.text_fg)
        row_index = y + int(self.scroll_offset.y)
        if not (0 <= row_index < len(self.rows)):
            return Strip.blank(width, page)
        entry, depth, key = self.rows[row_index]
        is_cursor = row_index == self.cursor

        divider_style = Style(color=theme.unknown_fg, bgcolor=theme.page_bg)
        n = self.num_panes
        col_width = (width - (n - 1)) // n

        if entry.isdir:
            glyph = "▾" if key in self.expanded else "▸"
        else:
            glyph = " "

        text = Text()
        for pane in range(n):
            if pane:
                text.append("│", divider_style)
            bg = theme.page_bg
            if is_cursor:
                bg = (theme.selection_bg if pane == self.focus_pane
                      else theme.current_line_bg)
            cell_style = state_style(theme, entry.pane_state(pane))
            cell_style += Style(bgcolor=bg)
            if is_cursor and pane == self.focus_pane:
                cell_style += Style(color=theme.selection_fg)
            cell = Text()
            cell.append("  " * depth + glyph + " ", cell_style)
            cell.append(entry.names[pane], cell_style)
            cell.truncate(col_width)
            cell.pad_right(col_width - cell.cell_len)
            cell.stylize(Style(bgcolor=bg))
            text.append(cell)
        segments = list(text.render(self.app.console))
        return Strip(segments).adjust_cell_length(width, page)


class DirDiffView(ComparisonView):
    """One folder comparison: the DirTree plus its model and actions."""

    def __init__(
        self,
        paths: Sequence[str],
        theme_def: Theme,
        comparison_factory: Callable[..., DirComparison] = DirComparison,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        # Validates the roots here, like FileDiffView reading its files
        self.comparison = comparison_factory(paths)
        self.status_text = "Scanning…"
        self._delete_pending: Optional[DirEntry] = None
        self.dirtree: Optional[DirTree] = None

    @property
    def num_panes(self) -> int:
        return self.comparison.num_panes

    @property
    def tab_label(self) -> str:
        return " — ".join(shorten_names(*self.comparison.roots))

    def compose(self) -> ComposeResult:
        self.dirtree = DirTree(self.theme_def, self.num_panes, id="dirtree")
        yield self.dirtree

    def on_mount(self) -> None:
        self._start_scan()

    def focus_default(self) -> None:
        self.dirtree.focus()

    # --- Scanning -------------------------------------------------------------

    def _start_scan(self, cursor_key: Optional[RowKey] = None) -> None:
        self.status_text = "Scanning…"
        self.post_message(self.StatusChanged(self))
        self._scan_worker(cursor_key)

    @work(thread=True, exclusive=True)
    def _scan_worker(self, cursor_key: Optional[RowKey]) -> None:
        last_update = 0.0
        for progress in self.comparison.scan_iter():
            now = time.monotonic()
            if now - last_update >= SCAN_PROGRESS_INTERVAL:
                last_update = now
                self.app.call_from_thread(self._scan_progress, progress)
        self.app.call_from_thread(self._scan_done, cursor_key)

    def _scan_progress(self, folder: str) -> None:
        self.status_text = f"Scanning {folder}"
        self.post_message(self.StatusChanged(self))

    def _scan_done(self, cursor_key: Optional[RowKey]) -> None:
        root = self.comparison.root_entry
        self.dirtree.set_root(root, cursor_key=cursor_key)
        differences = sum(
            1 for e in root.walk()
            if e.different and e.state != STATE_EMPTY and not e.isdir
        )
        self.status_text = (
            "identical" if differences == 0 else f"{differences} differences"
        )
        self.post_message(self.StatusChanged(self))

    def _rescan(self) -> None:
        row = self.dirtree.cursor_row
        self._start_scan(cursor_key=row[2] if row else None)

    def action_refresh(self) -> None:
        """Ctrl+R / F5 (Meld's view refresh)."""
        self._rescan()

    # --- Row activation ---------------------------------------------------------

    def on_dir_tree_activated(self, message: DirTree.Activated) -> None:
        entry = message.entry
        paths = [
            p for p, exists in zip(entry.paths, entry.exists) if exists
        ]
        if len(paths) < 2:
            # Nothing to compare against; Meld would open a single-file
            # comparison, which tmeld doesn't have
            self.app.bell()
            return
        self.post_message(self.OpenComparison(paths))

    # --- Copy / delete (upstream copy_selected / delete_selected) ---------------

    def _copy_selected(self, direction: int) -> None:
        src_pane = self.dirtree.focus_pane
        dst_pane = src_pane + direction
        if not (0 <= dst_pane < self.num_panes):
            self.app.bell()
            return
        row = self.dirtree.cursor_row
        if row is None:
            self.app.bell()
            return
        entry = row[0]
        if entry.state in (STATE_EMPTY, STATE_ERROR) or not entry.exists[src_pane]:
            self.app.bell()
            return
        src, dst = entry.paths[src_pane], entry.paths[dst_pane]
        try:
            if os.path.isfile(src):
                dstdir = os.path.dirname(dst)
                if not os.path.exists(dstdir):
                    os.makedirs(dstdir)
                copy2(src, dstdir)
            elif os.path.isdir(src):
                # upstream prompts before replacing an existing tree;
                # copytree merges in place, which is closer to rsync
                # semantics and reversible by copying back
                copytree(src, dst)
        except (OSError, shutil.Error) as err:
            self.notify(f"Error copying {src}: {err}", severity="error")
            return
        self._rescan()

    def action_push_left(self) -> None:
        """Alt+Left: folder-copy-left (copy row to the left pane)."""
        self._copy_selected(-1)

    def action_push_right(self) -> None:
        """Alt+Right: folder-copy-right (copy row to the right pane)."""
        self._copy_selected(1)

    def on_dir_tree_delete_requested(
        self, message: DirTree.DeleteRequested
    ) -> None:
        entry, pane = message.entry, message.pane
        if not entry.exists[pane]:
            self.app.bell()
            return
        if self._delete_pending is not entry:
            self._delete_pending = entry
            self.notify(
                f"Delete {entry.paths[pane]}? Press Delete again to confirm",
                severity="warning",
                timeout=3,
            )
            self.set_timer(3, self._clear_delete_pending)
            return
        self._delete_pending = None
        path = entry.paths[pane]
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as err:
            self.notify(f"Error deleting {path}: {err}", severity="error")
            return
        self._rescan()

    def _clear_delete_pending(self) -> None:
        self._delete_pending = None

    # --- Navigation (chunk-nav parity: differing rows are the "chunks") ---------

    def action_next_chunk(self) -> None:
        if not self.dirtree.move_to_difference(+1):
            self.app.bell()

    def action_previous_chunk(self) -> None:
        if not self.dirtree.move_to_difference(-1):
            self.app.bell()

    def action_next_pane(self) -> None:
        self.dirtree.focus_pane = (self.dirtree.focus_pane + 1) % self.num_panes
        self.dirtree.refresh()

    def action_previous_pane(self) -> None:
        self.dirtree.focus_pane = (self.dirtree.focus_pane - 1) % self.num_panes
        self.dirtree.refresh()
