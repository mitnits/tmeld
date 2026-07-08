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
  HANDOFF.md. Bare repo at `~/git/tmeld.git` (remote `origin`); PUBLIC
  repo at github.com/mitnits/tmeld (remote `github`, created
  2026-07-07, CI = GitHub Actions). Push BOTH remotes (+ --tags) after
  each coherent step. The old Windows clone (C:\Users\roman\projects\
  Meld, remote name `mini`) may be stale.
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
- Phase 6 done: folder comparison. `tmeld dirA dirB` (2- or 3-way).
  Engine: tmeld/dircompare.py — _files_same/CanonicalListing copied
  verbatim from upstream dirdiff.py, scan/state ports of
  _search_recursively_iter/_update_item_state; meld/filters.py vendored
  (maint/vendor.py); STATE_* constants copied from vc/_vc.py (re-point
  when Phase 7 vendors vc/); gschema default name filters/comparison
  args baked in. UI: tmeld/dirdiff.py DirDiffView + DirTree — ONE
  shared tree model rendered as N state-styled columns (PARITY.md §2
  styles; rows align across panes by construction), thread-worker scan,
  rescan preserves expansion/cursor by name-chain row keys, difference
  paths auto-expand. Keys per upstream accelerators: Return
  compare/toggle, Alt+Left/Right copy row (focused pane -> neighbor),
  Delete (twice to confirm), Alt+PgDn/PgUp switches focused COLUMN.
  Views now share tmeld/comparisonview.py ComparisonView (tab_label /
  status_text / StatusChanged / focus_default / merge_resolved); shell
  actions dispatch by name to the active view, missing = bell. Enter
  on a file row posts OpenComparison; shell opens a FileDiffView tab.
  Widget.tree is taken by Textual — the attr is view.dirtree. 108
  tests green. Demo dirs: /tmp/tmeld-demo/project-{a,b}.
- User verdict 2026-07-07 (round 6): tabs + folder comparison validated
  after fixes: black-bar border leak (be70dc4), single-click folder
  toggle, ✕ close buttons, overflow shift arrows + wheel, active-tab
  background. v0.3.0 tag = user-validated high-water mark.
- GOTCHA (black-bar regression, fixed be70dc4): rules that override a
  Textual widget's built-in styling (e.g. TextArea's :focus border)
  MUST live in TmeldApp.CSS — App CSS outranks all DEFAULT_CSS, but a
  view's DEFAULT_CSS competes with the widget's own by specificity and
  loses to pseudo-class rules. Symptom was focus-dependent (terminal
  blur removes :focus). Regression test in test_app.py.
- Phase 7 done: VC view. `tmeld .` (single path = VC view; all vc/
  backends vendored — git/bzr/cvs/darcs/hg/svn; gi_shim grew a Gio
  file-info shim over os.scandir; _vendor/meld/{conf,misc}.py are
  HAND-WRITTEN shims, not vendored — vendor.py must not overwrite
  them). tmeld/vcview.py: VcComparison ports vcview scan with gschema
  default filters (flatten+modified); VcView reuses DirTree (1 col);
  Enter ports run_diff — repo-temp (0o444, atexit cleanup) vs working,
  read-only left; conflicts open remote|merge|local 3-way with output=
  working file. FileDiffView grew labels/readonly/tab_title kwargs +
  readonly write guards; OpenComparison generalized onto
  ComparisonView (paths/output/labels/readonly/tab_title).
  KEY DEVIATION: commit = 'c', revert = 'r' on the tree (Ctrl+M IS
  Enter in terminals); Ctrl+R/F5 = refresh (also dirdiff). CommitScreen
  = modal input; runner is sync subprocess + rescan. 123 tests green
  (incl. end-to-end conflict resolve against scratch git repos).
- Phase 8 done: Tier-2 pixel linkmap. tmeld/linkmap.py = pure-python
  port of upstream linkmap.py geometry (bezier x_steps, ±0.5 nudges,
  f1-1 "last pixel of previous line", fill + 1px line-color stroke +
  current-chunk emphasis — ChunkStyle.line finally used) with a
  column-coverage rasterizer (connectors are x-monotone: one vertical
  span/column = exact vertical AA, no deps) + kitty (f=32, zlib,
  stable image id = flicker-free replace) and sixel (quantized
  palette, RLE) encoders. tmeld/term.py probes pre-Textual: kitty APC
  query + DA1; sixel = DA1 param 4; cell px via TIOCGWINSZ (fallback
  8x16). --graphics auto|none|sixel|kitty. Gutter in graphics mode =
  [▶][7 img cols][◀] (arrows/clicks unchanged, divider dropped);
  overlay painted via app._driver.write AFTER frames
  (call_after_refresh + dedup flag), repainted on scroll/styling/
  emphasis/resize/tab-shown; kitty images deleted on tab-hidden/
  unmount (they float above cells; sixel self-heals). Perf: ~13ms
  kitty / ~37ms sixel per repaint at 7x45 cells — fine; optimize
  sixel indexing loop if user reports scroll lag. Untested on real
  terminals as of writing — iTerm2 = sixel is the user's daily path.
  Known gaps: modal over kitty image not cleared; probe adds ≤300ms
  on terminals that never answer DA1.
- Graphics round 2 (user feedback): linkmap shrunk to 4 image cols
  (6-cell gutter). ChunkMap gets a pixel overlay in graphics mode —
  render_chunk_map paints per-chunk spans (min 1px, so single-line
  chunks in huge files stay visible; cell rows were ~total/45 lines
  each) + translucent viewport lens; shared plumbing factored into
  tmeld/overlay.py GraphicsOverlay mixin (gutter + chunkmap).
  GOTCHA: on_tab_hidden can arrive after the view is unmounted —
  query(), not query_one(), for overlay widgets.
- PINNED idea (user, 2026-07-07): "bmeld" — browser-based meld: `bmeld
  args` prints a clickable link, UI in browser. Design sketch agreed:
  engine stays server-side (vendored, unchanged); UI = CodeMirror
  merge / Monaco diff speaking a chunk-model + edit-ops websocket
  protocol; process stays alive for the mergetool exit-code contract.
  Transport TIERS like graphics: (1) direct localhost+token — works
  whenever the user can reach the port (their normal case, via ssh -L
  / LAN; `ssh -O forward` adds to a live ControlMaster session), (2)
  SSH-detected hint printing the forward one-liner (VS Code terminals
  auto-forward printed localhost:PORT), (3) opt-in outbound-wss relay
  with E2E key in the URL fragment (relay sees ciphertext only —
  magic-wormhole pattern). Taste today: `textual serve "tmeld a b"`.
  Related (user, 2026-07-07): awrit (github.com/chase/awrit, archived
  but forked) renders Chromium in-terminal via kitty graphics — bmeld
  served on localhost + awrit-style renderer = graphical meld without
  leaving the terminal. bmeld's server work pays off in both worlds.
- PINNED idea (user, 2026-07-07): shrink terminal font while tmeld
  runs to fit more text — doable opt-in per terminal: iTerm2 OSC 1337
  SetProfile=<name> (needs a user-made small-font profile + a restore
  profile name; crash leaves it switched), kitty remote-control
  set-font-size (needs allow_remote_control). No universal escape.
  Decided: not now; revisit as --font-profile flag.
- Release prep done: LICENSE = GPLv2 text (from upstream COPYING);
  vendor.py now stamps a provenance + GPLv2-§2(a) change-notice header
  on every vendored file and copies meld/vc/COPYING (BSD 2-clause);
  version single-sourced from tmeld.__version__ (0.3.0, keep in sync
  with tags); pyproject has classifiers/keywords/license-files +
  package-data for UPSTREAM + vc/COPYING (non-.py files DON'T ship
  without it); sdist/wheel build clean, wheel smoke-tested in a fresh
  venv; .github/workflows/ci.yml = pytest matrix 3.10-3.13 + build.
  Still to do to actually publish: pick repo host + push, `twine
  upload`, README not-affiliated note is in; courtesy note to Meld
  maintainers after publishing.
- Debian packaging done: debian/ (debhelper 13, source format "3.0
  (native)", version has NO -1 revision). KEY CONSTRAINT: Debian 13
  ships python3-textual 2.1.2, tmeld needs >=8 — so the deb BUNDLES
  pip-fetched deps under /usr/share/tmeld/lib (Architecture: all, pure
  python; build needs network) with sys.path-inserting launchers in
  debian/launchers/. Not suitable for the Debian archive as-is (noted
  in rules header). Build: dpkg-buildpackage -us -uc -b; lintian
  clean (bundled .py files get shebangs stripped + exec bits dropped —
  lintian flags '#!python' even on 0644 files). Manpages in docs/*.1.
  CI has a deb job (build + lintian + install smoke test). The .deb is
  attached to the GitHub release. dpkg's /usr/bin/tmeld is shadowed by
  ~/.local/bin/tmeld (dev symlink) on mini — intended.
- bmeld B0-B3 done (2026-07-07, single session): `bmeld a b [c]` =
  Meld in the browser. tmeld/web/{server,protocol,cli}.py — aiohttp
  (extras: tmeld[web]), token routes, WS session, chunks_payload
  serializes the SAME Comparison methods the TUI renders from; client
  = web/src (vanilla JS + CM6, esbuild bundle COMMITTED at
  tmeld/web/static/bmeld.js ~280KB, rebuild: cd web && npm run
  build; CI web-bundle job diffs for freshness). Thick client:
  browser owns buffers, 250ms debounced buffers->chunks loop; chunk
  actions are client-side ports of _replace_lines; merge-all runs
  SERVER-side (vendored Merger, parity). SVG connectors = upstream
  bezier verbatim; boundary 1px lines done via box-shadow (browser
  got the TUI's open parity item for free). scroll.py ported to
  web/src/scrollsync.js. Exit contract: Done button / --grace
  disconnect timer; abandoned merge = exit 1. Validated in headless
  Chromium (Playwright, venv-only dev dep): visual (pixel-sampled
  exact #ffa5a3 connector fills) + full interaction gauntlet (push/
  pull/merge-all/save-to-disk/exit codes/no console errors).
  GOTCHA: merge-all AFTER manually resolving a conflict re-pulls the
  other side (middle==one side looks like an unmerged change) — same
  as Meld/tmeld; workflow is merge-all first. 145 tests green.
- bmeld B4 done (2026-07-07): tabs + folder + VC in the browser.
  BmeldSession holds a dict of tabs (Comparison | DirComparison |
  VcComparison); make_comparison mirrors the TUI make_view (1 path =
  VC). Protocol grew tab ids on every scoped msg + tab_added/tree/
  vc_result; dir/VC scans run in run_in_executor. VC diff/commit/
  revert reuse tmeld/vcview.py's extracted run_diff_spec + run_vc_command
  (TUI VcView refactored onto them — 7 vc tests still green). Client
  main.js refactored to FileTab/DirTab/VcTab classes + TabBar (✕,
  active highlight, auto-hide at 1); keymap routes to the active file
  tab; readonly panes via EditorState.readOnly; tab-aware hint bar.
  GOTCHA (fixed): wrapping panes in .bm-tabcontent needs min-height:0
  +overflow:hidden or the CM editor overflows the footer (Playwright
  caught it via #done click interception). Validated headless: dir
  tree states/colors, dblclick->file tab, arrow push+save, tab switch/
  close, VC modified+status, repo-vs-working readonly diff, Commit
  button->git log, --diff startup tabs; both gauntlets + 149 tests
  green. --diff/1-3 path CLI + single-path VC.
- bmeld B5 (2026-07-08, user caught a 1px connector misalignment):
  three bugs, all in the client's geometry/layout, all browser-only.
  (1) GOTCHA: the gutter/chunkmap SVGs carried viewBox="0 0 W H" from a
  stale render; when the element grew (470->475) the default
  preserveAspectRatio="xMidYMid meet" CENTERED the drawing, shifting
  every connector down by half the difference (getScreenCTM f=2.5).
  Fixed by dropping viewBox entirely — coords are already CSS px of the
  element's own box, so user units must map 1:1.
  (2) upstream's cairo nudges (f0-0.5, f1-1 then +0.5) center a 1px
  stroke STRADDLING the line boundary; our pane boundary lines are CSS
  box-shadow *inset*, painted inside the fill. Ported verbatim = half a
  pixel off. Now FileTab.chunkEdges() insets the stroke centerlines
  (top+0.5, bottom-0.5) and snapY()s endpoints to the device-pixel grid
  (line-height 1.45*13px lands on fractional y; the browser snaps CSS
  backgrounds but antialiases SVG). Degenerate chunk (pure insert on
  the other side) keeps the 1px straddling band — no fill to align to.
  (3) BIG one, latent since B0: .bm-filegrid's implicit row was `auto`,
  which sizes to the panes' max-content (the whole document), so files
  taller than the window overflowed and were CLIPPED by
  .bm-tabcontent{overflow:hidden} instead of scrolling — cm-scroller
  never scrolled. Every test used tiny files. Fixed with
  grid-template-rows: minmax(0,1fr) + .bm-pane{min-height:0}.
  Also: .bm-chunkmap was styled as `#chunkmap` (dead selector since the
  B4 tab refactor renamed it to a class). tests/test_bmeld_static.py
  guards all four statically (dead #id/.bm-* selectors, no viewBox,
  pinned grid row, snapped+inset edges) — verified to fail pre-fix.
  154 tests green. Connector now butts the fill pixel-exactly (verified
  by sampling the junction column, scrolled and unscrolled).
- bmeld B6 (2026-07-08): footer shows `bmeld <ver> · <build>` where build =
  sha256[:8] of the served bmeld.{js,css}+index.html; the same id is appended
  to the asset URLs (?v=) and index.html is no-store, so a cached bundle can
  never be served (user asked for this to settle "am I running your fix?").
  Scrollbars: my grid fix made panes actually scroll, so a 13-15px bar
  appeared between the chunk fill and the connector. Panes now carry
  bm-pane-{first,mid,last}; first pane's bar moves to its LEFT edge via
  `direction: rtl` on .cm-scroller (that is what positions a scrollbar) +
  `flex-direction: row-reverse` (else rtl flips the line-number gutter to the
  right) + `direction: ltr` back on .cm-gutters/.cm-content. A 3-way middle
  pane sits between two gutters and can't win either way -> hides its bar
  (still wheel/key/sync scrollable). NOTE this is a DIFFERENCE from Meld, not
  a fix: Meld gives every pane a right-hand bar, but GTK draws thin
  auto-hiding overlay scrollbars so nothing interrupts the curves. Textual
  cannot do this: scrollbar_visibility is only visible/hidden and
  _arrange_scrollbars hardcodes region.split_vertical(width - size).
  Ctrl-C: one press now suffices. `async for msg in ws` never returns while a
  browser holds the socket, so AppRunner.cleanup() waited out its 60s shutdown
  timeout; run_session installs loop.add_signal_handler(SIGINT/SIGTERM ->
  session.interrupt()), tracks live sockets and closes them before cleanup,
  and TCPSite gets shutdown_timeout=1.0. exit_status() returns 130 when
  interrupted (128+SIGINT, so `git mergetool` sees a failure even if the
  merge was saved). Measured: 15s+ -> 0.10s. 159 tests green.
- Read-only parity (2026-07-08, user spotted it in real Meld):
  upstream ActionGutter._classify_change_actions -> if the TARGET pane isn't
  editable the push arrow becomes a DELETE (✕) that removes the chunk from the
  SOURCE; both panes read-only = no button; source-side "insert" chunks never
  had one. Ported to tmeld/gutter.py (_action) and bmeld (FileTab.chunkAction).
  GOTCHA: EditorState.readOnly only blocks *user* input -- pushPair's
  programmatic dispatch wrote into read-only panes; all edits go via editPane().
  Lock: Meld's readonlytoggle (changes-prevent-symbolic), visible = not
  writable, and CLICKABLE -- it unlocks the *buffer*, not the file, so the save
  then fails (Meld's tooltip says save to a new file). bmeld implements the
  toggle with a CM Compartment; TUI shows 🔒 in the pane title (not clickable:
  border_title has no click hook).
  Insert marker: Meld draws EVERY chunk with append_border([1,0,1,0]) in the
  line colour on a rect of height max(1, y1-y0)+1 -- so a zero-height chunk
  collapses to one 2px line marking where the other pane's text lands, with no
  fill (sourceview.py do_snapshot). bmeld: .bm-mark box-shadow, verified as
  exactly 2px of #a5ff4c. TUI: NOT POSSIBLE in Tier 1 -- neither rich.Style nor
  textual.style.Style exposes an underline colour (SGR 58), and there is no
  sub-cell drawing between rows. Options are a colourless underline on the row
  above, or a Tier-2 pixel overlay drawn over the pane (deferred).
  Icons: Meld's meld-change-apply-right is a solid arrow WITH A SHAFT and
  meld-change-delete a heavy cross. bmeld now draws both as inline SVG paths
  (currentColor). TUI keeps ▶ ◀ and upgrades ✕ -> ✘ (U+2718): ➡/⬅ have emoji
  presentation, so terminals render them double-width in a fixed colour --
  no single-cell heavy arrow pair exists. 164 tests green.
- Still open (bmeld): conflict 3-way FROM the VC view (spec ready,
  untested in browser), tier-3 relay transport, Playwright job in CI,
  favicon, dark theme toggle (palette plumbing exists), folder copy/
  delete row actions in browser.
- Still open: chunk boundary lines (underline approx), Phase 4 Tier-1
  text linkmap (braille/box approx — may be moot given Tier 2),
  dirdiff polish (F8 state/name filter toggles, compare-marked,
  size/mtime columns), VC polish (push/update/add/unstage actions,
  console output view, VC picker when multiple repos overlap), Phase 9
  backlog (find/replace, go-to-line, syntax, config file, scrollbar
  theming), Tier 0 256-color degrade.
