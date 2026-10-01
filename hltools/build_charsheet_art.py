"""The Inspecter tab's character-sheet art, cut out of the game's own UI.

Maintainer tool, not part of the meter: run it when the game's UI art
changes, and the meter ships the result in assets/charsheet/ (inlined in the
page by menu_host, nothing loaded at runtime).

    UI/Character/classBanners.png     540x512, one 128 px band per class
                                      (warrior, mage, rogue, priest)
    UI/icons/gear_slots.png           54 px cells: an empty slot's outline
    UI/icons/atlas_characterSheet_statsIcons_28PX.png
                                      28 px cells: the attributes' icons

Usage: python build_charsheet_art.py [--pak PATH]
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image

import pak_extract
from gamepath import find_hlboot

OUT = Path(__file__).resolve().parent.parent / "assets" / "charsheet"

BANNERS = ("warrior", "mage", "rogue", "priest")
# gear_slots.png, row by row (54 px cells)
SLOT_CELLS = {"Chest": (0, 0), "Hands": (1, 0), "Waist": (2, 0),
              "Legs": (3, 0), "Feet": (4, 0), "Back": (5, 0), "Head": (6, 0),
              "Shoulders": (7, 0), "Neck": (8, 0), "Finger": (9, 0),
              "Trinket": (1, 1)}
STAT_CELLS = ("Vitality", "Strength", "Dexterity", "Faith", "Intelligence")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", type=Path)
    a = ap.parse_args()
    pak = a.pak or Path(find_hlboot(99)).parent / "res.pak"
    OUT.mkdir(parents=True, exist_ok=True)

    def img(name):
        return Image.open(io.BytesIO(pak_extract.read_entry(pak, name))) \
                    .convert("RGBA")

    banners = img("UI/Character/classBanners.png")
    for i, key in enumerate(BANNERS):
        band = banners.crop((0, i * 128, banners.width, (i + 1) * 128))
        band.save(OUT / f"banner_{key}.webp", quality=82)
    slots = img("UI/icons/gear_slots.png")
    for name, (cx, cy) in SLOT_CELLS.items():
        slots.crop((cx * 54, cy * 54, cx * 54 + 54, cy * 54 + 54)) \
             .save(OUT / f"slot_{name}.png", optimize=True)
    stats = img("UI/icons/atlas_characterSheet_statsIcons_28PX.png")
    for i, name in enumerate(STAT_CELLS):
        stats.crop((i * 28, 0, i * 28 + 28, 28)) \
             .save(OUT / f"stat_{name}.png", optimize=True)
    print(f"written to {OUT}")


if __name__ == "__main__":
    main()
