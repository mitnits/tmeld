"""Static guards for bmeld's client assets.

These catch two classes of bug that unit tests over the Python engine cannot
see, and that only surface in a browser with a long-enough file:

* A CSS rule whose selector no longer matches anything (the chunkmap was
  styled as ``#chunkmap`` while the element carried ``class="bm-chunkmap"``,
  so its positioning rules were silently dead).
* A ``viewBox`` on the connector/chunkmap SVGs. Coordinates are already
  computed in CSS pixels of the element's own box, so a viewBox buys nothing
  -- but if its height ever lags the element's, the default
  ``preserveAspectRatio`` centres the drawing and shifts every connector by
  half the difference.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "tmeld" / "web" / "static"
CSS = (STATIC / "bmeld.css").read_text()
HTML = (STATIC / "index.html").read_text()
MAIN_JS = (Path(__file__).resolve().parent.parent / "web" / "src" / "main.js").read_text()


def _css_selectors_stripped_of_comments() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def _css_without_hex_colors() -> str:
    """`#ddd` is a colour, `#done` is a selector. Drop the hex-only tokens."""
    return re.sub(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b",
                  "", _css_selectors_stripped_of_comments())


def test_every_id_selector_in_css_exists_in_the_client():
    """An `#id` rule that matches nothing is dead styling."""
    ids = set(re.findall(r"#([A-Za-z][\w-]*)", _css_without_hex_colors()))
    referenced = set(re.findall(r'getElementById\("([^"]+)"\)', MAIN_JS))
    referenced |= set(re.findall(r'id="([^"]+)"', HTML))
    dead = sorted(ids - referenced)
    assert not dead, f"CSS #id rules matching no element: {dead}"


def test_every_bm_class_in_css_is_used_by_the_client():
    """A `.bm-*` rule that no code ever applies is dead styling."""
    classes = set(re.findall(r"\.(bm-[\w-]+)", _css_selectors_stripped_of_comments()))
    used = set(re.findall(r"bm-[\w-]+", MAIN_JS))
    # Some class names are built by interpolation, so no literal appears in the
    # source: `bm-${tag}-fill` for chunk tags, `bm-st-${state}` for tree rows.
    def interpolated(cls: str) -> bool:
        if re.match(r"bm-(insert|delete|replace|conflict|error)$", cls):
            return True
        for prefix in ("bm-pane-", "bm-mark-", "bm-st-"):
            if cls.startswith(prefix) and prefix + "${" in MAIN_JS:
                return True
        return False
    dead = sorted(c for c in classes - used if not interpolated(c))
    assert not dead, f"CSS .bm-* rules never applied by main.js: {dead}"


def test_overlay_svgs_carry_no_viewbox():
    """User units must map 1:1 to CSS px: no scale, no preserveAspectRatio.

    The connector and chunkmap SVGs are sized in CSS pixels of their own box.
    A viewBox whose height lags the element's gets centred by the default
    preserveAspectRatio, silently offsetting every connector. Icons are
    different: they are intrinsically 16x16 art and *want* a viewBox.
    """
    occurrences = [m.start() for m in re.finditer(r'setAttribute\("viewBox"', MAIN_JS)]
    icon_start = MAIN_JS.index("function iconSvg")
    icon_end = MAIN_JS.index("class FileTab")
    stray = [o for o in occurrences if not icon_start < o < icon_end]
    assert not stray, (
        f"{len(stray)} viewBox set outside iconSvg(); the overlay SVGs must "
        "map user units 1:1 to CSS pixels"
    )


def test_filegrid_row_cannot_size_to_content():
    """An `auto` grid row sizes to the panes' max-content (the whole
    document), so long files overflow and are clipped instead of scrolling."""
    grid = re.search(r"\.bm-filegrid\s*\{([^}]*)\}", _css_selectors_stripped_of_comments())
    assert grid, ".bm-filegrid rule missing"
    body = grid.group(1)
    assert re.search(r"grid-template-rows:\s*minmax\(\s*0\s*,\s*1fr\s*\)", body), (
        f".bm-filegrid must pin its row to the container, got: {body.strip()}"
    )


def test_connector_edges_are_snapped_and_inset():
    """The connector attaches to the pane fill, not half a pixel above it."""
    assert "snapY(" in MAIN_JS, "connector edges must snap to the device-pixel grid"
    assert "chunkEdges(" in MAIN_JS, "connector edges must be inset like box-shadow"


def test_no_scrollbar_sits_against_a_linkmap_gutter():
    """Panes are tagged by position so CSS can keep scrollbars off the gutters.

    The first pane's bar moves left (`direction: rtl` on the scroll container,
    with the flex row reversed so the line numbers stay inboard); a middle pane
    is between two gutters and can't win, so it hides its bar.
    """
    assert re.search(r"bm-pane-\$\{where\}|bm-pane-\$\{", MAIN_JS), (
        "panes must carry a position class for the scrollbar rules"
    )
    css = _css_selectors_stripped_of_comments()
    first = re.search(r"\.bm-pane-first \.cm-scroller\s*\{([^}]*)\}", css)
    assert first, ".bm-pane-first .cm-scroller rule missing"
    assert "direction: rtl" in first.group(1)
    # without reversing the row, rtl flips the line-number gutter to the right
    assert "flex-direction: row-reverse" in first.group(1)
    # ...and the text itself must stay left-to-right
    assert re.search(r"\.bm-pane-first \.cm-content\s*\{[^}]*direction: ltr", css)
    assert re.search(r"\.bm-pane-mid \.cm-scroller\s*\{[^}]*scrollbar-width: none", css)


def test_every_bm_class_applied_by_the_client_is_styled():
    """The reverse of the above: a class the client sets but nothing styles.

    `.bm-readonly` was applied to read-only pane titles for two releases with
    no rule anywhere, so the state was invisible.
    """
    css = set(re.findall(r"\.(bm-[\w-]+)", _css_selectors_stripped_of_comments()))
    applied = set(re.findall(r"bm-[\w-]+", MAIN_JS))
    raw = _css_selectors_stripped_of_comments()
    def is_class(name: str) -> bool:
        if name.endswith("-"):
            return False              # `bm-st-` etc: interpolation prefix
        if f"--{name}" in raw or f"--{name}" in MAIN_JS:
            return False              # a CSS custom property, not a class
        return True
    unstyled = sorted(c for c in applied - css if is_class(c))
    assert not unstyled, f"classes set by main.js with no CSS rule: {unstyled}"


def test_no_focus_ring_on_panes():
    """Meld draws none, and a 2px outline cuts across the chunk fills."""
    css = _css_selectors_stripped_of_comments()
    rule = re.search(r"\.cm-editor\.cm-focused\s*\{([^}]*)\}", css)
    assert rule, ".cm-editor.cm-focused rule missing"
    assert "outline: none" in rule.group(1), rule.group(1)


def test_current_line_tint_never_wins_over_a_chunk_fill():
    """The cursor line must not wipe the chunk background (as in the TUI).

    Both active-line rules sit at specificity (0,2,0) and must be declared
    before the chunk fills, which are also (0,2,0) and therefore win by order.
    CodeMirror's own `.cm-activeLine` is (0,1,0), so the neutralising rule has
    to be at least (0,2,0) to beat it in unfocused panes.
    """
    css = _css_selectors_stripped_of_comments()
    neutral = css.index(".cm-editor .cm-activeLine")
    tint = css.index(".cm-focused .cm-activeLine")
    fills = css.index(".cm-line.bm-insert, .cm-line.bm-delete")
    assert neutral < tint < fills, (
        "active-line rules must precede the chunk fills, and the neutralising "
        "rule must precede the tint"
    )


def test_palette_colours_are_all_consumed_by_the_client():
    """current_line_bg and selection_bg were sent but ignored for two releases."""
    from tmeld.palette import THEMES
    from tmeld.web.protocol import palette_payload

    payload = palette_payload(THEMES["meld-base"])
    scalars = [k for k in payload if k not in ("chunk", "name", "dark")]
    unused = [k for k in scalars if k not in MAIN_JS]
    assert not unused, f"palette fields the client never reads: {unused}"


def test_arrows_sit_on_the_connectors_flat_strips():
    """Meld puts the arrows in their own gutters, flush against each pane.

    The connector runs flat under those strips, so the chunk fill reaches the
    pane edge and the buttons never overlap a curve.
    """
    assert "const ARROW_STRIP" in MAIN_JS
    assert "const GUTTER_W" in MAIN_JS
    # flat run out to the strip, bezier only across the middle
    assert re.search(r"M -0\.5 \$\{f0\} L \$\{a\} \$\{f0\}", MAIN_JS), \
        "connector must start with a flat run to the arrow strip"
    assert "W - ARROW_STRIP" in MAIN_JS, "right arrow must snap to its strip"
