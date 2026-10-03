"""The world map for the app's Map tab: the game's own minimap tiles, and the
completion points placed in the world — chests, red orbs, obelisks and
respawn points — each with its position and zone.

Tiles: res.map.pak Level/World/<world>.dat/minimap/<tx>_<ty>_1024.png. Tile
(tx, ty) covers world x in [tx*576, (tx+1)*576) and y in [ty*576,
(ty+1)*576); +y is south, so the map reads with +y DOWN. (Worked out on the
game's minimap and cross-checked against questlog.gg, whose markers use
raw in-game coordinates on these same tiles.)

Points: the world level's elements (HBSON, see hbson.py), by the prefab they
are an instance of. Their x/y are world coordinates; the rare one nested in
another object is placed through its parent (offset, rotation in degrees).
The counts match questlog.gg's: 131 world chests, 8 vault chests, 23 recipe
chests, 284 red orbs, 11 obelisks, 29 respawn points."""
import io
import json
import math
import re
import struct
from pathlib import Path

import imgcache
import pak_extract
from collection_data import _levels

WORLD = "W1_Siagarta"
UNITS_PER_TILE = 576.0
TILE_SRC_PX = 1024
TILE_PX = 512
CATEGORIES = {"WorldChest.prefab": "chest", "VaultChest.prefab": "vault",
              "Recipe_Chest.prefab": "recipe", "RedOrb_World.prefab": "orb",
              "Obelisk.prefab": "obelisk", "RespawnPoint.prefab": "respawn"}


# Each point category's marker, by the game's own icon rows (icon sheet:
# the map window's markers and completion icons).
MARKER_ICONS = {"chest": "ChestCompletion", "vault": "VaultChestMarker",
                "recipe": "RecipeChestMarker", "orb": "RedOrbCompletion",
                "obelisk": "ObeliskMarker", "respawn": "RespawnPointMarker",
                "dungeon": "Dungeon"}
# The rifts have no icon row of their own: the same sheet's purple eye, the
# chaos one, stands for them (the hunting log's rift entrances).
RAW_ICONS = {"rift": {"file": "UI/icons/activities.png", "size": 128,
                      "x": 2, "y": 2}}


def build(game_dir, tile_dir=None):
    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    if tile_dir is not None:
        from collection_data import _images
        icons = {ln["id"]: ln.get("gfx") for s in cdb["sheets"]
                 if s["name"] == "icon" for ln in s["lines"]
                 if isinstance(ln.get("id"), str)}
        _images(game_dir, tile_dir,
                {"icon_" + c: icons.get(i) for c, i in MARKER_ICONS.items()
                 if icons.get(i)}
                | {"icon_" + c: g for c, g in RAW_ICONS.items()})
    zones = {ln["id"]: ln for s in cdb["sheets"] if s["name"] == "zone"
             for ln in s["lines"] if isinstance(ln.get("id"), str)}

    def region(z):
        seen = set()
        while z in zones and z not in seen:
            seen.add(z)
            if zones[z].get("type") == 0:
                return z
            z = zones[z].get("parent")
        return None

    points = []

    def walk(o, zone, px, py, rot):
        if isinstance(o, dict):
            props = o.get("props") if isinstance(o.get("props"), dict) else {}
            zone = props.get("zoneBaked") or o.get("zoneBaked") or zone
            x, y = o.get("x"), o.get("y")
            wx, wy, wr = px, py, rot
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                wx, wy = px + x * c - y * s, py + x * s + y * c
                wr = rot + float(o.get("rotationZ") or 0)
            cat = CATEGORIES.get(str(o.get("source") or "").split("/")[-1])
            # one red orb rides a moving object and has no x/y of its own:
            # it is placed where its carrier is
            if cat:
                points.append({"c": cat, "id": props.get("id") or o.get("name"),
                               "x": round(wx, 1), "y": round(wy, 1),
                               "zone": zone or "", "region": region(zone) or ""})
            for k, v in o.items():
                if k == "children":
                    walk(v, zone, wx, wy, wr)
                else:
                    walk(v, zone, px, py, rot)
        elif isinstance(o, list):
            for v in o:
                walk(v, zone, px, py, rot)

    for path, level in _levels(game_dir):
        if path.startswith(f"Level/World/{WORLD}.dat/gameplayData/"):
            walk(level, None, 0.0, 0.0, 0.0)

    tiles = _tiles(game_dir, tile_dir)
    xs = [t[0] for t in tiles] or [0]
    ys = [t[1] for t in tiles] or [0]
    meta = {"world": WORLD, "units_per_tile": UNITS_PER_TILE,
            "tile_px": TILE_PX, "tx": [min(xs), max(xs)],
            "ty": [min(ys), max(ys)],
            "tiles": sorted(f"{tx}_{ty}" for tx, ty in tiles)}
    return {"meta": meta, "points": points}


def _tiles(game_dir, out_dir):
    """The world's minimap tiles, out_dir/<tx>_<ty>.webp at TILE_PX."""
    from PIL import Image
    pak = game_dir / "res.map.pak"
    pat = re.compile(rf"Level/World/{WORLD}\.dat/minimap/(-?\d+)_(-?\d+)_"
                     rf"{TILE_SRC_PX}\.png$", re.I)
    found = []
    with open(pak, "rb") as f:
        header = struct.unpack_from("<i", f.read(12), 4)[0]
        f.seek(0)
        entries, off = pak_extract.read_tree(f.read(header), pak.name)
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
        for e in entries:
            m = pat.search(e.path)
            if not m:
                continue
            tx, ty = int(m.group(1)), int(m.group(2))
            found.append((tx, ty))
            if out_dir is None:
                continue
            f.seek(off + e.pos)
            im = Image.open(io.BytesIO(f.read(e.size))).convert("RGB")
            imgcache.save(im.resize((TILE_PX, TILE_PX), Image.LANCZOS),
                          Path(out_dir) / f"{tx}_{ty}.webp", quality=78,
                          method=6)
    return found
