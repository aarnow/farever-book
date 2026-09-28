"""Build the class icons shown next to player names, from the game's own art.

Maintainer tool, not part of the meter: run it when the game changes its class
icons (rarely). It reads UI/icons/Classes/class_icon.png out of res.pak — one
strip of four white silhouettes — cuts it into the four classes, tints each
one with its class colour, and writes assets/classes/<class>.png. The meter
only ever reads those files.

    python hltools/build_class_icons.py [path\\to\\res.pak]

The images belong to the game: fine for personal use, not for redistribution.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pak_extract import read_tree  # noqa: E402

ENTRY = "UI/icons/Classes/class_icon.png"
OUT = HERE.parent / "assets" / "classes"
# The strip's order, left to right, read off the art: sword and shield, orb and
# flame, twin daggers, halo and cross.
ORDER = ("warrior", "mage", "rogue", "priest")
# The meter's class colours (the original project's palette).
COLOURS = {"warrior": "#D98A5A", "mage": "#6FA8DC",
           "rogue": "#87B37A", "priest": "#C9B87A"}
SIZE = 64                       # square output, icon centred in it


def find_pak():
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    from gamepath import find_hlboot
    return Path(find_hlboot(argv_index=99)).with_name("res.pak")


def read_entry(pak: Path, name: str) -> bytes:
    """One file out of a Heaps pak, reading only the directory and that file —
    res.pak is several gigabytes."""
    with open(pak, "rb") as f:
        head = f.read(12)
        header_size = struct.unpack_from("<i", head, 4)[0]
        f.seek(0)
        entries, data_off = read_tree(f.read(header_size), pak.name)
        hit = next((e for e in entries if e.path == name), None)
        if hit is None:
            sys.exit(f"[!] {name} is not in {pak}")
        f.seek(data_off + hit.pos)
        return f.read(hit.size)


def split(strip):
    """The four icons, cut at the fully transparent columns between them."""
    alpha = strip.getchannel("A")
    w, h = strip.size
    filled = [any(alpha.getpixel((x, y)) > 8 for y in range(h))
              for x in range(w)]
    spans, start = [], None
    for x, on in enumerate(filled + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            spans.append((start, x))
            start = None
    if len(spans) != len(ORDER):
        sys.exit(f"[!] expected {len(ORDER)} icons in the strip, found "
                 f"{len(spans)} — the art changed; check ORDER.")
    return [strip.crop((a, 0, b, h)) for a, b in spans]


def main():
    from io import BytesIO
    from PIL import Image

    pak = find_pak()
    strip = Image.open(BytesIO(read_entry(pak, ENTRY))).convert("RGBA")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, icon in zip(ORDER, split(strip)):
        icon = icon.crop(icon.getbbox())
        colour = Image.new("RGBA", icon.size, COLOURS[name])
        colour.putalpha(icon.getchannel("A"))
        icon = colour
        scale = (SIZE - 4) / max(icon.size)
        icon = icon.resize((max(1, round(icon.width * scale)),
                            max(1, round(icon.height * scale))),
                           Image.LANCZOS)
        canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        canvas.paste(icon, ((SIZE - icon.width) // 2,
                            (SIZE - icon.height) // 2))
        canvas.save(OUT / f"{name}.png")
        print(f"[+] {OUT / (name + '.png')}")


if __name__ == "__main__":
    main()
