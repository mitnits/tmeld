# HANDOFF — tmeld development moves to mini

Written 2026-07-06, when development moved from the Windows machine to
mini (`~/tmeld`). Read CLAUDE.md first (working context), then PLAN.md
(architecture + phase plan), then PARITY.md (extracted Meld behavior
spec). This document is the bridge: exact current state, hard-won
gotchas, and the detailed forward plan.

## 1. What tmeld is

A faithful terminal port of GNOME Meld (2-way diff/merge working today;
3-way, folder compare, VC view planned). Meld's actual engine is
vendored verbatim (`tmeld/_vendor/meld/`, pinned @ 9c4be506) so chunk
boundaries, inline highlights, and merge semantics are identical by
construction. UI is Python + Textual (pinned `>=8,<9`). GPL-2.

## 2. Current state (v0.1.0 tag + polish commits)

Phases 0-3 of PLAN.md complete, plus most Phase 2/3 polish. Working
today, user-validated over SSH from iTerm2 and gnome-terminal:

- `tmeld a b` — editable two-pane comparison, exact meld-base palette
  (meld-dark via `--theme`), inline (intra-line) highlights, line
  numbers, current-chunk emphasis following the cursor.
- Meld sync-scrolling (exact port: syncpoint ramp + chunk-interval
  interpolation), mouse wheel included.
- Chunk actions ported from filediff.py: push (Alt+Left/Right), pull
  (Alt+Shift+arrows), delete (Alt+Delete), copy above/below
  (Alt+[ ] ; '), EOF newline splice handled.
- Live re-diff ~250ms after last keystroke; dirty markers; Ctrl+S save
  (preserves trailing newline); clickable [ Save ] in dirty pane titles.
- ActionGutter (user-designed): 3 columns doubling as the pane divider —
  `│ │` quiet, `▶ │` / `│ ◀` / `▶ ◀` at chunk starts; arrows clickable
  to push. ChunkMap right edge: chunk positions + viewport band,
  click-to-jump.
- Navigation: Alt+Down/Up + Ctrl+D/E via Differ.locate_chunk (cursor-
  relative, Meld semantics). Clipboard: ctrl+c/x/v/z/y (+ctrl+shift+z),
  copy/cut toast; OSC 52 carries copies to the local clipboard.
- `tmeld-dump a b [c]` — chunk model as JSON (engine fidelity tool).
- 51 tests, all green on mini: `cd ~/tmeld && .venv/bin/python -m pytest`.

## 3. Environment on mini (everything is local now)

- Working clone: `~/tmeld` (this repo). Bare repo: `~/git/tmeld.git`
  (remote `origin`). The old Windows clone pushes/pulls the same bare
  repo as remote `mini`; after this handoff mini is primary — Windows
  may go stale, always trust the bare repo.
- venv: `~/tmeld/.venv` (Python 3.13.5, textual 8.2.8, pytest, tmeld
  editable install). User-facing commands symlinked:
  `~/.local/bin/tmeld`, `~/.local/bin/tmeld-dump`.
- Upstream Meld reference checkout: `~/tmeld/upstream` (gitignored),
  MUST stay at commit 9c4be506a26dd0a0742887ee4d6e28208812c792 — it is
  the source for `maint/vendor.py` and every `upstream/...` file:line
  reference in PARITY.md. Phases 5-7 read filediff.py/dirdiff.py/vcview.py
  from it constantly.
- Screenshot generator: `.venv/bin/python spikes/screenshot.py A B out.svg`
  (headless; docs/screenshot.svg is the checked-in sample).
- User tests by SSH-ing from their Mac (iTerm2, sometimes
  gnome-terminal): `ssh -t mini tmeld <a> <b>`. Demo files convention:
  /tmp/a.py /tmp/b.py.

## 4. Gotchas that cost real debugging time (do not rediscover)

- **Textual private APIs in use** (all commented at use sites; textual
  pinned `<9`, re-audit on any bump):
  1. `DiffPane` clears `self._line_cache` in `_rebuild_line_styles()` —
     TextArea's strip cache doesn't key on our chunk styling; skipping
     this brings back stale highlights after re-diff.
  2. `DiffPane._set_theme()` override — `TextAreaTheme.apply_css()` runs
     per-render and backfills any "non-configured" attribute from CSS;
     `_set_theme` copies themes via `dataclasses.replace()`, which
     rebuilds the configured set from non-None fields. Without the
     override, cursor_line_style gets backfilled and wipes chunk row
     backgrounds under the cursor (took three attempts to kill).
- **Painting semantics** (PARITY.md §1): pale scheme `background` = row
  fill; saturated `line-background` = 1px chunk boundary lines (NOT yet
  rendered in the TUI); `delete` fills/lines aliased to `insert`
  (one-sided lines paint green — upstream style.py get_common_theme).
- **Chunk orientation**: 2-way stored chunks are right->left
  (sequences[1] is the "a" side). Use single_changes(pane) /
  pair_changes(from, to) / get_chunk(i, from, to) — never raw
  merge-cache tags — or insert/delete flip sides.
- **Sync-scroll echo**: DiffPane.Scrolled messages are async; an
  in-progress flag CANNOT guard the sync loop (caused endless jitter).
  `sync_scroll_to()` tags the programmatic target; `watch_scroll_y`
  swallows exactly that echo. Keep this pattern for the 3rd pane.
- **Test conventions**: plain pytest funcs wrapping `asyncio.run` +
  `app.run_test()`; ALWAYS `await pilot.pause()` after `.focus()` before
  calling actions directly (focus lands async — bit us twice); assert
  colors via `app.export_screenshot()` SVG (count occurrences, not just
  presence — a single-pane wipe hides behind the other pane's color).
- **Dirty semantics**: dirty = pane text diverged from comparison.lines,
  OR set explicitly by chunk actions (comparison.lines is synced before
  the Changed message arrives, so actions must mark dirty themselves).
- `ssh -t mini tmeld ...` works (PATH via ~/.local/bin + login shell);
  plain `ssh mini tmeld` has no tty and won't run a TUI.

## 5. Forward plan (detailed)

Order recommended below; PLAN.md §5 has the original phase text.

### Next: Phase 5 — three-way merge (M)
The engine is already 3-way (Differ computes both diffs + conflicts;
AutoMergeDiffer vendored). UI work:
1. `Comparison` drops its 2-pane assert; lines/tags/inline per N panes.
   tmeld-dump already accepts 3 files — use it to sanity-check chunks.
2. App layout for 3 panes: pane0 | gutter01 | pane1 | gutter12 | pane2 |
   chunkmap. ActionGutter needs a (left_pane, right_pane) pair instead
   of hardcoded panes[0]/panes[1]; click pushes between ITS pair.
3. Scroll sync: influence map through the middle pane —
   `((1,2),(0,2),(1,0))` (PARITY.md §4 / filediff._sync_vscroll). The
   pure function sync_scroll_target is ready; wire per-pair chunks via
   pair_changes(master, other).
4. Chunk actions in 3-way: push acts between adjacent panes (focused ->
   neighbor in arrow direction); Meld semantics live in
   filediff.py:2611+ (copy/replace/delete already ported — verify the
   to_pane=2 paths of get_chunk).
5. Conflict styling (meld:conflict) works already via tags; add
   next/prev conflict (Ctrl+K / Ctrl+J) using differ.conflicts (list of
   merge-cache indices).
6. "Merge all non-conflicting" command (Meld: view menu) — iterate
   non-conflict chunks, push into middle.
7. `git mergetool` contract: `tmeld $LOCAL $BASE $REMOTE` with middle =
   merged output? NO — Meld's convention is
   `meld $LOCAL $MERGED $REMOTE` (middle pane IS the merged file).
   Support `--output` too (meld -o). Exit code 0 iff middle saved.
   Document .gitconfig stanza in README.
8. Tests: 3-way fixtures incl. conflict, auto-merge both-sides-same,
   push-to-middle, mergetool exit codes.

### Phase 2/3 leftovers (S, anytime)
- Chunk boundary lines: underline (SGR) on the last row of each chunk in
  the saturated line color — spike first, might look noisy; skip if so.
- PARITY.md §6 checklist: gutter hover states, gschema settings worth
  mirroring (wrap, tab width, ignore-blank-lines...) into a config file.

### Phase 4 — linkmap connectors (M)
The user's 3-col gutter already carries arrows; full Meld linkmap draws
connecting slopes. Tier 1 approximation: braille/box-drawing diagonals
in the gutter column linking chunk spans (chunk start row on left pane
to start row on right pane). Prototype before committing — the user may
prefer the current clean divider. Coordinate with Phase 8 (same geometry
feeds the pixel renderer).

### Phase 6 — folder comparison (M)
1. Vendor meld/filters.py logic (+ meld/misc parts it needs).
2. DirDiffView: Textual Tree (or two synced trees) with §2 state
   styling (fg/bold/italic/strike from tree.py:95). Scan off-thread.
3. Enter on file row opens a file-comparison tab (App gets tabbed
   views — introduce Screen/TabbedContent then).
4. Filename filters (F8 menu later; start with sensible defaults).
Reference: upstream/meld/dirdiff.py (states at :489, :1758).

### Phase 7 — version control view (M)
1. Vendor meld/vc/ (git first: upstream/meld/vc/git.py — shells out,
   nearly UI-free; strip GLib bits like helpers.py was).
2. VcView: status tree, diff-against-HEAD opens file comparison, basic
  commit (Ctrl+M) flow.
3. `tmeld .` auto-detects repo -> VcView.

### Phase 8 — Tier 2 graphics (M, independent)
1. Capability probe at startup: DA1 for sixel, Kitty APC query, fall
   back cleanly; `--graphics=none|sixel|kitty` override. (PLAN.md D3.)
2. Render the linkmap gutter as a bitmap (bezier slopes, chunk colors,
   anti-aliased) sized to the gutter rect; repaint on scroll. Kitty
   protocol preferred (image IDs = flicker-free updates), sixel second.
3. Terminal matrix doc: iTerm2 ✓ (sixel), WezTerm/kitty/Ghostty ✓
   (kitty protocol), Terminal.app ✗, mosh ✗, screen 4.x ✗ (also kills
   truecolor — warn at startup, PLAN.md risks).

### Phase 9 — polish backlog
Syntax highlighting under chunk fills (tree-sitter via TextArea
`language=`; map GtkSourceView classic scheme approximately); find /
find-replace (Ctrl+F/H); go-to-line (Ctrl+I); wrap toggle; config file
(~/.config/tmeld.toml) mirroring Meld gsettings names; keybinding remap
layer; large-file profiling (inline matcher is the hotspot — cap exists,
consider process pool from helpers.py which is vendored but unused).

## 6. Session workflow reminder

Work in ~/tmeld. After each coherent step: run tests, commit, push to
origin (bare repo — the user's other machines pull from it). Update the
Status section of CLAUDE.md as phases land; keep PARITY.md file:line
references pointing at upstream/ (pinned — do not `git pull` in
upstream/). The user tests hands-on from their Mac after each deployed
batch and gives sharp, actionable feedback — small shippable increments
work best.
