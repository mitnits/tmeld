"""ComparisonView: the contract every tab in the shell fulfills.

The app shell talks to tabs only through this interface: a label, a
status line, a StatusChanged message, a default focus target, and the
mergetool exit-status contribution. FileDiffView and DirDiffView (and
later the VC view) extend it; shell keybinding actions are dispatched
by name onto the active view, so views simply implement the action_*
methods that make sense for them (missing ones bell).
"""

from typing import List, Optional, Sequence

from textual.containers import Horizontal
from textual.message import Message


class ComparisonView(Horizontal):
    class StatusChanged(Message):
        """Status text or dirty state changed; the shell refreshes the
        window subtitle and this view's tab label."""

        def __init__(self, view: "ComparisonView") -> None:
            self.view = view
            super().__init__()

    class OpenComparison(Message):
        """Ask the shell to open a file-comparison tab (folder/VC views
        activate rows; upstream emits create_diff_signal)."""

        def __init__(
            self,
            paths: List[str],
            output: Optional[str] = None,
            labels: Optional[Sequence[Optional[str]]] = None,
            readonly: Sequence[int] = (),
            tab_title: Optional[str] = None,
        ) -> None:
            self.paths = paths
            self.output = output
            self.labels = labels
            self.readonly = readonly
            self.tab_title = tab_title
            super().__init__()

    status_text = ""
    # Per-pane unsaved-changes flags; views without editable panes keep
    # the empty default (the shell checks this before closing a tab)
    dirty = ()

    @property
    def tab_label(self) -> str:
        raise NotImplementedError

    def focus_default(self) -> None:
        """Focus the view's natural first widget on tab activation."""
        raise NotImplementedError

    def on_tab_shown(self) -> None:
        """Called by the shell when this view's tab becomes active."""

    def on_tab_hidden(self) -> None:
        """Called by the shell when another tab takes over (views with
        floating graphics clear them here)."""

    def merge_resolved(self) -> bool:
        """Mergetool contract: only unsaved 3-way file views fail."""
        return True
