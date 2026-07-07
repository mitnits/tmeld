# tmeld — project context

Terminal-UI port of Meld (the GNOME visual diff/merge tool). Goal: a Meld
user's muscle memory, color memory, and workflow transfer to a TUI that works
over plain SSH. Read PLAN.md first (full architecture + phase plan) and
HANDOFF.md (current state, gotchas, detailed forward plan); this file holds
working context that isn't in either.

## Load-bearing decisions (do not relitigate casually)

- **Vendor Meld's engine verbatim** (`meld/matchers/`, `meld/vc/`, filter
  logic) from gitlab.gnome.org/GNOME/meld. Identical chunking/merge behavior
  is the whole point. Project license is therefore GPL-2.
- **Python + Textual** for the UI. Notcurses and Rust were rejected (see
  PLAN.md D2).
- **Tiers are runtime capability levels, not build stages**: Tier 0 =
  256-color degraded, Tier 1 = truecolor/mouse/Unicode (the complete product),
  Tier 2 = sixel/Kitty-graphics pixel linkmap (enhancement only). Detected at
  startup via DA1 + Kitty APC probe, overridable by flag.
- Tier 1 must be fully functional with **exact Meld hex colors** — the palette
  comes from upstream `data/styles/meld.style-scheme.xml.in`, never approximated.

## Environment / workflow

- **Primary dev machine: mini** (Debian 13, this repo at `~/tmeld`,
  Python 3.13.5, venv at `.venv` with textual 8.2.8 + pytest + editable
  install). Development moved here from Windows on 2026-07-06 — see
  HANDOFF.md. Bare repo at `~/git/tmeld.git` (remote `origin`); push
  there after each coherent step. The old Windows clone
  (C:\Users\roman\projects\Meld, remote name `mini`) may be stale.
- Tests: `.venv/bin/python -m pytest` (all green expected). Run the app:
  `.venv/bin/tmeld a b` or the `~/.local/bin/tmeld` symlink.
- `upstream/` is a pinned Meld checkout @ 9c4be506 (gitignored) — the
  vendoring source and the target of all PARITY.md file:line references.
  Never pull/update it casually.
- Real-world verification: the user SSHes in from a Mac (iTerm2,
  sometimes gnome-terminal): `ssh -t mini tmeld /tmp/a.py /tmp/b.py`.
- User's multiplexer notes: plain SSH (no screen/tmux). GNU screen 4.x
  is a known truecolor-killer — see PLAN.md risks.

## Status

- Phase 0 done: PARITY.md holds palette, tree states, full keymap,
  sync-scroll algorithm (upstream pinned @ 9c4be506). User note: exact hex
  polish deferred until things work; meld-base already matches their memory.
- Phase 1 done: engine vendored (maint/vendor.py), gi_shim for the two GTK
  touchpoints, `python -m tmeld.dump a b` prints the chunk model, 10 tests
  green on Windows and mini. Note: 2-way stored chunks are right->left;
  dump (and future UI) must reverse for left-pane presentation.
- mini has a venv at ~/tmeld/.venv with pytest + textual installed.
- Phase 2 done: `tmeld a b` is a working read-only two-pane viewer.
  Spike verdict: TextArea subclass works — get_line() is the styling seam
  (pad row + stylize under for chunk fill, stylize over for inline).
  Sync scroll, chunk nav (Alt+Down/Up + Ctrl+D/E), ChunkMap, themes done.
  IMPORTANT painting semantics learned from upstream (see PARITY.md §1):
  pale "background" = row fill; saturated "line-background" = 1px chunk
  boundary lines (not yet rendered in TUI — candidate: underline on last
  chunk row); delete fills aliased to insert (one-sided lines = green).
- Both machines have editable installs: .venv locally, ~/tmeld/.venv on
  mini. Screenshot generator: spikes/screenshot.py -> docs/screenshot.svg.
- Phase 3 done: panes editable (debounced re-diff, dirty markers, Ctrl+S
  save preserving trailing newline), chunk actions ported from
  filediff.py (push Alt+Left/Right, pull Alt+Shift+arrows, delete
  Alt+Delete, EOF newline splice handled), ActionGutter between panes
  with clickable push arrows, ChunkMap click-to-jump, ctrl+shift+z redo,
  Ctrl+Q quit ('q' types now). Dirty semantics: text diverged from
  comparison.lines, OR set explicitly by chunk actions.
- User verdict rounds 1-5: works great incl. gnome-terminal (Tier 1
  portability confirmed). Fixed along the way: cursor-line wipe (3 tries
  — see pane._set_theme override), unreadable fg, stale line-cache
  highlights, sync-scroll jitter (async echo — see sync_scroll_to),
  gutter redesign to user's 3-col divider spec ('▶ │' / '│ ◀' / '▶ ◀').
  Save button lives in the dirty pane's title row (click = save).
  v0.1.0 tag = user-validated high-water mark.
- Phase 2/3 polish done: current-chunk emphasis (cursor-driven, blended
  fills via palette.blend), locate_chunk-based nav (cursor-relative like
  Meld), copy-above/below (Alt+[ ] ; '), ChunkMap viewport indicator.
- Phase 5 done: three-way merge. `tmeld local base remote` (middle =
  merged file, Meld/git-mergetool convention; `-o` redirects middle
  saves; exit code 0 iff middle saved — README has the .gitconfig
  stanza). Comparison/inline/gutters generalized to N panes; two
  ActionGutters each own an adjacent pane pair; scroll influence
  cascades through the middle pane (re-master on pane 1, upstream
  _sync_vscroll); chunk actions use upstream get_action_panes semantics
  (2-way push ignores focus; pull/copy = focused±1, bell off-edge —
  NOTE: 2-way pull now bells at the edge pane, was "pull from other").
  Ctrl+K/J conflict nav, Alt+M merge-all-non-conflicting (vendored
  Merger.merge_3_files, conflicts keep base). Merge-cache indices don't
  map 1:1 to per-pane chunk lists in 3-way — always differ.get_chunk.
  71 tests green. Demo files: /tmp/local.py /tmp/base.py /tmp/remote.py.
- User verdict 2026-07-07: 3-way merge works fine hands-on (moves, saves).
  v0.2.0 tag = user-validated high-water mark.
- Tabbed shell + multi-pair diff done (user caught the gap — `--diff` was
  in no phase): app.py split into shell (tabs, window-level bindings that
  delegate to the active view, CLI) and tmeld/filediff.py FileDiffView
  (everything one comparison owns; named after upstream). `tmeld a b
  --diff c d` = extra tabs, positional first (upstream meldapp order);
  tab labels = misc.shorten_names ported verbatim (tmeld/misc.py) joined
  " — " with '*' on dirty panes; tab bar hidden when single. Ctrl+W close
  (press twice if unsaved — TUI stand-in for Meld's save prompt),
  Ctrl+Alt+PgDn/PgUp cycle. exit_status aggregates over ALL views incl.
  closed tabs (closing an unsaved merge tab = mergetool failure). Tests
  rely on app.panes/comparison/dirty delegating to the active view.
  88 tests green.
- Still open: chunk boundary lines (underline approx), Phase 4 linkmap,
  Phase 6 dirdiff, Phase 7 VC view, Phase 8 graphics.
