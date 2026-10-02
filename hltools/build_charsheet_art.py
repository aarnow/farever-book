"""The Inspecter tab's character-sheet art, cut out of the game's own UI.

Maintainer tool, not part of the meter: run it when the game's UI art
changes, and the meter ships the result in assets/charsheet/ (inlined in the
page by menu_host, nothing loaded at runtime).

    UI/icons/gear_slots.png           54 px cells: an empty slot's outline
    UI/icons/atlas_characterSheet_statsIcons_28PX.png
                                      28 px cells: the attributes' icons
    UI/icons/ui_icons_26PX.png        26 px cells: the 8th is a gear
                                      upgrade's pip
    UI/Elements/talent/talent_tree_boxes.png
                                      160x146 cells: the talent tree's
                                      shapes (diamond, small diamond, large
                                      diamond, triangle), then the grey
                                      they take when nothing is in them
    UI/icons/POI_DifficultySkulls_atlas_38PX.png
                                      38 px cells: the dungeon difficulties
                                      (Normal, Vétéran, Héroïque)
    UI/icons/Steam_Achievments/Steam_Achievments_unlocked2.png
                                      the achievement badge (Succès tab)

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

# gear_slots.png, row by row (54 px cells)
SLOT_CELLS = {"Chest": (0, 0), "Hands": (1, 0), "Waist": (2, 0),
              "Legs": (3, 0), "Feet": (4, 0), "Back": (5, 0), "Head": (6, 0),
              "Shoulders": (7, 0), "Neck": (8, 0), "Finger": (9, 0),
              "Trinket": (1, 1)}
TALENT_BOXES = ("diamond", "small", "large", "triangle")
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

    slots = img("UI/icons/gear_slots.png")
    for name, (cx, cy) in SLOT_CELLS.items():
        slots.crop((cx * 54, cy * 54, cx * 54 + 54, cy * 54 + 54)) \
             .save(OUT / f"slot_{name}.png", optimize=True)
    stats = img("UI/icons/atlas_characterSheet_statsIcons_28PX.png")
    for i, name in enumerate(STAT_CELLS):
        stats.crop((i * 28, 0, i * 28 + 28, 28)) \
             .save(OUT / f"stat_{name}.png", optimize=True)
    pips = img("UI/icons/ui_icons_26PX.png")
    pips.crop((7 * 26, 0, 8 * 26, 26)).save(OUT / "upgrade_pip.png",
                                            optimize=True)
    boxes = img("UI/Elements/talent/talent_tree_boxes.png")
    for i, name in enumerate(TALENT_BOXES):
        for row, kind in ((0, "box"), (1, "dim")):
            boxes.crop((i * 160, row * 146, i * 160 + 160, row * 146 + 146))                  .save(OUT / f"talent_{kind}_{name}.png", optimize=True)
    skulls = img("UI/icons/POI_DifficultySkulls_atlas_38PX.png")
    for i in range(3):
        skulls.crop((i * 38, 0, i * 38 + 38 + (1 if i == 2 else 0), 38))               .save(OUT / f"dungeon_diff_{i}.png", optimize=True)
    badge = img("UI/icons/Steam_Achievments/Steam_Achievments_unlocked2.png")
    badge.resize((64, 64), Image.LANCZOS).save(OUT / "ach_badge.png",
                                               optimize=True)
    print(f"written to {OUT}")


if __name__ == "__main__":
    main()
