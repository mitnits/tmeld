# PARITY.md — extracted Meld behavior spec

Source of truth: upstream Meld at commit
`9c4be506a26dd0a0742887ee4d6e28208812c792` (describes as 3.24.0-56), cloned in
`upstream/` (git-ignored). All values below are extracted from that tree, with
file:line references so they can be re-verified after upstream bumps.

> **Open question for the user:** these are the *current master* palettes.
> If your color memory is from an older installed Meld (3.20/3.22 used
> GTK-CSS-defined colors, slightly different values), tell us the version you
> run daily and we re-extract from that tag instead.

## 1. Color palette

From `upstream/data/styles/meld-base.style-scheme.xml.in` (theme `meld-base`,
parent scheme `classic`) and `meld-dark.style-scheme.xml.in` (parent
`solarized-dark`). `line-background` is the whole-line chunk background;
`background` is used for e.g. the linkmap/gutter fills; `foreground` doubles
as the tree-state text color (see §2).

### meld-base (light)

| Style key                    | foreground | background | line-background |
|------------------------------|------------|------------|-----------------|
| meld:insert                  | #008800    | #d0ffa3    | #a5ff4c         |
| meld:replace                 | #0044dd    | #bdddff    | #65b2ff         |
| meld:conflict                | #ff0000    | #ffa5a3    | #ff4f4c         |
| meld:delete                  | #880000    | #ffffff    | #cccccc         |
| meld:error                   | #faad3d    | #fce94f    | #fade0a         |
| meld:inline                  | —          | #8ac2ff    | —               |
| meld:current-line-highlight  | #333333    | rgba(255,255,0,0.25) | —      |
| meld:unknown-text            | #888888    | —          | —               |
| meld:syncpoint-outline       | #555555    | —          | —               |
| meld:current-chunk-highlight | —          | rgba(255,255,255,0.5) | —     |
| meld:overscroll              | —          | rgba(50,50,50,0.1) | —        |
| meld:dimmed                  | #999999    | —          | —               |
| map-overlay                  | —          | rgba(100,100,100,0.4) | —     |

### meld-dark

| Style key                    | foreground | background | line-background |
|------------------------------|------------|------------|-----------------|
| meld:insert                  | #4e6206    | #123806    | #245515         |
| meld:replace                 | #1d59d6    | #003266    | #0053a6         |
| meld:conflict                | #ff0000    | #7a2a28    | #ac3b39         |
| meld:delete                  | #a40000    | #ffffff*   | #cccccc*        |
| meld:error                   | #faad3d    | #fce94f    | #fdf8cd         |
| meld:inline                  | —          | #24527e    | —               |
| meld:current-line-highlight  | #eeeeee    | rgba(17,17,0,0.25) | —        |
| meld:unknown-text            | #aaaaaa    | —          | —               |
| meld:syncpoint-outline       | #bbbbbb    | —          | —               |
| meld:current-chunk-highlight | —          | rgba(255,255,255,0.06) | —    |
| meld:overscroll              | —          | rgba(255,255,255,0.1) | —     |
| meld:dimmed                  | #999999    | —          | —               |
| map-overlay                  | —          | rgba(200,200,200,0.4) | —     |

\* upstream FIXME: dark delete bg should be theme bg color — we should do the
right thing here rather than clone the bug (use terminal/theme bg, shaded).

**TUI note on alpha:** rgba entries composite over the buffer background in
GTK. Terminals have no alpha; we pre-composite: e.g. current-line-highlight on
white = #ffffbf-ish. Compute at theme-load time against the active background,
never hardcode the composited value.

Syntax highlighting sits under these (GtkSourceView `classic` /
`solarized-dark` parent schemes); Phase 9 maps tree-sitter scopes to those
parents' colors.

## 2. Tree state styling (folder compare + VC view)

From `upstream/meld/tree.py:95-111`. Foregrounds are the §1 style
foregrounds; format is (fg, style, weight, strikethrough):

| State      | fg source     | italic | bold | strike |
|------------|---------------|--------|------|--------|
| IGNORED    | unknown-text  |        |      |        |
| NONE       | unknown-text  |        |      |        |
| NORMAL     | default       |        |      |        |
| NOCHANGE   | default       | ✓      |      |        |
| ERROR      | error         |        | ✓    |        |
| EMPTY      | unknown-text  | ✓      |      |        |
| NEW        | insert        |        | ✓    |        |
| MODIFIED   | replace       |        | ✓    |        |
| RENAMED    | replace       |        |      |        |
| CONFLICT   | conflict      |        | ✓    |        |
| REMOVED    | delete        |        | ✓    | ✓      |
| MISSING    | delete        |        | ✓    | ✓      |
| NONEXIST   | unknown-text  |        |      | ✓      |
| SPINNER    | default       | ✓      |      |        |

## 3. Keymap

Complete map from `upstream/meld/accelerators.py` (`<Primary>` = Ctrl in
terminals). Multiple bindings listed comma-separated.

### Global / window
| Action | Keys |
|---|---|
| Quit | Ctrl+Q |
| Help | F1 |
| Preferences | Ctrl+, |
| Close tab | Ctrl+W |
| New tab | Ctrl+N |
| Gear menu | F10 |
| Fullscreen | F11 |
| Stop | Escape |

### All comparison views
| Action | Keys |
|---|---|
| Find / next / prev | Ctrl+F / Ctrl+G, F3 / Ctrl+Shift+G, Shift+F3 |
| Find & replace | Ctrl+H |
| Go to line | Ctrl+I |
| Next / previous change | **Alt+Down, Ctrl+D** / **Alt+Up, Ctrl+E** |
| Next / previous pane | Alt+PgDn / Alt+PgUp |
| Refresh | Ctrl+R, F5 |
| Save / save-as / save-all | Ctrl+S / Ctrl+Shift+S / Ctrl+Shift+L |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z |
| Filter menu (vc/folder/text) | F8 |
| Open external | Ctrl+Shift+O |

### File comparison
| Action | Keys |
|---|---|
| Push chunk left / right | **Alt+Left / Alt+Right** |
| Pull chunk from left / right | Alt+Shift+Right / Alt+Shift+Left |
| Copy chunk above: left / right | Alt+[ / Alt+] |
| Copy chunk below: left / right | Alt+; / Alt+' |
| Delete chunk | Alt+Delete |
| Next / previous conflict | Ctrl+K / Ctrl+J |
| Overview map toggle | F9 |
| Swap panes (2-way) | Alt+\ |

### Folder comparison
| Action | Keys |
|---|---|
| Compare selected | Enter |
| Copy left / right | Alt+Left / Alt+Right |
| Delete | Delete |

### Version control
| Action | Keys |
|---|---|
| Commit | Ctrl+M |
| Console toggle | F9 |

**Terminal hazards:** Alt+arrows (macOS word-jump / terminal escape timing),
Ctrl+W (some terminals), F10/F11 (window managers), Alt+; and Alt+' (Option
dead-keys on macOS unless "Option as Esc+" is set). All must be remappable;
ship an iTerm2 setup note.

## 4. Sync-scroll algorithm (the "Meld feel")

Port exactly; do not approximate. Sources:
`upstream/meld/misc.py:401` (`calc_syncpoint`) and
`upstream/meld/filediff.py:2441` (`_sync_vscroll`).

1. **Syncpoint** ∈ [0,1] per viewport: normally 0.5 (viewport middle); scales
   linearly to 0.0 within the first half-screen of the document and to 1.0
   within the last half-screen (so unequal-length files pin correctly at both
   ends).
2. Master pane: compute fractional buffer line at the syncpoint (fractional ⇒
   smooth, non-jerky sync).
3. For each influenced pane, walk the chunk list between the two panes: find
   the chunk **or inter-chunk gap** containing the target line, then linearly
   interpolate the fraction through that interval to the other pane's
   interval: `other = obegin + frac * (oend - obegin)`.
4. 3-way influence map: all influence flows through the middle pane —
   `((1,2), (0,2), (1,0))` for masters left/middle/right.
5. Overscroll margin blends in over the last half page (`syncpoint > 0.5`).
6. Result clamped and floored to whole pixels (for us: whole cells; consider
   fractional-cell smoothing later).

TUI mapping: "pixel" y-coordinates become buffer-line floats directly (no
wrapped-line height lookup needed in phase 2 if we start without soft wrap;
`get_line_yrange` generalizes when wrap arrives).

## 5. Engine inventory (to vendor in Phase 1)

| Upstream file | Contents | GTK/GLib coupling |
|---|---|---|
| meld/matchers/myers.py | Myers + inline + syncpoint matchers | none/minimal |
| meld/matchers/diffutil.py | `Differ`: 2×pairwise → 3-way chunks | check signals |
| meld/matchers/merge.py | `AutoMergeDiffer`, `Merger` | minimal |
| meld/matchers/helpers.py | `CachedSequenceMatcher`, process pool | GLib idle → asyncio |
| meld/filters.py | filename/text filter model | minimal |
| meld/vc/* | git/hg/svn/… backends | check dirs/GLib usage |
| meld/misc.py (parts) | `calc_syncpoint`, small helpers | mixed — cherry-pick |

Vendoring rules: copy verbatim into `tmeld/_vendor/meld/`, record upstream
commit in `_vendor/UPSTREAM`, patch minimally with clearly-marked shims, keep
a script (`maint/vendor.py`) that re-copies and reports drift.

## 6. Still to capture (Phase 0 leftovers)

- [ ] Chunk-action gutter behavior: hover states, click targets, what
      Shift/Ctrl modifiers do to the gutter arrows (push vs delete vs copy).
- [ ] Current-chunk focus rules (which chunk is "current" after nav/edit/click).
- [ ] Text-filter interaction with chunk display ("dimmed" chunks that are
      identical after filters).
- [ ] Overview map (sourcemap) geometry: chunk → pixel mapping, drag behavior.
- [ ] gsettings keys worth mirroring in our config (wrap, line numbers,
      ignore-blank-lines, custom-font, tab-width, draw-spaces…) from
      `data/org.gnome.Meld.gschema.xml`.
