# tmeld — Meld, in your terminal

A faithful terminal port of [GNOME Meld](https://meld.app/): the same
diff engine (vendored verbatim), the same colors, the same keybindings —
over plain SSH. Two- and three-way file comparison and merging.

```
tmeld a.py b.py                  # 2-way compare/edit
tmeld local.py base.py remote.py # 3-way merge (middle = merged file)
tmeld dirA dirB                  # folder comparison (Enter opens files)
tmeld .                          # version-control view (git, hg, svn, ...)
tmeld a b --diff c d --diff x y  # extra comparison tabs (like meld --diff)
tmeld --theme meld-dark a b      # Meld's dark scheme
```

## Keys (Meld's own)

| Action | Keys |
|---|---|
| Next / previous change | Alt+Down, Ctrl+D / Alt+Up, Ctrl+E |
| Push chunk left / right | Alt+Left / Alt+Right |
| Pull chunk from left / right | Alt+Shift+Right / Alt+Shift+Left |
| Copy chunk above / below | Alt+[ Alt+] / Alt+; Alt+' |
| Delete chunk | Alt+Delete |
| Next / previous conflict (3-way) | Ctrl+K / Ctrl+J |
| Merge all non-conflicting (3-way) | Alt+M |
| Save | Ctrl+S |
| Next / previous pane | Alt+PgDn / Alt+PgUp |
| Close tab | Ctrl+W (twice if unsaved) |
| Next / previous tab | Ctrl+Alt+PgDn / Ctrl+Alt+PgUp |
| Quit | Ctrl+Q |

Gutter arrows between panes are clickable (they push the chunk); the
right-edge map is click-to-jump. On macOS terminals, set "Option as
Esc+" (iTerm2: Profiles → Keys) so Alt bindings arrive.

In folder comparisons: Enter compares the file under the cursor (or
expands/collapses a folder), Alt+Left/Right copy the row to that
neighbor pane, Delete deletes it (press twice to confirm), Alt+Down/Up
jump between differing rows, and Alt+PgDn/PgUp move the focused pane
(the column copy/delete act on). Meld's default filename filters
(backups, VCS metadata, binaries, OS cruft) apply.

In the version-control view (`tmeld .` anywhere in a working copy):
Enter compares a changed file against the repository (repo side
read-only); a conflicted file opens as a remote/merge/local 3-way whose
middle-pane saves resolve the working file. `c` commits (Meld's Ctrl+M
IS Enter in a terminal, so the mnemonic moved), `r` reverts, Delete
deletes, Ctrl+R/F5 rescans.

## Pixel linkmap (Tier 2)

On terminals with graphics support, the gutter between panes widens
and Meld's anti-aliased connector curves are drawn there as real
pixels — kitty graphics protocol (kitty, WezTerm, Ghostty) or sixel
(iTerm2, recent VTE), auto-detected at startup. `--graphics
none|sixel|kitty` overrides the probe. Everything else stays
cell-based; without graphics you keep the compact 3-column gutter.

## git mergetool

tmeld follows Meld's convention: `tmeld $LOCAL $MERGED $REMOTE`, the
middle pane is the merged file. The exit code is 0 only if the middle
pane was saved, so git can trust it:

```ini
[merge]
    tool = tmeld
[mergetool "tmeld"]
    cmd = tmeld "$LOCAL" "$MERGED" "$REMOTE"
    trustExitCode = true
```

`tmeld -o OUTPUT local base remote` redirects middle-pane saves to
OUTPUT (like `meld -o`) if you'd rather keep the base file untouched.

For diffs: `git difftool -x tmeld` or

```ini
[diff]
    tool = tmeld
[difftool "tmeld"]
    cmd = tmeld "$LOCAL" "$REMOTE"
```

## License and provenance

GPL-2.0-or-later (see LICENSE), like Meld — whose engine this project
vendors byte-for-byte apart from mechanical import rewrites
(`tmeld/_vendor/meld/`, pinned commit recorded in
`tmeld/_vendor/UPSTREAM`, rewrites applied by `maint/vendor.py`). The
vendored `vc/` package is BSD 2-clause (its COPYING ships alongside).

tmeld is an independent project, not affiliated with or endorsed by
the Meld or GNOME projects. All credit for the diff engine and the
design this port imitates goes to [Meld](https://meld.app/) and its
maintainers.
