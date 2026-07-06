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
- Phase 2 next: two-pane read-only viewer in Textual. First task is the
  spike: can TextArea do per-line backgrounds, or do we need a custom
  Line-API widget? Then exact-color chunk rendering + sync scrolling per
  PARITY.md §4.
