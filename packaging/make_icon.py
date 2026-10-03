"""Draw assets/farevermeter.ico — the tray, executable and installer icon.

Generated rather than committed as an opaque binary: it is the window's own
emblem (the Farever France shield), so a re-skin can follow by editing the
colour constants here instead of by opening an image editor.

    py packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "farevermeter.ico"

# The window's emblem (menu.css .emblem): a dark-blue-rimmed shield, its
# field in the French colours — blue, white, red, side by side.
RIM_HI, RIM_LO = (42, 63, 143), (14, 26, 74)
BANDS = ((0, 85, 164), (255, 255, 255), (239, 65, 53))
SHADE = 0.22                # how much darker a band gets at the shield's tip

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _shield(w, h, inset=0.0):
    """The shield's outline in a w x h box, shrunk by `inset` pixels."""
    pts = [(0.5, 0), (1, 0.14), (1, 0.62), (0.5, 1), (0, 0.62), (0, 0.14)]
    cx, cy = w / 2, h / 2
    sx, sy = (w - 2 * inset) / w, (h - 2 * inset) / h
    return [(cx + (x * w - cx) * sx, cy + (y * h - cy) * sy) for x, y in pts]


def _gradient(size, top, bottom):
    g = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(g)
    for y in range(size):
        t = y / max(1, size - 1)
        d.line([(0, y), (size, y)], fill=tuple(
            round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return g


def draw(size: int) -> Image.Image:
    # drawn 4x then reduced: the shield's slanted edges stay smooth
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    w, h = big * 0.86, big * 0.96
    ox, oy = (big - w) / 2, (big - h) / 2

    def placed(pts):
        return [(ox + x, oy + y) for x, y in pts]

    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).polygon(placed(_shield(w, h)), fill=255)
    img.paste(_gradient(big, RIM_HI, RIM_LO), (0, 0), mask)
    rim = max(big * 0.075, 4)
    mask2 = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask2).polygon(placed(_shield(w, h, rim)), fill=255)
    # the field: three vertical bands, each a little darker towards the tip
    field = Image.new("RGBA", (big, big))
    inner = placed(_shield(w, h, rim))
    x0 = min(x for x, _ in inner)
    x1 = max(x for x, _ in inner)
    for i, col in enumerate(BANDS):
        dark = tuple(round(c * (1 - SHADE)) for c in col)
        band = _gradient(big, col, dark)
        left = round(x0 + (x1 - x0) * i / 3)
        right = round(x0 + (x1 - x0) * (i + 1) / 3)
        field.paste(band.crop((left, 0, right, big)), (left, 0))
    img.paste(field, (0, 0), mask2)
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [draw(s) for s in SIZES]
    # Pillow writes every append_images frame at its own size, so the .ico ends
    # up with a real image per resolution instead of one Windows has to rescale.
    frames[-1].save(OUT, format="ICO",
                    sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    print(f"[written] {OUT}  ({', '.join(f'{s}x{s}' for s in SIZES)})")


if __name__ == "__main__":
    main()
