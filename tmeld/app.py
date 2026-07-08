"""tmeld application shell: comparison tabs, global keys, CLI.

One FileDiffView per comparison, in a Meld-style notebook: `tmeld a b`
opens one tab; each repeatable `--diff x y [z]` opens another (upstream
meldapp.py opens the positional comparison first, then --diff groups).
The tab bar stays hidden until there's more than one comparison.

Keybindings follow Meld exactly (PARITY.md §3) and are window-level, as
upstream accelerators are; each action delegates to the active view.
Alt+arrow availability depends on the terminal sending ESC-prefixed
sequences ("Option as Esc+" in iTerm2); Ctrl+D/E are Meld's own
alternates for chunk navigation.

Exit code is 0 only if every 3-way view's middle pane was saved (the
git mergetool contract; closing a merge tab unsaved counts as failure).
"""

import argparse
import os
import sys
from typing import List, Optional, Sequence, Tuple

from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.markup import escape
from textual.binding import Binding
from textual import events
from textual.widgets import Footer, Static, TabbedContent, TabPane, Tabs

from tmeld import __version__
from tmeld.comparisonview import ComparisonView
from tmeld.dirdiff import DirDiffView
from tmeld.filediff import REDIFF_DEBOUNCE, FileDiffView  # noqa: F401 (re-export)
from tmeld.palette import DEFAULT_THEME, THEMES
from tmeld.vcview import VcView

# (paths, output-override-for-middle-pane)
DiffSpec = Tuple[List[str], Optional[str]]


def make_view(
    paths: Sequence[str], theme_def, output: Optional[str] = None,
    show_line_numbers: bool = False,
) -> ComparisonView:
    """Files -> FileDiffView, folders -> DirDiffView, single path ->
    VcView (Meld auto-detects comparison type from the arguments)."""
    if len(paths) == 1:
        if output:
            raise ValueError("--output requires a file comparison")
        return VcView(paths[0], theme_def)
    dir_flags = [os.path.isdir(p) for p in paths]
    if all(dir_flags):
        if output:
            raise ValueError("--output requires a file comparison")
        return DirDiffView(paths, theme_def)
    if any(dir_flags):
        raise ValueError(
            "cannot mix files and folders in one comparison"
        )
    return FileDiffView(paths, theme_def, output=output,
                        show_line_numbers=show_line_numbers)


class TabArrows(Static):
    """Meld's notebook shift arrows: shown top-right when the tab strip
    overflows; each ◀/▶ scrolls the strip (markup @click actions)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            "[@click=app.scroll_tabs_left]◀[/]"
            "[@click=app.scroll_tabs_right] ▶[/]",
            **kwargs,
        )


class TmeldApp(App):
    TITLE = "tmeld"

    CSS = """
    Screen {
        layers: base tabarrows;
    }
    TabPane {
        padding: 0;
    }
    TabbedContent.single Tabs {
        display: none;
    }
    /* Tab strip. The active tab is the pane's page background, so it reads as
       the sheet you are looking at; an inactive tab is halfway to the bar; and
       the bar is pushed hard away from the page so both stand out and adjacent
       inactive tabs are separated by their caps. Every pair clears WCAG's 3:1
       for non-text UI -- the gutter's grey gave 1.14:1 and was unusable.
       Text is chosen per surface; the active tab's colour is unmistakable, so
       Textual's Underline is dropped, buying back a terminal row. */
    Tabs, ContentTabs {
        height: 1;
        background: $tmeld-tabbar;
    }
    Tabs Underline, ContentTabs Underline {
        display: none;
    }
    Tab {
        /* the slanted caps occupy the cells Tab reserves for padding */
        padding: 0;
        background: $tmeld-tab-inactive;
        color: $tmeld-tab-inactive-fg;
    }
    Tab:hover {
        background: $tmeld-tab-inactive;
        color: $tmeld-tab-inactive-fg;
        text-style: underline;
    }
    Tab.-active {
        background: $tmeld-page;
        color: $tmeld-tab-active-fg;
        text-style: bold;
    }
    Tab.-active:hover {
        background: $tmeld-page;
    }
    TabArrows {
        layer: tabarrows;
        dock: right;
        width: 4;
        height: 1;
        content-align: right middle;
        background: $tmeld-tabbar;
        color: $tmeld-tabbar-fg;
        display: none;
    }
    DiffPane {
        width: 1fr;
        border: none;
        /* One rule colour for every pane, focused or not: the blinking cursor
           already says which pane you are in, and an accent-coloured title
           border made the rule change colour halfway across the gutter. */
        border-top: round $border;
    }
    """

    BINDINGS = [
        Binding("alt+down,ctrl+d", "next_chunk", "Next change", priority=True),
        Binding("alt+up,ctrl+e", "previous_chunk", "Prev change", priority=True),
        Binding("alt+right", "push_right", "Push right", priority=True),
        Binding("alt+left", "push_left", "Push left", priority=True),
        # Meld action names: "pull left/right" is the arrow drawn on the
        # button; the key pulls FROM that neighbor into the focused pane
        Binding("alt+shift+right", "pull_left", "Pull from left", priority=True),
        Binding("alt+shift+left", "pull_right", "Pull from right", priority=True),
        Binding("alt+delete", "delete_chunk", "Delete", priority=True),
        Binding("alt+left_square_bracket", "copy_up_left", "Copy above left",
                show=False, priority=True),
        Binding("alt+right_square_bracket", "copy_up_right", "Copy above right",
                show=False, priority=True),
        Binding("alt+semicolon", "copy_down_left", "Copy below left",
                show=False, priority=True),
        Binding("alt+apostrophe", "copy_down_right", "Copy below right",
                show=False, priority=True),
        Binding("ctrl+k", "next_conflict", "Next conflict", show=False,
                priority=True),
        Binding("ctrl+j", "previous_conflict", "Prev conflict", show=False,
                priority=True),
        Binding("alt+m", "merge_all", "Merge all", show=False, priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("ctrl+r,f5", "refresh", "Refresh", show=False, priority=True),
        Binding("alt+pagedown", "next_pane", "Next pane", show=False),
        Binding("alt+pageup", "previous_pane", "Prev pane", show=False),
        Binding("ctrl+w", "close_tab", "Close tab", show=False, priority=True),
        Binding("ctrl+alt+pagedown", "next_tab", "Next tab", show=False),
        Binding("ctrl+alt+pageup", "previous_tab", "Prev tab", show=False),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        paths: Optional[Sequence[str]] = None,
        theme_name: str = DEFAULT_THEME,
        output: Optional[str] = None,
        diffs: Optional[Sequence[DiffSpec]] = None,
        graphics: str = "none",
        cell_px: Optional[Tuple[int, int]] = None,
        show_line_numbers: bool = False,
    ):
        # Before super(): App.__init__ parses CSS, and get_css_variables()
        # feeds it the palette.
        self.theme_def = THEMES[theme_name]
        super().__init__()
        # Meld hides line numbers by default; the status bar carries the
        # cursor position instead.
        self.show_line_numbers = show_line_numbers
        # Tier 2 pixel linkmap: "kitty" | "sixel" | "none" (gutters read
        # these on mount)
        self.graphics = graphics
        self.cell_px = cell_px or (8, 16)
        if diffs is None:
            diffs = [(list(paths), output)]
        # Views are built here so file errors surface before the TUI
        # starts; self.views keeps closed tabs too — a merge tab closed
        # unsaved must still fail the exit-status contract.
        self.views: List[ComparisonView] = [
            make_view(spec_paths, self.theme_def, output=spec_output,
                      show_line_numbers=show_line_numbers)
            for spec_paths, spec_output in diffs
        ]
        self._tab_ids = {view: f"tab{k}" for k, view in enumerate(self.views)}
        self._tab_counter = len(self.views)
        self._active_view = self.views[0]
        self._close_pending: Optional[ComparisonView] = None

    # The active comparison; also the delegation target for the test
    # suite's app.panes / app.comparison / ... shorthands.
    @property
    def view(self) -> ComparisonView:
        return self._active_view

    @property
    def comparison(self):
        return self.view.comparison

    @property
    def panes(self):
        return self.view.panes

    @property
    def gutters(self):
        return self.view.gutters

    @property
    def dirty(self):
        return self.view.dirty

    @property
    def current_chunk(self):
        return self.view.current_chunk

    @property
    def merged_saved(self) -> bool:
        return self.view.merged_saved

    # Slanted tab caps: the tab widens toward the bottom, as tabs do. Each cap
    # cell is bar-coloured with a triangle of the tab's own colour, so the pair
    # reads as the sloping edge of the sheet. They replace Tab's `padding: 0 1`
    # rather than adding width.
    TAB_CAP_LEFT = "◢"   # U+25E2 black lower right triangle
    TAB_CAP_RIGHT = "◣"  # U+25E3 black lower left triangle

    def _tab_is_active(self, tab_id: str) -> bool:
        try:
            return self.query_one(TabbedContent).active == tab_id
        except NoMatches:
            return False

    def _tab_label(self, view: ComparisonView) -> str:
        """Tab title markup plus a click-to-close ✕ (Meld tabs have one
        too). Textual parses the @click action from the markup."""
        tab_id = self._tab_ids[view]
        theme = self.theme_def
        bar = theme.tab_bar_bg
        tab = (
            (theme.page_bg or ("#000000" if theme.dark else "#ffffff"))
            if self._tab_is_active(tab_id) else theme.tab_inactive_bg
        )
        cap = f"[{tab} on {bar}]"
        return (
            f"{cap}{self.TAB_CAP_LEFT}[/]"
            f"{escape(view.tab_label)} "
            f"[dim @click=app.close_tab_by_id('{tab_id}')]✕[/]"
            f"{cap}{self.TAB_CAP_RIGHT}[/]"
        )

    def _refresh_tab_labels(self) -> None:
        """Caps are tinted by active state, so every label is rebuilt on switch."""
        try:
            tabs = self.query_one(TabbedContent)
        except NoMatches:
            return
        for view, tab_id in self._tab_ids.items():
            try:
                tabs.get_tab(tab_id).label = self._tab_label(view)
            except Exception:  # tab already closed
                continue

    def get_css_variables(self) -> dict:
        """Expose the Meld palette to App.CSS as $tmeld-* variables."""
        variables = super().get_css_variables()
        theme = self.theme_def
        page = theme.page_bg or ("#000000" if theme.dark else "#ffffff")
        variables.update({
            "tmeld-page": page,
            "tmeld-gutter": theme.gutter_bg,
            "tmeld-tabbar": theme.tab_bar_bg,
            "tmeld-tab-inactive": theme.tab_inactive_bg,
            # text has to be picked per surface: the scheme's own colour is
            # unreadable on a mid-grey tab
            "tmeld-tab-active-fg": theme.readable_on(page),
            "tmeld-tab-inactive-fg": theme.readable_on(theme.tab_inactive_bg),
            "tmeld-tabbar-fg": theme.readable_on(theme.tab_bar_bg),
            "tmeld-text": theme.text_fg,
            "tmeld-dim": theme.dimmed_fg,
        })
        return variables

    def compose(self) -> ComposeResult:
        single = "single" if len(self.views) == 1 else ""
        with TabbedContent(id="tabs", classes=single):
            for view in self.views:
                with TabPane(self._tab_label(view), id=self._tab_ids[view]):
                    yield view
        yield TabArrows(id="tab-arrows")
        yield Footer()

    def on_mount(self) -> None:
        self.view.focus_default()
        self._harden_tab_activation()
        self.call_after_refresh(self._update_tab_arrows)

    def _harden_tab_activation(self) -> None:
        """Clicking a tab's ✕ removes the tab while the Tab.Clicked
        event is still queued; Textual's ContentTabs._activate_tab then
        raises on the stale tab id and takes the app down. Skip
        activation for tabs that are already gone.

        Instance-level patch of a private Textual API (like
        pane._set_theme) — re-audit on textual major bump.
        """
        tabs = self.query_one(Tabs)
        original_activate = tabs._activate_tab

        def activate_if_present(tab) -> None:
            if tab.id and tabs.query(f"#tabs-list > #{tab.id}"):
                original_activate(tab)

        tabs._activate_tab = activate_if_present

    # --- Tabs ---------------------------------------------------------------

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        view = event.pane.query_one(ComparisonView)
        previous = self._active_view
        self._active_view = view
        if previous is not view:
            previous.on_tab_hidden()
        self.sub_title = view.status_text
        self._refresh_tab_labels()
        view.focus_default()
        view.on_tab_shown()

    def on_comparison_view_status_changed(
        self, message: ComparisonView.StatusChanged
    ) -> None:
        view = message.view
        tab_id = self._tab_ids.get(view)
        if tab_id is not None:
            tabs = self.query_one(TabbedContent)
            tabs.get_tab(tab_id).label = self._tab_label(view)
            # a longer/shorter label can tip the strip into overflow
            self.call_after_refresh(self._update_tab_arrows)
        if view is self.view:
            self.sub_title = view.status_text

    def on_comparison_view_open_comparison(
        self, message: ComparisonView.OpenComparison
    ) -> None:
        """Row activated in a folder/VC view: open a comparison tab."""
        try:
            view = FileDiffView(
                message.paths,
                self.theme_def,
                output=message.output,
                labels=message.labels,
                readonly=message.readonly,
                tab_title=message.tab_title,
                show_line_numbers=self.show_line_numbers,
            )
        except OSError as err:
            self.notify(str(err), severity="error")
            return
        tab_id = f"tab{self._tab_counter}"
        self._tab_counter += 1
        self.views.append(view)
        self._tab_ids[view] = tab_id
        tabs = self.query_one(TabbedContent)
        tabs.add_pane(TabPane(self._tab_label(view), view, id=tab_id))
        tabs.active = tab_id
        self._update_single_class()

    def _update_single_class(self) -> None:
        open_count = len(self._tab_ids)
        self.query_one(TabbedContent).set_class(open_count == 1, "single")
        self._update_tab_arrows()

    # --- Tab-strip overflow (Meld's notebook shift arrows) -------------------

    def _tab_strip_overflow(self) -> int:
        """How many cells of tab labels don't fit; 0 when they all do."""
        tabs = self.query_one(Tabs)
        if not tabs.display:
            return 0
        bar = tabs.query_one("#tabs-list-bar")
        scroll = tabs.query_one("#tabs-scroll")
        return max(0, bar.region.width - scroll.region.width)

    def _update_tab_arrows(self) -> None:
        self.query_one(TabArrows).display = self._tab_strip_overflow() > 0

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._update_tab_arrows)

    def _scroll_tab_strip(self, direction: int) -> None:
        scroll = self.query_one(Tabs).query_one("#tabs-scroll")
        # force: the strip container is overflow:hidden, and non-forced
        # scrolls are refused for non-scrollable containers
        scroll.scroll_relative(x=direction * 16, animate=False, force=True)

    def action_scroll_tabs_left(self) -> None:
        self._scroll_tab_strip(-1)

    def action_scroll_tabs_right(self) -> None:
        self._scroll_tab_strip(1)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._wheel_over_tabs(event, +1)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._wheel_over_tabs(event, -1)

    def _wheel_over_tabs(self, event, direction: int) -> None:
        """The strip's own container is overflow:hidden, so wheel events
        bubble up unhandled; scroll it when the pointer is over it."""
        tabs = self.query_one(Tabs)
        if tabs.display and tabs.region.contains(
            event.screen_x, event.screen_y
        ):
            self._scroll_tab_strip(direction)
            event.stop()

    def action_close_tab(self) -> None:
        self._request_close(self.view)

    def action_close_tab_by_id(self, tab_id: str) -> None:
        """Target of the ✕ in each tab label."""
        for view, known_id in self._tab_ids.items():
            if known_id == tab_id:
                self._request_close(view)
                return

    def _request_close(self, view: ComparisonView) -> None:
        if any(view.dirty) and self._close_pending is not view:
            # Meld prompts on unsaved changes; the TUI equivalent is a
            # confirm-by-repeat within the notification's lifetime
            self._close_pending = view
            self.notify(
                "Unsaved changes — close again to confirm",
                severity="warning",
                timeout=3,
            )
            self.set_timer(3, self._clear_close_pending)
            return
        self._close_pending = None
        if len(self._tab_ids) == 1:
            self.exit()
            return
        tab_id = self._tab_ids.pop(view)
        self.query_one(TabbedContent).remove_pane(tab_id)
        self._update_single_class()

    def _clear_close_pending(self) -> None:
        self._close_pending = None

    def action_next_tab(self) -> None:
        self.query_one(Tabs).action_next_tab()

    def action_previous_tab(self) -> None:
        self.query_one(Tabs).action_previous_tab()

    # --- Delegation to the active comparison --------------------------------
    # Window-level accelerators (as in Meld); each view implements the
    # action_* methods that make sense for it, the rest bell.

    def _delegate(self, name: str) -> None:
        method = getattr(self.view, name, None)
        if callable(method):
            method()
        else:
            self.bell()

    def save_pane(self, i: int) -> None:
        self.view.save_pane(i)

    def action_save(self) -> None:
        self._delegate("action_save")

    def action_refresh(self) -> None:
        self._delegate("action_refresh")

    def action_next_chunk(self) -> None:
        self._delegate("action_next_chunk")

    def action_previous_chunk(self) -> None:
        self._delegate("action_previous_chunk")

    def action_push_right(self) -> None:
        self._delegate("action_push_right")

    def action_push_left(self) -> None:
        self._delegate("action_push_left")

    def action_pull_left(self) -> None:
        self._delegate("action_pull_left")

    def action_pull_right(self) -> None:
        self._delegate("action_pull_right")

    def action_copy_up_left(self) -> None:
        self._delegate("action_copy_up_left")

    def action_copy_up_right(self) -> None:
        self._delegate("action_copy_up_right")

    def action_copy_down_left(self) -> None:
        self._delegate("action_copy_down_left")

    def action_copy_down_right(self) -> None:
        self._delegate("action_copy_down_right")

    def action_delete_chunk(self) -> None:
        self._delegate("action_delete_chunk")

    def action_merge_all(self) -> None:
        self._delegate("action_merge_all")

    def action_next_conflict(self) -> None:
        self._delegate("action_next_conflict")

    def action_previous_conflict(self) -> None:
        self._delegate("action_previous_conflict")

    def action_next_pane(self) -> None:
        self._delegate("action_next_pane")

    def action_previous_pane(self) -> None:
        self._delegate("action_previous_pane")

    def exit_status(self) -> int:
        """Mergetool contract: every 3-way view must have been saved."""
        return 0 if all(v.merge_resolved() for v in self.views) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tmeld", description="Meld, in your terminal"
    )
    parser.add_argument(
        "files", nargs="*",
        help="two or three files (3-way: LOCAL MERGED REMOTE) or folders "
             "to compare; a single path opens the version-control view",
    )
    parser.add_argument(
        "--diff", action="append", nargs="+", default=[], metavar="PATH",
        help="open an extra comparison tab for 1-3 paths (repeatable)",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="write middle-pane saves to FILE (3-way only, like meld -o)",
    )
    parser.add_argument(
        "--theme", choices=sorted(THEMES), default=DEFAULT_THEME
    )
    parser.add_argument(
        "--graphics", choices=("auto", "none", "sixel", "kitty"),
        default="auto",
        help="pixel linkmap protocol (auto probes the terminal at startup)",
    )
    parser.add_argument(
        "--show-line-numbers", action="store_true",
        help="show line numbers in the panes (Meld hides them by default; "
             "the status bar always shows the cursor position)",
    )
    parser.add_argument(
        "--version", action="version", version=f"tmeld {__version__}"
    )
    args = parser.parse_args(argv)

    if args.files and len(args.files) > 3:
        parser.error("expected 1-3 paths")
    for group in args.diff:
        if len(group) > 3:
            parser.error("--diff takes 1-3 paths")
    if not args.files and not args.diff:
        parser.error("expected 1-3 paths to compare")
    if args.output and len(args.files) != 3:
        parser.error("--output requires a 3-way comparison")

    diffs: List[DiffSpec] = []
    if args.files:
        diffs.append((args.files, args.output))
    diffs.extend((group, None) for group in args.diff)

    graphics = args.graphics
    if graphics == "auto":
        from tmeld.term import probe_graphics

        graphics = probe_graphics()
    cell_px = None
    if graphics != "none":
        from tmeld.term import cell_pixel_size

        cell_px = cell_pixel_size()

    try:
        app = TmeldApp(
            theme_name=args.theme, diffs=diffs,
            graphics=graphics, cell_px=cell_px,
            show_line_numbers=args.show_line_numbers,
        )
    except (OSError, ValueError) as e:
        parser.error(str(e))
    app.run()
    return app.exit_status()


if __name__ == "__main__":
    sys.exit(main())
