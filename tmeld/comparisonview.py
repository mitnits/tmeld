"""ComparisonView: the contract every tab in the shell fulfills.

The app shell talks to tabs only through this interface: a label, a
status line, a StatusChanged message, a default focus target, and the
mergetool exit-status contribution. FileDiffView and DirDiffView (and
later the VC view) extend it; shell keybinding actions are dispatched
by name onto the active view, so views simply implement the action_*
methods that make sense for them (missing ones bell).
"""

from textual.containers import Horizontal
from textual.message import Message


class ComparisonView(Horizontal):
    class StatusChanged(Message):
        """Status text or dirty state changed; the shell refreshes the
        window subtitle and this view's tab label."""

        def __init__(self, view: "ComparisonView") -> None:
            self.view = view
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

    def merge_resolved(self) -> bool:
        """Mergetool contract: only unsaved 3-way file views fail."""
        return True
