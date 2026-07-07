"""GraphicsOverlay: shared Tier-2 pixel-overlay plumbing for widgets.

A widget mixes this in, calls _init_graphics() from on_mount, and
implements _render_overlay() returning (rgba, width_px, height_px,
row, col) or None. Painting happens after the next Textual frame (so
pixels land on freshly drawn cells), deduplicated per frame; escapes
go out through app._driver.write — private API, same precedent as
pane._set_theme; re-audit on textual bump. kitty images are deleted on
unmount and on clear_overlay (they float above cells; sixel pixels are
plain cell content and self-heal on repaint).
"""

import itertools
from typing import Optional, Tuple

from tmeld.linkmap import (
    kitty_delete_escape,
    kitty_place_escape,
    sixel_escape,
)

_image_ids = itertools.count(100)

OverlayImage = Tuple[bytearray, int, int, int, int]  # rgba, w, h, row, col


class GraphicsOverlay:
    graphics = "none"
    cell_px: Tuple[int, int] = (8, 16)

    def _init_graphics(self) -> None:
        """Read the shell's probed mode; call from on_mount."""
        mode = getattr(self.app, "graphics", "none")
        if mode in ("kitty", "sixel"):
            self.graphics = mode
            self.cell_px = getattr(self.app, "cell_px", (8, 16))
        self._image_id = next(_image_ids)
        self._overlay_scheduled = False

    def _render_overlay(self) -> Optional[OverlayImage]:
        raise NotImplementedError

    def refresh_overlay(self) -> None:
        """Schedule an overlay repaint after the next Textual frame."""
        if self.graphics == "none" or getattr(self, "_overlay_scheduled", True):
            return
        self._overlay_scheduled = True
        self.app.call_after_refresh(self._paint_overlay)

    def clear_overlay(self) -> None:
        if self.graphics == "kitty":
            self._write(kitty_delete_escape(self._image_id))

    def on_unmount(self) -> None:
        self.clear_overlay()

    def _paint_overlay(self) -> None:
        self._overlay_scheduled = False
        if self.graphics == "none" or not self.display:
            return
        image = self._render_overlay()
        if image is None:
            return
        rgba, width_px, height_px, row, col = image
        if self.graphics == "kitty":
            payload = kitty_place_escape(
                self._image_id, rgba, width_px, height_px
            )
        else:
            payload = sixel_escape(rgba, width_px, height_px)
        # Save cursor, jump to the image cell, paint, restore
        self._write(f"\x1b7\x1b[{row + 1};{col + 1}H{payload}\x1b8")

    def _write(self, escape: str) -> None:
        driver = getattr(self.app, "_driver", None)
        if driver is not None:
            try:
                driver.write(escape)
            except Exception:
                pass

    def on_resize(self) -> None:
        self.refresh_overlay()
