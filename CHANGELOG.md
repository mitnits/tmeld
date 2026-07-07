# Changelog

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
