# tmeld — Meld, in your terminal

A faithful terminal port of [GNOME Meld](https://meld.app/): the same
diff engine (vendored verbatim), the same colors, the same keybindings —
over plain SSH. Two- and three-way file comparison and merging.

```
tmeld a.py b.py                  # 2-way compare/edit
tmeld local.py base.py remote.py # 3-way merge (middle = merged file)
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
| Quit | Ctrl+Q |

Gutter arrows between panes are clickable (they push the chunk); the
right-edge map is click-to-jump. On macOS terminals, set "Option as
Esc+" (iTerm2: Profiles → Keys) so Alt bindings arrive.

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

## License

GPL-2.0-or-later, like Meld — whose engine this project vendors
(`tmeld/_vendor/meld/`, from gitlab.gnome.org/GNOME/meld).
