# Changelog

## 0.5.0 — 2026-07-09

### bmeld — Meld in your browser

- `bmeld a b` (or `tmeld --web a b`) serves the comparison to a browser:
  the same vendored engine, palette and keybindings, rendered by CodeMirror
  with real SVG linkmap curves. Files, folders and the version-control view
  all open as tabs; the process keeps the git-mergetool exit contract.
- `--bind ADDR` / `--advertise HOST` to reach it from other machines (loopback
  by default, with a warning when you open it wider). `--port` implies `--web`.
- One press of Ctrl-C exits cleanly even with a browser attached; a cache-busted
  build id in the footer tells you which bundle you are running.

### One command line

- `tmeld` and `bmeld` now share a single parser and validation. `--web` selects
  the browser and `--port` implies it; options that belong to the other
  front-end are refused rather than silently ignored.

### Terminal UI

- Esc quits (warning once if there are unsaved edits); a ✕ closes the lone
  comparison when the tab strip is hidden.
- Read-only panes get Meld's treatment: the push arrow toward them becomes a
  delete (✕), a 🔒 marks the file, and a `/dev/null` side (as p4 and git pass
  for an added or deleted file) is read-only automatically.
- One overview map per pane, so the two sides drift apart as they diverge; the
  first pane's scrollbar moves to the far left, clear of the linkmap.
- Line numbers are hidden by default, matching Meld; the status bar carries
  `Ln, Col`. `--show-line-numbers` restores them.
- Graphics mode: the gutter arrows are Meld's own icons, drawn into the linkmap
  and scaled with the font; the insert marker (Meld's thin line for a one-sided
  change) is drawn under kitty; the channel takes Meld's grey.
- Tab strip: slanted caps, palette colours meeting WCAG contrast, and one fewer
  row (the underline is gone).

### Packaging

- The Debian package now ships `bmeld` too, depends on the system aiohttp rather
  than bundling it (staying `Architecture: all`), and `maint/mkdeb.sh` stamps
  each build so rebuilds upgrade in place.

### Fixes

- Diff-chunk fills now cover the full scrollable width, not just the viewport.
- Empty panes are no longer reported as modified.

## 0.4.0 — 2026-07-07

- Version-control view: `tmeld .` shows working-copy status (git
  first-class; bzr/cvs/darcs/hg/svn backends vendored). Enter compares
  against the repository; conflicted files open a remote/merge/local
  three-way whose middle-pane saves resolve the working file. `c`
  commits, `r` reverts, Ctrl+R/F5 rescans.
- Tier-2 graphics: Meld's anti-aliased linkmap connector curves drawn
  between the panes on kitty-graphics or sixel terminals
  (auto-probed at startup, `--graphics` to override); the overview map
  gains pixel resolution so single-line chunks stay visible in huge
  files.
- Tab polish: click-to-close ✕, active-tab highlight, overflow shift
  arrows + mouse-wheel scrolling on the tab strip.
- Release packaging: LICENSE, vendoring provenance notices, CI.

## 0.3.0 — 2026-07-07

- Tabbed shell: `--diff a b [c]` opens extra comparison tabs (Meld's
  multi-pane window); Ctrl+W closes with unsaved-changes confirm.
- Folder comparison: `tmeld dirA dirB` (2/3-way) with Meld's tree
  states, default filename filters, copy/delete row actions, and
  Enter opening file comparisons in tabs.

## 0.2.0 — 2026-07-07

- Three-way merge: `tmeld LOCAL MERGED REMOTE` with conflict
  highlighting and navigation (Ctrl+K/J), merge-all-non-conflicting
  (Alt+M), per-pair action gutters, and the git mergetool contract
  (`-o`, exit code 0 only when the merge was saved).

## 0.1.0 — 2026-07-06

- Two-pane compare and edit with Meld's exact palette, keybindings,
  proportional sync scrolling, chunk push/pull/delete/copy actions,
  clickable action gutter and overview map, light and dark themes —
  over plain SSH.
