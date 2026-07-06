# tmeld — project context

Terminal-UI port of Meld (the GNOME visual diff/merge tool). Goal: a Meld
user's muscle memory, color memory, and workflow transfer to a TUI that works
over plain SSH. Read PLAN.md first — it holds the full architecture and the
phase plan; this file holds working context that isn't in the plan.

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

- Primary dev machine: Windows (this repo, C:\Users\roman\projects\Meld).
  Python 3.13, git available. Edit + unit tests run here.
- Runtime target: **"mini"** — Debian 13 Linux box, passwordless SSH as `mini`.
  Python 3.13.5. Bare repo at `~/git/tmeld.git` (git remote named `mini`),
  working clone at `~/tmeld`. Deploy = push to `mini`, then
  `ssh mini "git -C ~/tmeld pull"`.
- Real-world verification: run tmeld on mini through an actual SSH session.
  The user tests from a Mac (iTerm2) → mini.
- User's multiplexer notes: starting with plain SSH (no screen/tmux). GNU
  screen 4.x is a known truecolor-killer — see PLAN.md risks.

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
- Still open: chunk boundary lines (underline approx), Phase 4 linkmap,
  Phase 5 three-way, Phase 6 dirdiff, Phase 7 VC view, Phase 8 graphics.
