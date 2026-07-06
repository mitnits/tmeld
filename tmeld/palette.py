"""Meld color palettes, extracted from upstream style schemes.

Values from upstream data/styles/meld-base.style-scheme.xml.in and
meld-dark.style-scheme.xml.in @ 9c4be506 — see PARITY.md §1. line_bg is
the whole-row chunk background; fg doubles as the tree-state text color.

Alpha entries from the GTK schemes are pre-composited here against the
scheme's assumed page background, since terminals have no alpha channel.
"""

from dataclasses import dataclass
from typing import Dict, Optional


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
)

THEMES = {t.name: t for t in (MELD_BASE, MELD_DARK)}
DEFAULT_THEME = "meld-base"
