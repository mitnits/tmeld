"""Meld color palettes, extracted from upstream style schemes.

Values from upstream data/styles/meld-base.style-scheme.xml.in and
meld-dark.style-scheme.xml.in @ 9c4be506 — see PARITY.md §1. line_bg is
the whole-row chunk background; fg doubles as the tree-state text color.

Alpha entries from the GTK schemes are pre-composited here against the
scheme's assumed page background, since terminals have no alpha channel.
"""

from dataclasses import dataclass
from typing import Dict, Optional


def blend(base: str, over: str, alpha: float) -> str:
    """Pre-composite `over` at `alpha` onto opaque `base` (both #rrggbb).

    Terminals have no alpha channel, so every rgba() from the GTK schemes
    is blended at theme-load time against its actual backdrop.
    """
    b = int(base[1:], 16)
    o = int(over[1:], 16)
    out = 0
    for shift in (16, 8, 0):
        bc = (b >> shift) & 0xFF
        oc = (o >> shift) & 0xFF
        out |= round(bc + (oc - bc) * alpha) << shift
    return f"#{out:06x}"


@dataclass(frozen=True)
class ChunkStyle:
    fg: str       # tree-state text color (PARITY.md §2)
    fill: str     # whole-row chunk background (scheme "background")
    line: str     # chunk boundary line color (scheme "line-background")


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    chunk: Dict[str, ChunkStyle]  # keyed by tag: insert/replace/conflict/delete/error
    inline_bg: str
    unknown_fg: str
    dimmed_fg: str
    current_line_fg: str
    current_line_bg: str  # pre-composited
    page_bg: Optional[str]  # None = terminal default
    # Pane text styling (GTK parent scheme: classic / solarized-dark)
    text_fg: str = "#000000"
    selection_fg: str = "#ffffff"
    selection_bg: str = "#3584e4"
    # meld:current-chunk-highlight — white at alpha, composited over fills
    emphasis_color: str = "#ffffff"
    emphasis_alpha: float = 0.5

    @property
    def gutter_bg(self) -> str:
        """Meld's linkmap sits on the window background, a touch off the page.

        Terminals have no alpha, so pre-composite the way blend() does for the
        chunk fills. Works in both directions: on a dark scheme the light text
        colour lifts the channel instead of darkening it.
        """
        base = self.page_bg or ("#000000" if self.dark else "#ffffff")
        return blend(base, self.text_fg, 0.06)
    # map-overlay rgba(100,100,100,0.4) — composited over map cells
    overlay_color: str = "#646464"
    overlay_alpha: float = 0.4

    def chunk_fill(self, tag: str, emphasized: bool = False) -> str:
        fill = self.chunk[tag].fill
        if emphasized:
            fill = blend(fill, self.emphasis_color, self.emphasis_alpha)
        return fill

    @property
    def tab_bar_bg(self) -> str:
        """The strip behind the tabs, pushed hard away from the page.

        Not the gutter's grey: that sat 1.14:1 from the active tab, so two
        inactive tabs were indistinguishable. The bar goes toward whichever end
        has headroom -- black on a light scheme, white on a dark one, since a
        dark theme's page is already near-black and cannot go darker.
        """
        far = "#ffffff" if self.dark else "#000000"
        base = self.page_bg or ("#000000" if self.dark else "#ffffff")
        return blend(base, far, 0.87)

    @property
    def tab_inactive_bg(self) -> str:
        """Halfway between the bar and the page: distinct from both."""
        base = self.page_bg or ("#000000" if self.dark else "#ffffff")
        return blend(self.tab_bar_bg, base, 0.51)

    @staticmethod
    def _relative_luminance(color: str) -> float:
        parts = [int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        parts = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            for c in parts
        ]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

    @classmethod
    def contrast(cls, a: str, b: str) -> float:
        """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
        high, low = sorted(
            (cls._relative_luminance(a), cls._relative_luminance(b)),
            reverse=True,
        )
        return (high + 0.05) / (low + 0.05)

    def readable_on(self, background: str) -> str:
        """Black or white, whichever the eye can actually read there.

        The scheme's own text colour is fine on the page but can vanish on a
        mid-grey tab (solarized's #839496 on #6d858c is 1.23:1).
        """
        if self.contrast(self.text_fg, background) >= 4.5:
            return self.text_fg
        black, white = "#000000", "#ffffff"
        return black if self.contrast(black, background) >= self.contrast(white, background) else white

    def chunk_fg(self, tag: str) -> str:
        """Foreground for a gutter icon sitting on this chunk's fill.

        Meld aliases a "delete" chunk's fill and line to insert -- one-sided
        lines are green on whichever pane has them -- but meld:delete keeps its
        own dark-red foreground, which the dir tree uses for removed files. An
        icon drawn on the green fill has to follow the fill, not the tag.
        """
        return self.chunk["insert" if tag == "delete" else tag].fg

    def overlay(self, base: str) -> str:
        return blend(base, self.overlay_color, self.overlay_alpha)


MELD_BASE = Theme(
    name="meld-base",
    dark=False,
    chunk={
        "insert": ChunkStyle(fg="#008800", fill="#d0ffa3", line="#a5ff4c"),
        # "delete" chunk fills are aliased to insert in Meld
        # (upstream style.py get_common_theme): one-sided lines paint
        # green on whichever pane has them. meld:delete's own colors
        # only style tree states (removed/missing).
        "delete": ChunkStyle(fg="#880000", fill="#d0ffa3", line="#a5ff4c"),
        "replace": ChunkStyle(fg="#0044dd", fill="#bdddff", line="#65b2ff"),
        "conflict": ChunkStyle(fg="#ff0000", fill="#ffa5a3", line="#ff4f4c"),
        "error": ChunkStyle(fg="#faad3d", fill="#fce94f", line="#fade0a"),
    },
    inline_bg="#8ac2ff",
    unknown_fg="#888888",
    dimmed_fg="#999999",
    current_line_fg="#333333",
    # rgba(255,255,0,0.25) over #ffffff
    current_line_bg="#ffffbf",
    page_bg="#ffffff",
    text_fg="#000000",
    selection_fg="#ffffff",
    selection_bg="#3584e4",
)

MELD_DARK = Theme(
    name="meld-dark",
    dark=True,
    chunk={
        "insert": ChunkStyle(fg="#4e6206", fill="#123806", line="#245515"),
        # delete aliased to insert, as in upstream get_common_theme
        "delete": ChunkStyle(fg="#a40000", fill="#123806", line="#245515"),
        "replace": ChunkStyle(fg="#1d59d6", fill="#003266", line="#0053a6"),
        "conflict": ChunkStyle(fg="#ff0000", fill="#7a2a28", line="#ac3b39"),
        "error": ChunkStyle(fg="#faad3d", fill="#fce94f", line="#fdf8cd"),
    },
    inline_bg="#24527e",
    unknown_fg="#aaaaaa",
    dimmed_fg="#999999",
    current_line_fg="#eeeeee",
    # rgba(17,17,0,0.25) over solarized-dark base #002b36
    current_line_bg="#002128",
    page_bg="#002b36",
    text_fg="#839496",
    selection_fg="#93a1a1",
    selection_bg="#073642",
    # rgba(255,255,255,0.06) in the dark scheme
    emphasis_alpha=0.06,
    # map-overlay rgba(200,200,200,0.4) in the dark scheme
    overlay_color="#c8c8c8",
)

THEMES = {t.name: t for t in (MELD_BASE, MELD_DARK)}
DEFAULT_THEME = "meld-base"
