"""Draw assets/fareverbook.ico — the tray, executable and installer icon —
and assets/grimoire.png, the header's: the game's apprentice grimoire
(Grimoire d'apprenti), read from the installed game's res.pak.

    py packaging/make_icon.py
"""
import io
import sys
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hltools"))
import pak_extract  # noqa: E402
from gamepath import find_hlboot  # noqa: E402

OUT = ROOT / "assets" / "fareverbook.ico"
HEADER = ROOT / "assets" / "grimoire.png"
SOURCE = "UI/Portraits/Items/Book/Book_Start.png"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
HEADER_PX = 168             # the header shows it at 56 px: drawn at 3x


def grimoire():
    """The grimoire, cropped to its pixels and centred on a square."""
    pak = Path(find_hlboot()).parent / "res.pak"
    raw = pak_extract.read_entry(pak, SOURCE)
    if not raw:
        sys.exit(f"[!] {SOURCE} not found in {pak}")
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())
    side = max(im.size)
    sq = Image.new("RGBA", (side, side))
    sq.alpha_composite(im, ((side - im.width) // 2, (side - im.height) // 2))
    return sq


def at(src, size):
    # small sizes lose their edges in the reduction: sharpened back
    im = src.resize((size, size), Image.LANCZOS)
    return im.filter(ImageFilter.UnsharpMask(1, 60, 2)) if size <= 48 else im


def main():
    src = grimoire()
    frames = [at(src, s) for s in SIZES]
    # one real image per resolution, not one Windows rescales
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    at(src, HEADER_PX).save(HEADER, optimize=True)
    print(f"[written] {OUT}  ({', '.join(f'{s}x{s}' for s in SIZES)})")
    print(f"[written] {HEADER}")


if __name__ == "__main__":
    main()
