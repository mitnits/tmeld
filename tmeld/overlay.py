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
from typing import List, Optional, Tuple

from tmeld.linkmap import (
    kitty_delete_escape,
    kitty_place_escape,
    sixel_escape,
)

# Each widget owns a block of kitty image ids: a widget may place several
# images (a pane draws one per visible insert marker), and ids must not collide
# between widgets.
IMAGE_IDS_PER_WIDGET = 256
_image_ids = itertools.count(100, IMAGE_IDS_PER_WIDGET)

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
        self._painted = 0  # how many image ids are currently on screen

    def _render_overlay(self) -> Optional[OverlayImage]:
        raise NotImplementedError

    def _render_overlays(self) -> List[OverlayImage]:
        """Images to paint this frame. Defaults to the single-image widget."""
        image = self._render_overlay()
        return [] if image is None else [image]

    def refresh_overlay(self) -> None:
        """Schedule an overlay repaint after the next Textual frame."""
        if self.graphics == "none" or getattr(self, "_overlay_scheduled", True):
            return
        self._overlay_scheduled = True
        self.app.call_after_refresh(self._paint_overlay)

    def clear_overlay(self) -> None:
        if self.graphics == "kitty":
            for i in range(max(1, getattr(self, "_painted", 1))):
                self._write(kitty_delete_escape(self._image_id + i))
            self._painted = 0

    def on_unmount(self) -> None:
        self.clear_overlay()

    def _paint_overlay(self) -> None:
        self._overlay_scheduled = False
        if self.graphics == "none" or not self.display:
            return
        images = self._render_overlays()[:IMAGE_IDS_PER_WIDGET]
        # Images we placed last frame but no longer want (a marker scrolled
        # away). kitty images float above the cells, so they must be deleted.
        if self.graphics == "kitty":
            for i in range(len(images), self._painted):
                self._write(kitty_delete_escape(self._image_id + i))
        self._painted = len(images)
        for i, (rgba, width_px, height_px, row, col) in enumerate(images):
            if self.graphics == "kitty":
                payload = kitty_place_escape(
                    self._image_id + i, rgba, width_px, height_px
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
