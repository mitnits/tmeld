"""VcView: working-copy version-control status (Meld's VC view).

Backed by the vendored meld/vc/ plugins (git first-class; bzr, cvs,
darcs, hg, svn ride along). The scan is a UI-free port of upstream
vcview._search_recursively_iter with the gschema-default status
filters ('flatten', 'modified'): a flat list of interesting paths,
recursing into changed directories. Row activation is a port of
vcview.run_diff with the gschema defaults (left-is-local off, merge
order remote-merge-local):

  * normal changed file -> 2-way tab: read-only repo temp | working
  * conflict            -> 3-way tab: remote | merged | local temps,
    with middle-pane saves redirected to the working file (our -o
    machinery), exactly Meld's resolve flow

Keys: Return compares, Ctrl+R/F5 rescans, Delete deletes (twice to
confirm). Meld's commit accel is Ctrl+M, which a terminal cannot
distinguish from Enter, so commit is 'c' and revert 'r' on the tree
(free keys — the tree takes no text input); both are Meld menu actions
without usable accels here.

Temp files are chmod 0444 and cleaned up at exit (upstream
vcview.cleanup_temp).
"""

import atexit
import os
import shutil
import stat
import subprocess
import time
from typing import Callable, List, Optional, Sequence, Tuple

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from tmeld._vendor.meld.vc import get_vcs
from tmeld._vendor.meld.vc._vc import (
    CONFLICT_MERGED,
    CONFLICT_OTHER,
    CONFLICT_THIS,
    STATE_CONFLICT,
    STATE_EMPTY,
    STATE_ERROR,
    STATE_IGNORED,
    STATE_NORMAL,
    Entry,
)
from tmeld.comparisonview import ComparisonView
from tmeld.dircompare import DirEntry
from tmeld.dirdiff import SCAN_PROGRESS_INTERVAL, DirTree, RowKey
from tmeld.palette import Theme

_temp_files: List[str] = []


def _cleanup_temp() -> None:
    for f in _temp_files:
        try:
            os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
            os.remove(f)
        except OSError:
            pass


atexit.register(_cleanup_temp)


# upstream vcview.VcView.state_actions (minus the Gtk action names)
STATE_FILTER_FUNCS = {
    "modified": Entry.is_modified,
    "normal": Entry.is_normal,
    "unknown": Entry.is_nonvc,
    "ignored": Entry.is_ignored,
}

DEFAULT_STATUS_FILTERS = ("flatten", "modified")  # gschema vc-status-filters


class VcComparison:
    """Pure model: pick the VC for a location and scan its status."""

    def __init__(
        self,
        location: str,
        status_filters: Sequence[str] = DEFAULT_STATUS_FILTERS,
    ) -> None:
        location = os.path.abspath(location)
        candidates = [cls for cls, enabled in get_vcs(location) if enabled]
        if not candidates:
            raise ValueError(f"{location} is not under version control")
        self.vc = candidates[0](location)
        self.status_filters = tuple(status_filters)
        self.root_entry: Optional[DirEntry] = None

    @property
    def location(self) -> str:
        return self.vc.location

    def _display(self, entry: Entry, flattened: bool) -> str:
        name = (
            os.path.relpath(entry.path, self.location) if flattened
            else entry.name
        )
        status = entry.get_status()
        if entry.options:
            status = f"{status}; {entry.options}" if status else entry.options
        return f"{name}    {status}" if status else name

    def scan_iter(self):
        """Port of vcview._search_recursively_iter (states from the VC
        plugin instead of file comparison; same walk shape)."""
        yield "Scanning repository"
        self.vc.refresh_vc_state()

        location = self.location
        flattened = "flatten" in self.status_filters
        filters = [
            STATE_FILTER_FUNCS[k]
            for k in self.status_filters
            if k in STATE_FILTER_FUNCS
        ]

        root = DirEntry(
            (os.path.basename(location) or location,),
            (location,),
            (True,),
            isdir=True,
            state=STATE_NORMAL,
        )
        symlinks_followed = set()
        todo: List[Tuple[DirEntry, str]] = [(root, location)]

        while todo:
            parent, path = todo.pop(0)
            yield os.path.relpath(path, location)

            entries = [
                e for e in self.vc.get_entries(path)
                if any(f(e) for f in filters)
            ]
            entries = sorted(entries, key=lambda e: e.name)
            entries = sorted(entries, key=lambda e: not e.isdir)
            for e in entries:
                if e.isdir and e.is_present():
                    try:
                        st = os.lstat(e.path)
                    # Covers certain unreadable symlink cases; see bgo#585895
                    except OSError as err:
                        parent.children.append(DirEntry(
                            (f"{e.path!r}: {err.strerror}",), ("",),
                            (True,), isdir=False, state=STATE_ERROR,
                            error=err.strerror,
                        ))
                        continue

                    if stat.S_ISLNK(st.st_mode):
                        key = (st.st_dev, st.st_ino)
                        if key in symlinks_followed:
                            continue
                        symlinks_followed.add(key)

                    if flattened:
                        if e.state != STATE_IGNORED:
                            # If directory state is changed, render it
                            # in flattened mode.
                            if e.state != STATE_NORMAL:
                                root.children.append(self._row(e, flattened))
                            todo.append((root, e.path))
                        continue

                row = self._row(e, flattened)
                parent.children.append(row)
                if e.isdir and e.state != STATE_IGNORED and not flattened:
                    todo.append((row, e.path))

        self.root_entry = root

    def _row(self, entry: Entry, flattened: bool) -> DirEntry:
        # Flattened changed-directory marker rows never get children,
        # so mark them non-dir to keep them un-expandable
        isdir = entry.isdir and not flattened
        return DirEntry(
            (self._display(entry, flattened),),
            (entry.path,),
            (True,),
            isdir=isdir,
            state=entry.state,
        )


class VcTree(DirTree):
    """DirTree plus the VC-only key surface."""

    BINDINGS = [
        Binding("c", "commit", "Commit", show=False),
        Binding("r", "revert", "Revert", show=False),
    ]

    class CommitRequested(Message):
        pass

    class RevertRequested(Message):
        def __init__(self, entry: DirEntry) -> None:
            self.entry = entry
            super().__init__()

    def action_commit(self) -> None:
        self.post_message(self.CommitRequested())

    def action_revert(self) -> None:
        row = self.cursor_row
        if row is None or row[0].state in (STATE_EMPTY, STATE_ERROR):
            self.app.bell()
            return
        self.post_message(self.RevertRequested(row[0]))


class CommitScreen(ModalScreen[Optional[str]]):
    """Minimal stand-in for Meld's commit dialog: message + file list."""

    CSS = """
    CommitScreen {
        align: center middle;
    }
    #commit-box {
        width: 70%;
        max-width: 100;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $accent;
    }
    #commit-files {
        color: $text-muted;
        margin-bottom: 1;
        max-height: 8;
        overflow-y: auto;
    }
    #commit-buttons {
        height: auto;
        align-horizontal: right;
    }
    #commit-buttons Button {
        margin-left: 2;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, files: Sequence[str], prefill: str = "") -> None:
        super().__init__()
        self.files = list(files)
        self.prefill = prefill

    def compose(self) -> ComposeResult:
        count = len(self.files)
        with Vertical(id="commit-box"):
            yield Static(f"Commit {count} file{'s' if count != 1 else ''}:")
            yield Static("\n".join(self.files), id="commit-files")
            yield Input(
                value=self.prefill,
                placeholder="Commit message",
                id="commit-message",
            )
            with Horizontal(id="commit-buttons"):
                yield Button("Commit", variant="primary", id="commit-ok")
                yield Button("Cancel", id="commit-cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._finish()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "commit-ok":
            self._finish()
        else:
            self.dismiss(None)

    def _finish(self) -> None:
        message = self.query_one(Input).value.strip()
        if not message:
            self.app.bell()
            return
        self.dismiss(message)

    def action_cancel(self) -> None:
        self.dismiss(None)


class VcView(ComparisonView):
    """One working-copy status view: VcTree + the VC command surface."""

    def __init__(
        self,
        path: str,
        theme_def: Theme,
        comparison_factory: Callable[..., VcComparison] = VcComparison,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.theme_def = theme_def
        # Raises ValueError outside any repo, before the shell mounts us
        self.comparison = comparison_factory(path)
        self.status_text = "Scanning…"
        self._revert_pending: Optional[DirEntry] = None
        self._delete_pending: Optional[DirEntry] = None
        self.dirtree: Optional[VcTree] = None

    @property
    def vc(self):
        return self.comparison.vc

    @property
    def tab_label(self) -> str:
        name = os.path.basename(self.comparison.location.rstrip(os.sep))
        return f"{name} [{self.vc.NAME}]"

    def compose(self) -> ComposeResult:
        self.dirtree = VcTree(self.theme_def, 1, id="vctree")
        yield self.dirtree

    def on_mount(self) -> None:
        self._start_scan()

    def focus_default(self) -> None:
        self.dirtree.focus()

    # --- Scanning (same worker shape as DirDiffView) -------------------------

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
        changes = sum(1 for e in root.walk() if e.state != STATE_NORMAL)
        self.status_text = "clean" if changes == 0 else f"{changes} changes"
        self.post_message(self.StatusChanged(self))

    def _rescan(self) -> None:
        row = self.dirtree.cursor_row
        self._start_scan(cursor_key=row[2] if row else None)

    def action_refresh(self) -> None:
        self._rescan()

    # --- Row activation (port of vcview.run_diff) -----------------------------

    def on_dir_tree_activated(self, message: DirTree.Activated) -> None:
        path = message.entry.paths[0]
        if not path or os.path.isdir(path):
            self.app.bell()
            return
        basename = os.path.basename(path)
        state = self.vc.get_entry(path).state

        if state == STATE_CONFLICT and hasattr(self.vc, "get_path_for_conflict"):
            # gschema default vc-merge-file-order: remote-merge-local
            conflicts = (CONFLICT_OTHER, CONFLICT_MERGED, CONFLICT_THIS)
            diffs = [
                self.vc.get_path_for_conflict(path, conflict=c)
                for c in conflicts
            ]
            temps = [p for p, is_temp in diffs if is_temp]
            paths = [p for p, _is_temp in diffs]
            labels = (f"{basename} — remote", None, f"{basename} — local")
            spec = dict(
                paths=paths,
                output=path,
                labels=labels,
                readonly=(0, 2),
                tab_title=f"{basename} (remote, merge, local)",
            )
        else:
            comp_path = self.vc.get_path_for_repo_file(path)
            temps = [comp_path]
            # gschema default vc-left-is-local: false -> repo | working
            spec = dict(
                paths=[comp_path, path],
                labels=(f"{basename} — repository", None),
                readonly=(0,),
                tab_title=f"{basename} (repository, working)",
            )

        for temp_file in temps:
            os.chmod(temp_file, 0o444)
            _temp_files.append(temp_file)
        self.post_message(self.OpenComparison(**spec))

    # --- VC commands (port of vcview.runner/_command_iter, sync) --------------

    def _run_command(
        self, command: List[str], files: List[str], working_dir: str
    ) -> bool:
        try:
            result = subprocess.run(
                command + files,
                cwd=working_dir,
                capture_output=True,
                text=True,
            )
        except OSError as err:
            self.notify(str(err), severity="error")
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            self.notify(
                f"{' '.join(command)} failed: {detail}"[:200],
                severity="error",
                timeout=6,
            )
            return False
        return True

    def _runner(self, command, files, refresh, working_dir) -> None:
        if self._run_command(list(command), list(files), working_dir):
            if refresh:
                self._rescan()

    def _selected_paths(self) -> List[str]:
        row = self.dirtree.cursor_row
        if row is None or not row[0].paths[0]:
            return [self.comparison.location]
        return [row[0].paths[0]]

    def on_vc_tree_commit_requested(
        self, message: VcTree.CommitRequested
    ) -> None:
        try:
            files = self.vc.get_files_to_commit(self._selected_paths())
        except NotImplementedError:
            self.app.bell()
            return
        if not files:
            self.notify("Nothing to commit", timeout=3)
            return
        prefill = self.vc.get_commit_message_prefill() or ""

        def on_message(message_text: Optional[str]) -> None:
            if message_text:
                self.vc.commit(self._runner, files, message_text)

        self.app.push_screen(CommitScreen(files, prefill), on_message)

    def on_vc_tree_revert_requested(
        self, message: VcTree.RevertRequested
    ) -> None:
        entry = message.entry
        if self._revert_pending is not entry:
            self._revert_pending = entry
            self.notify(
                f"Revert {entry.paths[0]}? Press r again to confirm",
                severity="warning",
                timeout=3,
            )
            self.set_timer(3, self._clear_pending)
            return
        self._revert_pending = None
        self.vc.revert(self._runner, [entry.paths[0]])

    def on_dir_tree_delete_requested(
        self, message: DirTree.DeleteRequested
    ) -> None:
        entry = message.entry
        path = entry.paths[0]
        if not path or not os.path.exists(path):
            self.app.bell()
            return
        if self._delete_pending is not entry:
            self._delete_pending = entry
            self.notify(
                f"Delete {path}? Press Delete again to confirm",
                severity="warning",
                timeout=3,
            )
            self.set_timer(3, self._clear_pending)
            return
        self._delete_pending = None
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as err:
            self.notify(f"Error deleting {path}: {err}", severity="error")
            return
        self._rescan()

    def _clear_pending(self) -> None:
        self._revert_pending = None
        self._delete_pending = None

    # --- Navigation parity -----------------------------------------------------

    def action_next_chunk(self) -> None:
        if not self.dirtree.move_to_difference(+1):
            self.app.bell()

    def action_previous_chunk(self) -> None:
        if not self.dirtree.move_to_difference(-1):
            self.app.bell()
