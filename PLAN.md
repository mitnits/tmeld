# tmeld — Meld, in your terminal

A faithful port of Meld's UI and behavior to a terminal application, usable over
plain SSH with no X11 forwarding. Target: a Meld user's muscle memory, color
memory, and workflow transfer with near-zero friction.

---

## 1. Goal and definition of "perfect clone"

"Perfect" is defined as **semantic + muscle-memory + color parity**, not pixel
parity (a terminal cannot control fonts or sub-cell layout). Concretely:

| Aspect                                    | Parity target |
|-------------------------------------------|---------------|
| Diff chunk boundaries & classification    | **Identical** (same code as Meld) |
| Inline (intra-line) highlights            | **Identical** (same code) |
| Chunk colors                              | **Exact hex values** from Meld's style schemes (truecolor) |
| Keybindings (Alt+↑/↓ nav, Alt+←/→ push…)  | **Identical**, with remap escape hatch |
| Editable panes, live re-diff              | Yes |
| Proportional chunk-aligned sync scrolling | Yes |
| Overview/change map at pane edge          | Yes (block glyphs) |
| Curved linkmap connectors between panes   | Tiered: glyph approximation → real pixels (sixel/Kitty) |
| 3-way merge, folder compare, VC view      | Yes |
| Fonts                                     | Out of scope — terminal-controlled |

## 2. Why this works over SSH (and X11 doesn't)

X11 forwarding is synchronous and round-trip-bound: every expose/configure event
crosses the wire. A terminal app is a **one-way byte stream** of escape
sequences; latency adds keystroke echo delay only, and screen updates arrive as
a burst. Sixel and the Kitty graphics protocol are also in-band escape
sequences — "real graphics" that ride the same SSH channel with no extra setup.
Bonus: the app runs *on the remote host*, so it reads remote files natively and
survives inside tmux.

## 3. Core architecture decisions

### D1. Reuse Meld's engine verbatim (the fidelity keystone)
Meld's diff/merge core is UI-independent Python. We vendor from upstream
(gitlab.gnome.org/GNOME/meld):

- `meld/matchers/myers.py` — `MyersSequenceMatcher`, `InlineMyersSequenceMatcher`,
  `SyncPointMyersSequenceMatcher`
- `meld/matchers/diffutil.py` — `Differ`: composes two pairwise diffs into
  2-/3-way change chunks (the chunk model *is* Meld's behavior)
- `meld/matchers/merge.py` — `AutoMergeDiffer` (3-way auto-merge)
- `meld/matchers/helpers.py` — cached/background matching (strip GLib idle
  plumbing, replace with asyncio + process pool)
- `meld/vc/` — version-control backends (git, hg, svn…); they shell out to the
  CLI tools and are nearly UI-free
- Filter logic (filename filters, text/regex ignore filters)

Same code ⇒ identical chunking, identical inline highlights, identical merge
behavior. No re-implementation drift, ever.

**License consequence:** vendoring GPL-2 code makes this project GPL-2. Accepted.

### D2. Python + Textual for the UI layer
- Truecolor, mouse (click/drag/wheel), resize events, flicker-free rendering.
- `TextArea` widget: **editable**, tree-sitter syntax highlighting, line API.
- Async-first — clean fit for debounced background re-diffing.
- `pytest-textual-snapshot` for visual regression tests.

Rejected alternatives:
- **Notcurses**: best-in-class terminal graphics, but C-centric with weak Python
  bindings; would force abandoning D1. Instead we borrow its *idea* (bitmap
  planes) for the Tier-2 linkmap only.
- **Rust/ratatui**: excellent TUI story, but requires re-implementing the
  matchers — guaranteed behavioral drift from real Meld. Wrong trade.

### D3. Tiered terminal capability model
Detected at startup (terminfo + `COLORTERM` + DA1 sixel bit + Kitty APC query),
overridable by flag/config:

- **Tier 0** — 256-color, no mouse: functional degraded mode (nearest-color
  palette, keyboard only).
- **Tier 1 (primary target)** — truecolor + mouse + Unicode: exact colors,
  box-drawing/braille linkmap, block-glyph overview map.
- **Tier 2** — sixel or Kitty graphics: pixel-rendered anti-aliased linkmap
  curves in the center gutter; everything else stays cell-based.

## 4. Layered architecture

```
┌────────────────────────────────────────────────────────┐
│ App shell (tabs, command palette, prefs, session)      │
├──────────────┬──────────────┬──────────────────────────┤
│ FileDiffView │ DirDiffView  │ VcView                   │   Textual screens
├──────────────┴──────────────┴──────────────────────────┤
│ Widgets: DiffPane (editable buffer + chunk styling),   │
│ LinkMap (tiered renderer), Gutter (actions), ChunkMap  │
├────────────────────────────────────────────────────────┤
│ ComparisonModel: buffers, chunk list, dirty/re-diff    │
│ scheduler (debounce → process-pool matcher → apply)    │
├────────────────────────────────────────────────────────┤
│ VENDORED MELD CORE: matchers/, vc/, filters            │
└────────────────────────────────────────────────────────┘
```

Target layout (2-way file diff, Tier 1):

```
┌ a/foo.py ──────────────────┬──┬ b/foo.py ──────────────────┬─┐
│  1 def main():             │  │  1 def main():             │▒│
│  2     x = 1               │◀─│  2     x = 2               │█│  ← chunk map
│  3     print(x)            │  │  3     print(x)            │▒│
│  4                         │─▶│  4     log(x)              │▒│
└────────────────────────────┴──┴────────────────────────────┴─┘
 [Alt+↓] next chunk  [Alt+←/→] push  [Ctrl+S] save
```

## 5. Phase plan

Each phase ends runnable and tested. Sizes: S ≈ days, M ≈ 1–2 weeks, L ≈ 2–4
weeks of focused work.

### Phase 0 — Parity spec extraction (S)
1. Clone upstream Meld; pin a reference tag (latest 3.2x stable).
2. Extract the **exact color palette** from `data/styles/meld.style-scheme.xml.in`
   (meld-base + meld-dark) and `meld.css`: insert/delete/change/conflict
   backgrounds, inline-highlight variants, current-chunk emphasis, dirdiff state
   colors. Record as a theme table.
3. Catalog the **keymap** (from GTK resource/help): chunk nav, push/pull, copy
   above/below, file nav, save, undo. Note terminal conflicts (see Risks).
4. Write a behavior spec for the non-obvious bits: proportional chunk-aligned
   scrolling, current-chunk focus rules, "changed but identical after filters"
   rendering.
5. Repo scaffolding: GPL-2 license, `pyproject.toml`, CI, vendoring script that
   records upstream commit hash.

**Exit:** `PARITY.md` (palette + keymap + behavior spec) checked in.

### Phase 1 — Engine: vendor and headless-ify Meld core (M)
1. Vendor `matchers/` and filter logic; remove GTK/GLib imports (mechanical:
   idle-callback plumbing → asyncio; GLib types → stdlib).
2. Define `ComparisonModel`: N buffers + ordered chunk list
   (`tag ∈ {replace, insert, delete, conflict}`, per-pane line ranges) + inline
   ranges. Pure, renderer-free.
3. Background matcher: process pool + debounce, mirroring Meld's
   `CachedSequenceMatcher` behavior.
4. **Golden tests:** fixture file pairs/triples; assert our chunks == chunks
   captured from real Meld on the same inputs (capture via a small GTK-side
   dump script run once on a desktop machine).

**Exit:** `tmeld-dump a b` prints Meld-identical chunk JSON for any two files.

### Phase 2 — Two-pane read-only viewer, the MVP (M)
1. App shell + `FileDiffView` with two `DiffPane`s. Spike early: per-line
   background styling in Textual's `TextArea`; if it fights us, fall back to a
   custom Line-API widget (contained risk — decide in week 1).
2. Chunk backgrounds + inline highlights with exact Meld hex colors; line
   numbers; current-chunk emphasis.
3. **Meld-style sync scrolling**: proportional interpolation so corresponding
   chunks align mid-viewport (this is the feel-defining feature — port the
   logic from `filediff.py`'s scroll handling, don't approximate it).
4. Keyboard nav: Alt+↑/↓ prev/next chunk, PgUp/PgDn, wheel scrolling.
5. Chunk map (right edge): block-glyph column showing change positions.
6. Snapshot tests for all of the above.

**Exit:** `tmeld a b` over SSH is a pleasant read-only Meld.

### Phase 3 — Editing and chunk actions (L)
1. Editable panes; edits mark buffer dirty and schedule debounced re-diff;
   chunk/scroll state preserved across re-diff (match Meld's stability).
2. Undo/redo per pane; save with encoding + newline preservation; unsaved-marker
   in pane title; quit protection.
3. Chunk actions with Meld bindings: push left/right (Alt+←/→), delete chunk,
   copy-above/copy-below. Gutter arrows clickable with mouse.
4. Action semantics ported from Meld (`filediff.py` chunk ops), including
   merge-into-middle for 3-way later.

**Exit:** real merge work is doable end-to-end; edits + pushes + save round-trip.

### Phase 4 — Linkmap and center gutter (M)
1. Center gutter column between panes: per-chunk action glyphs (→, ←, ×) at
   chunk anchors, clickable.
2. Tier-1 connector rendering: box-drawing/braille lines linking chunk spans
   across panes, colored by chunk type — recognizably Meld's linkmap.
3. Hover/current-chunk emphasis in the gutter.

**Exit:** the between-panes region reads like Meld's, in pure cells.

### Phase 5 — Three-way merge (M)
1. Wire `Differ`'s 3-way chunk model + `AutoMergeDiffer` into a 3-pane layout
   (ours | base/merged | theirs), two linkmap gutters.
2. Conflict chunk coloring per Meld palette; push-to-middle actions;
   "merge all non-conflicting" command.
3. `git mergetool` contract: correct exit codes, `$MERGED` handling, docs for
   `.gitconfig` stanzas.

**Exit:** drop-in `git mergetool` replacement over SSH.

### Phase 6 — Folder comparison (M)
1. `DirDiffView`: side-by-side tree with Meld's state colors (same / modified /
   new / missing), recursive scan off the UI thread.
2. Filename filters + text filters (vendored Meld filter logic, same defaults).
3. Enter on a file row opens a file comparison tab; copy left/right, delete;
   "compare selected".

**Exit:** `tmeld dirA dirB` matches Meld folder-compare workflow.

### Phase 7 — Version-control view (M)
1. Vendor `meld/vc/` backends; `VcView` tree of working-copy status (git first,
   hg/svn after).
2. Diff-against-HEAD opens file comparison; basic commit flow; revert/stage
   where the backend supports it.

**Exit:** `tmeld .` in a repo ≈ Meld's VC view.

### Phase 8 — Tier 2 graphics: pixel linkmap (M, parallelizable after Ph4)
1. Capability probe: DA1 for sixel, APC query for Kitty protocol, iTerm2 OSC;
   honor overrides. Detect tmux and use passthrough where configured.
2. Render the center-gutter connectors as an anti-aliased bitmap (bezier curves,
   Meld colors) sized to the gutter's cell rectangle; repaint on scroll/resize.
   Cell renderer remains the always-available fallback.
3. Terminal support matrix in docs (macOS: iTerm2 ✓ sixel+images, WezTerm ✓,
   kitty ✓, Ghostty ✓ Kitty protocol, Terminal.app ✗; mosh ✗ sixel;
   tmux: needs ≥3.4 with sixel build or passthrough).

**Exit:** on iTerm2/WezTerm/kitty/Ghostty, the linkmap shows real curves.

### Phase 9 — Polish and daily-driver parity (M, ongoing)
1. Syntax highlighting in panes (tree-sitter), themed to approximate
   GtkSourceView's default scheme so colors-on-colors read like Meld.
2. Find / find-and-replace; go-to-line.
3. OSC 52 clipboard integration (copy works across SSH!).
4. Config file mirroring Meld's settings names (wrap-mode, show-line-numbers,
   ignore-blank-lines, text filters…); `meld-base` and `meld-dark` themes.
5. Keybinding remap layer + documented terminal setup (iTerm2 "Option as
   Meta/Esc+", tmux `set -g xterm-keys on`, etc.).
6. Performance pass on large files: virtualized rendering only for visible
   lines; matcher already off-thread.

## 6. Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Textual `TextArea` can't do per-line diff backgrounds cleanly | Blocks Ph2 | Spike in first week of Ph2; fallback is a custom Line-API pane widget (more work, fully within Textual's model) |
| Alt+arrow keys swallowed by terminal/macOS | Muscle-memory parity | Kitty keyboard protocol where available; documented terminal config; remap layer as escape hatch |
| Vendored GLib-isms deeper than expected in `matchers/helpers.py` | Ph1 slip | Only the scheduling wrapper touches GLib; matchers themselves are pure Python — worst case rewrite the wrapper (small) |
| Sixel/Kitty under tmux/mosh | Tier 2 only | Tier 1 is the contract; Tier 2 is enhancement; document the matrix |
| Python perf on huge files | UX | Same engine Meld ships — same ballpark; virtualize rendering; process-pool matching already async |
| Scroll-sync feel subtly "off" | The clone feels wrong | Port Meld's actual interpolation math from `filediff.py`; add a side-by-side video comparison to acceptance checklist |

## 7. Non-goals (explicit)

- Pixel-identical fonts/spacing (terminal-owned).
- GTK preferences dialog clone — config file + in-app palette instead.
- Meld's Windows/macOS *desktop* packaging; this ships as a Python package
  (`pipx install tmeld`) run on whatever host holds the files.
- Synchronized-comparison "sync points" UI — defer past Phase 9 (engine support
  is already vendored via `SyncPointMyersSequenceMatcher`).

## 8. Suggested first milestone

Phases 0–2: a read-only, exact-color, Meld-scrolling two-pane differ over SSH.
That alone replaces `vimdiff`/pager workflows and validates every risky
assumption (engine vendoring, TextArea styling, scroll feel) before the larger
editing investment in Phase 3.
