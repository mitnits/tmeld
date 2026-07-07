"""Generate an SVG screenshot of tmeld for docs/preview purposes."""

import asyncio
import sys

from tmeld.app import TmeldApp


async def shoot(paths, out):
    app = TmeldApp(paths)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()
        app.panes[0].scroll_to(y=2, animate=False)
        await pilot.pause()
        svg = app.export_screenshot(title="tmeld")
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(shoot(sys.argv[1:-1], sys.argv[-1]))
