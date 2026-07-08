"""Pixel linkmap: Meld's anti-aliased connector curves (Tier 2).

Geometry is a port of upstream meld/linkmap.py LinkMap.draw: for each
pair chunk, a closed shape whose top edge is a cubic bezier from the
left pane's chunk start to the right pane's, bottom edge the reverse
bezier, with control points at mid-width (x_steps = [-0.5, W/2,
W+0.5]); endpoints are nudged ±0.5px and the bottom edge sits on "the
last pixel of the previous line" (f1 - 1 unless the chunk is empty).
Fill uses the pale chunk fill, stroke the saturated line color, and
the current chunk gets Meld's white emphasis overlay — the same
palette entries the GTK renderer uses.

The rasterizer is pure Python: each connector is x-monotone with one
vertical span per pixel column, so per-column float [top, bottom]
edges give exact vertical coverage (anti-aliasing) without any
imaging dependency. Output is straight-alpha RGBA bytes.

Encoders:
  * kitty graphics protocol (preferred): f=32 zlib-compressed RGBA
    with a stable image id — retransmitting the same id replaces the
    image atomically, so scrolling repaints don't flicker.
  * sixel: alpha is composited over the page background at raster
    time (quantized to SIXEL_ALPHA_LEVELS), palette built from the
    colors actually used, run-length encoded.
"""

import base64
import zlib
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from tmeld.palette import Theme, blend

# Bezier polyline sampling; connectors are shallow, 32 segments is smooth
BEZIER_SEGMENTS = 32

# Sixel has no alpha channel: quantize edge coverage to this many levels
SIXEL_ALPHA_LEVELS = 8

RGB = Tuple[int, int, int]


class Connector(NamedTuple):
    """One pair-chunk connector, in image-local pixel coordinates."""

    tag: str
    f0: float  # left top y
    f1: float  # left bottom y
    t0: float  # right top y
    t1: float  # right bottom y
    emphasized: bool = False


INSERT_MARKER_PX = 2


def connectors_for_chunks(
    pair_chunks: Iterable,
    scroll_f_px: float,
    scroll_t_px: float,
    line_height_px: int,
    current_chunk_starts: frozenset = frozenset(),
) -> List[Connector]:
    """Map pair-oriented chunks to pixel-space connectors.

    scroll_*_px are the panes' vertical scroll offsets in pixels;
    current_chunk_starts is the set of chunk (start_a, start_b) pairs
    to emphasize (the cursor chunk, upstream linkmap.py:129).
    """
    result = []
    for chunk in pair_chunks:
        f0 = chunk.start_a * line_height_px - scroll_f_px
        f1 = chunk.end_a * line_height_px - scroll_f_px
        t0 = chunk.start_b * line_height_px - scroll_t_px
        t1 = chunk.end_b * line_height_px - scroll_t_px
        # "We want the last pixel of the previous line" (linkmap.py:95)
        f1 = f1 if f1 == f0 else f1 - 1
        t1 = t1 if t1 == t0 else t1 - 1
        # A zero-height chunk terminates on the pane's insert marker, which is
        # INSERT_MARKER_PX tall and starts at the line boundary. render_connectors
        # nudges the edges by ∓0.5 and strokes 1px bands, so centring the
        # degenerate band half a marker below the boundary makes it cover
        # exactly [y, y + INSERT_MARKER_PX] -- the marker's own rows.
        if f1 == f0:
            f0 = f1 = f0 + INSERT_MARKER_PX / 2.0
        if t1 == t0:
            t0 = t1 = t0 + INSERT_MARKER_PX / 2.0
        result.append(Connector(
            chunk.tag, f0, f1, t0, t1,
            emphasized=(chunk.start_a, chunk.start_b) in current_chunk_starts,
        ))
    return result


def _edge_curve(y_from: float, y_to: float, width: float) -> List[Tuple[float, float]]:
    """Sampled cubic bezier from (-0.5, y_from) to (width+0.5, y_to)
    with control points at mid-width (upstream x_steps)."""
    x0, x1, x2, x3 = -0.5, width / 2.0, width / 2.0, width + 0.5
    points = []
    for i in range(BEZIER_SEGMENTS + 1):
        t = i / BEZIER_SEGMENTS
        mt = 1.0 - t
        a = mt * mt * mt
        b = 3.0 * mt * mt * t
        c = 3.0 * mt * t * t
        d = t * t * t
        x = a * x0 + b * x1 + c * x2 + d * x3
        y = (a + b) * y_from + (c + d) * y_to
        points.append((x, y))
    return points


def _curve_column_ys(y_from: float, y_to: float, width_px: int) -> List[float]:
    """y of the edge curve at each pixel column center (x-monotone, so
    linear interpolation between polyline samples is exact enough)."""
    curve = _edge_curve(y_from, y_to, float(width_px))
    ys = []
    seg = 0
    for col in range(width_px):
        x = col + 0.5
        while seg < len(curve) - 2 and curve[seg + 1][0] < x:
            seg += 1
        (xa, ya), (xb, yb) = curve[seg], curve[seg + 1]
        frac = 0.0 if xb == xa else (x - xa) / (xb - xa)
        ys.append(ya + (yb - ya) * frac)
    return ys


def _hex_rgb(color: str) -> RGB:
    value = int(color.lstrip("#"), 16)
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


class _Canvas:
    """Straight-alpha RGBA canvas with column-span painting."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.data = bytearray(width * height * 4)

    def blend_pixel(self, x: int, y: int, rgb: RGB, alpha: float) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height) or alpha <= 0:
            return
        i = (y * self.width + x) * 4
        data = self.data
        old_a = data[i + 3] / 255.0
        out_a = alpha + old_a * (1.0 - alpha)
        if out_a <= 0:
            return
        for ch in range(3):
            old = data[i + ch]
            data[i + ch] = round(
                (rgb[ch] * alpha + old * old_a * (1.0 - alpha)) / out_a
            )
        data[i + 3] = round(out_a * 255)

    def fill_span(
        self, x: int, top: float, bottom: float, rgb: RGB,
        alpha: float = 1.0,
    ) -> None:
        """Paint the vertical span [top, bottom] with edge coverage,
        scaled by alpha (for translucent overlays)."""
        if bottom < top:
            return
        first, last = int(top // 1), int(bottom // 1)
        for y in range(max(0, first), min(self.height, last + 1)):
            cover = min(bottom, y + 1.0) - max(top, float(y))
            self.blend_pixel(x, y, rgb, max(0.0, min(1.0, cover)) * alpha)


class GutterArrow(NamedTuple):
    """A push arrow or a delete cross, drawn on top of the connector fill.

    Meld's ActionGutter paints the chunk background behind its button, so the
    icon sits on the chunk's own colour rather than in a reserved column. Since
    we draw the linkmap as pixels anyway, the icons can live inside it.
    """

    kind: str        # "push" | "delete"
    on_right: bool   # right-hand edge (the ◀ column), else the left one
    top: float       # top of the icon box, in image pixels
    rgb: RGB


def draw_arrow(
    canvas: "_Canvas", x0: int, top: float, w: int, h: int,
    rgb: RGB, pointing_right: bool,
) -> None:
    """Meld's meld-change-apply icon: a solid arrow with a shaft.

    Proportions traced from the 16x16 original: the shaft is a third of the
    height and the head half the width, on a box slightly wider than it is tall.
    """
    shaft = max(3.0, h * 0.36)
    head_w = max(4, round(w * 0.5))
    cy = top + h / 2.0
    for i in range(w):
        if i < w - head_w:
            span = (cy - shaft / 2.0, cy + shaft / 2.0)
        else:
            t = (i - (w - head_w) + 0.5) / head_w
            half = (h / 2.0) * (1.0 - t)
            if half < 0.4:
                continue
            span = (cy - half, cy + half)
        x = x0 + (i if pointing_right else w - 1 - i)
        canvas.fill_span(x, span[0], span[1], rgb)


def draw_delete(
    canvas: "_Canvas", x0: int, top: float, w: int, h: int, rgb: RGB,
) -> None:
    """Meld's meld-change-delete icon: a heavy cross."""
    thick = max(2.5, min(w, h) / 3.5)
    for i in range(w):
        fx = i + 0.5
        y1 = top + fx * h / w
        y2 = top + h - fx * h / w
        canvas.fill_span(x0 + i, y1 - thick / 2.0, y1 + thick / 2.0, rgb)
        canvas.fill_span(x0 + i, y2 - thick / 2.0, y2 + thick / 2.0, rgb)


def render_connectors(
    connectors: Iterable[Connector],
    width_px: int,
    height_px: int,
    theme: Theme,
    background: Optional[str] = None,
    arrows: Iterable[GutterArrow] = (),
    arrow_size: Tuple[int, int] = (8, 12),
    arrow_margin: int = 2,
) -> bytearray:
    """Rasterize connectors (and any gutter icons) to RGBA (straight alpha).

    With background=None the image is transparent outside connectors
    (kitty composites it over the cells); sixel callers pass the page
    background so alpha is resolved at raster time.
    """
    canvas = _Canvas(width_px, height_px)
    if background is not None:
        bg = _hex_rgb(background)
        for i in range(0, len(canvas.data), 4):
            canvas.data[i:i + 4] = bytes((*bg, 255))

    for conn in connectors:
        style = theme.chunk[conn.tag]
        fill = theme.chunk_fill(conn.tag, emphasized=conn.emphasized)
        fill_rgb = _hex_rgb(fill)
        line_rgb = _hex_rgb(style.line)
        tops = _curve_column_ys(conn.f0 - 0.5, conn.t0 - 0.5, width_px)
        bottoms = _curve_column_ys(conn.f1 + 0.5, conn.t1 + 0.5, width_px)
        for x in range(width_px):
            top, bottom = tops[x], bottoms[x]
            if bottom < top:
                top = bottom = (top + bottom) / 2.0
            canvas.fill_span(x, top, bottom, fill_rgb)
            # 1px stroke bands along both edges (line_width=1.0 upstream)
            canvas.fill_span(x, top - 0.5, top + 0.5, line_rgb)
            canvas.fill_span(x, bottom - 0.5, bottom + 0.5, line_rgb)

    # Keep the icons clear of the image edges. The arrow's *base* is a flat
    # shaft end; drawn at x=0 it butts straight into the neighbouring pane's
    # last text column and reads as if it were touching the letter there.
    aw, ah = arrow_size
    gap = max(1, arrow_margin)
    for arrow in arrows:
        if arrow.kind == "delete":
            # The cross fills its box corner to corner; give it the same air.
            w = max(4, aw - 2)
            x0 = width_px - w - gap if arrow.on_right else gap
            draw_delete(canvas, x0, arrow.top + 1, w, max(4, ah - 2), arrow.rgb)
        else:
            x0 = width_px - aw - gap if arrow.on_right else gap
            draw_arrow(canvas, x0, arrow.top, aw, ah, arrow.rgb,
                       pointing_right=not arrow.on_right)
    return canvas.data


def render_chunk_map(
    chunks: Iterable[Tuple[str, int, int]],
    total_lines: int,
    width_px: int,
    height_px: int,
    theme: Theme,
    viewport: Optional[Tuple[float, float]] = None,
    background: Optional[str] = None,
) -> bytearray:
    """Rasterize the overview map at pixel resolution.

    chunks are (tag, start_line, end_line); each paints an anti-aliased
    span in the saturated line color, at least 1px tall so single-line
    chunks in huge files stay visible (the cell renderer can't do
    that). viewport is (top_line, bottom_line) shaded with Meld's
    map-overlay color.
    """
    canvas = _Canvas(width_px, height_px)
    bg = _hex_rgb(background or theme.page_bg or "#000000")
    for i in range(0, len(canvas.data), 4):
        canvas.data[i:i + 4] = bytes((*bg, 255))

    total = max(total_lines, 1)
    scale = height_px / total
    for tag, start, end in chunks:
        style = theme.chunk.get(tag)
        if style is None:
            continue
        rgb = _hex_rgb(style.line)
        top = start * scale
        bottom = max(end, start + 1) * scale
        if bottom - top < 1.0:  # keep tiny chunks visible
            middle = (top + bottom) / 2.0
            top, bottom = middle - 0.5, middle + 0.5
        for x in range(width_px):
            canvas.fill_span(x, top, bottom, rgb)

    if viewport is not None:
        overlay_rgb = _hex_rgb(theme.overlay_color)
        top = viewport[0] * scale
        bottom = max(viewport[1] * scale, top + 1.0)
        for x in range(width_px):
            canvas.fill_span(
                x, top, bottom, overlay_rgb, alpha=theme.overlay_alpha
            )
    return canvas.data


# --- kitty graphics protocol -------------------------------------------------


def render_insert_marker(width_px: int, rgb: RGB) -> bytearray:
    """An opaque 2px line: Meld's zero-height chunk.

    Upstream draws every chunk with a [top, 0, bottom, 0] border on a rect of
    height max(1, y1 - y0) + 1 (sourceview.py do_snapshot), so a chunk with no
    lines on this side collapses to a single thin line at the point where the
    other pane's text would land. Opaque, so it needs no alpha compositing --
    it only covers the top two pixel rows of a character cell.
    """
    row = bytes((rgb[0], rgb[1], rgb[2], 255)) * width_px
    return bytearray(row * INSERT_MARKER_PX)


def kitty_place_escape(
    image_id: int, rgba: bytes, width_px: int, height_px: int
) -> str:
    """Transmit + place RGBA at the cursor. A stable image_id makes
    retransmission an atomic replace (flicker-free scroll repaints)."""
    payload = base64.standard_b64encode(zlib.compress(bytes(rgba)))
    pieces = [
        payload[i:i + 4096].decode("ascii")
        for i in range(0, len(payload), 4096)
    ] or [""]
    parts = []
    for index, piece in enumerate(pieces):
        keys = []
        if index == 0:
            keys += [
                "a=T", "f=32", "o=z", "q=2",
                f"i={image_id}", "p=1",
                f"s={width_px}", f"v={height_px}",
                "C=1",  # don't move the cursor
            ]
        keys.append(f"m={0 if index == len(pieces) - 1 else 1}")
        parts.append(f"\x1b_G{','.join(keys)};{piece}\x1b\\")
    return "".join(parts)


def kitty_delete_escape(image_id: int) -> str:
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


# --- sixel -------------------------------------------------------------------


def sixel_escape(rgba: bytes, width_px: int, height_px: int) -> str:
    """Encode an opaque RGBA buffer (raster with a background) as sixel."""
    # Build the palette from colors actually present, quantizing lightly
    # so anti-aliased edges don't explode the color count
    palette: Dict[RGB, int] = {}
    indexed = bytearray(width_px * height_px)
    for p in range(width_px * height_px):
        i = p * 4
        rgb = (
            rgba[i] & ~3,
            rgba[i + 1] & ~3,
            rgba[i + 2] & ~3,
        )
        index = palette.get(rgb)
        if index is None:
            index = len(palette)
            if index > 255:
                # Extremely unlikely with our palette; clamp to last
                index = 255
            else:
                palette[rgb] = index
        indexed[p] = index

    out = ['\x1bP0;0;8q"1;1;%d;%d' % (width_px, height_px)]
    for rgb, index in palette.items():
        out.append(
            "#%d;2;%d;%d;%d" % (
                index,
                round(rgb[0] * 100 / 255),
                round(rgb[1] * 100 / 255),
                round(rgb[2] * 100 / 255),
            )
        )

    for band_top in range(0, height_px, 6):
        band_rows = min(6, height_px - band_top)
        # Which colors appear in this band?
        band_colors = set()
        for row in range(band_rows):
            base = (band_top + row) * width_px
            band_colors.update(indexed[base:base + width_px])
        first_color = True
        for color in sorted(band_colors):
            if not first_color:
                out.append("$")  # carriage return within the band
            first_color = False
            out.append("#%d" % color)
            run_char = None
            run_len = 0
            for x in range(width_px):
                bits = 0
                for row in range(band_rows):
                    if indexed[(band_top + row) * width_px + x] == color:
                        bits |= 1 << row
                ch = chr(0x3F + bits)
                if ch == run_char:
                    run_len += 1
                else:
                    if run_char is not None:
                        out.append(
                            run_char if run_len == 1
                            else "!%d%s" % (run_len, run_char)
                        )
                    run_char, run_len = ch, 1
            if run_char is not None:
                out.append(
                    run_char if run_len == 1
                    else "!%d%s" % (run_len, run_char)
                )
        out.append("-")  # next band
    out.append("\x1b\\")
    return "".join(out)
