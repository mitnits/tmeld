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

- Phase 0 (parity spec extraction) is next: clone upstream Meld, extract
  palette + keymap + scroll-behavior spec into PARITY.md, set up vendoring
  script recording the upstream commit hash.
