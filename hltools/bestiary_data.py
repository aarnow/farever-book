"""The bestiary: every monster the game places in its world, dungeons and
rifts, for the app's hunting log. The kill counts themselves are the game's
own (Progress.unitsProgress, read by the hook); this is the list to count
against.

A monster is a unit some level spawns — a spawner's `unit`, or the units of
its `unitGroup` — minus what is not a monster: companions (Critter), mounts,
totems, scenery, and the units data.cdb keeps out of the codex. Each comes
with its family (unit.type -> unitType), the zones it spawns in and their
region, and its codex tier (codex_units.json: elite / big / foe).

For the hunting log's monster page, two more things:
* spawns: where each spawner of the open world (W1_Siagarta, the level the
  Map tab draws) stands, in world coordinates — a spawner is the `props` of
  a level object, so it stands where that object does (its x/y, through its
  parents' offsets and rotations, as in map_data.py). Dungeon and rift levels
  have no minimap: their monsters keep their zones only.
* loot: what a kill can give — the family's table (unitType.lootTable) and
  the unit's own (lootTable, bossLootTable), nested tables flattened, each
  item with its chance per kill (collection_data._table_items)."""
import json
import math
import re
import struct
from collections import defaultdict
from pathlib import Path

import imgcache
import pak_extract
from collection_data import _levels, _table_items

NOT_MONSTERS = {"Critter", "Mount", "Totem", "Environment"}
IMG_PX = 96


def build(game_dir, codex, img_dir=None):
    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}

    def rows(name):
        return {ln["id"]: ln for ln in sheets[name].get("lines") or ()
                if isinstance(ln.get("id"), str)}

    units, groups, zones = rows("unit"), rows("unitGroup"), rows("zone")
    tables = rows("lootTable")

    def region(z):
        seen = set()
        while z in zones and z not in seen:
            seen.add(z)
            if zones[z].get("type") == 0:
                return z
            z = zones[z].get("parent")
        return None

    spawned = defaultdict(set)          # unit -> zones
    spots = defaultdict(set)            # unit -> {(x, y, zone)}, open world

    def walk(o, zone, px, py, rot, world):
        if isinstance(o, dict):
            props = o.get("props") if isinstance(o.get("props"), dict) else {}
            zone = props.get("zoneBaked") or o.get("zoneBaked") or zone
            x, y = o.get("x"), o.get("y")
            wx, wy, wr = px, py, rot
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                wx, wy = px + x * c - y * s, py + x * s + y * c
                wr = rot + float(o.get("rotationZ") or 0)
            if o.get("$cdbtype") == "spawner" or "unitGroup" in o:
                here = []
                if isinstance(o.get("unit"), str):
                    here.append(o["unit"])
                g = groups.get(o.get("unitGroup"))
                for c in (g or {}).get("composition") or ():
                    for m in c.get("group") or ():
                        if m.get("unit"):
                            here.append(m["unit"])
                for u in here:
                    spawned[u].add(zone)
                    if world:
                        spots[u].add((round(wx), round(wy), zone or ""))
            for k, v in o.items():
                # an object's props and children stand where it does
                if k in ("children", "props"):
                    walk(v, zone, wx, wy, wr, world)
                else:
                    walk(v, zone, px, py, rot, world)
        elif isinstance(o, list):
            for v in o:
                walk(v, zone, px, py, rot, world)

    for path, level in _levels(game_dir):
        walk(level, None, 0.0, 0.0, 0.0,
             path.startswith("Level/World/W1_Siagarta.dat/gameplayData/"))

    no_codex = set(codex.get("noCodex") or ())
    elite, big = set(codex.get("elite") or ()), set(codex.get("big") or ())

    def monster(uid, codex_only=True):
        u = units.get(uid)
        return bool(u and u.get("type") not in NOT_MONSTERS
                    and not (codex_only and uid in no_codex)
                    and not uid.startswith("TODO")
                    and (u.get("texts") or {}).get("name"))

    def tier(uid):
        return "elite" if uid in elite else "big" if uid in big else "foe"

    placed = []
    for uid, zs in spawned.items():
        if not monster(uid):
            continue
        zs = sorted(z for z in zs if z)
        regions = sorted({r for r in (region(z) for z in zs) if r})
        placed.append({"id": uid, "family": units[uid].get("type") or "",
                       "tier": tier(uid), "zones": zs, "regions": regions,
                       "lvl": units[uid].get("lvl")})
    placed.sort(key=lambda e: (e["regions"][:1] or ["~"], e["family"],
                               e["id"]))

    # Every monster of the game, for the ones the levels don't place (rift
    # waves, invasions, summons, boss champions) that show up in a codex:
    # their family, tier, and a region read off the id (Crab_Z1D -> Z1).
    # The units kept out of the codex's pages (boss champions, dungeon
    # variants, portals) are in: the game still counts their kills.
    every = {}
    for uid in units:
        if not monster(uid, codex_only=False):
            continue
        m = re.search(r"_Z(\d)", uid)
        every[uid] = {"family": units[uid].get("type") or "",
                      "tier": tier(uid),
                      "region": "rift" if "Rift" in uid or "Portal" in uid
                      else f"Z{m.group(1)}_Region" if m else ""}
    families = sorted({e["family"] for e in every.values() if e["family"]})

    def table(tid):
        return {i: round(p, 5) for i, p in _table_items(tables, tid).items()
                if p > 0} if tid else {}

    types = rows("unitType")
    fam_loot = {f: table(types.get(f, {}).get("lootTable"))
                for f in families}
    unit_loot = {}
    for uid in every:
        p = units[uid].get("props") or {}
        own = {}
        for key, src in (("lootTable", "unit"), ("bossLootTable", "boss")):
            # a line can require a difficulty (the bosses' infusion
            # pattern: Heroic only, conditions.difficulty.min = 2)
            need = {}
            for ln in (tables.get(p.get(key)) or {}).get("loot") or ():
                d = (((ln.get("conditions") or {}).get("difficulty") or {})
                     .get("min") or [None])[0]
                if ln.get("item") and d:
                    need[ln["item"]] = d
            for i, q in table(p.get(key)).items():
                own[i] = [max(q, (own.get(i) or [0])[0]), src, need.get(i)]
        if own:
            unit_loot[uid] = own
    spawns = {u: sorted([x, y, z] for x, y, z in pts)
              for u, pts in spots.items() if u in every}
    lvls = {uid: units[uid].get("lvl") for uid in every
            if units[uid].get("lvl")}

    if img_dir is not None:
        type_gfx = {tid: r.get("gfx") for tid, r in rows("unitType").items()}
        gfx = {uid: units[uid].get("gfx") for uid in every}
        gfx.update({f"family_{f}": type_gfx.get(f) for f in families})
        _images(game_dir, img_dir, gfx)
    return {"placed": placed, "units": every, "families": families,
            "spawns": spawns, "lvl": lvls, "famLoot": fam_loot,
            "unitLoot": unit_loot}


def _images(game_dir, out_dir, gfx):
    import io
    from PIL import Image
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pak = game_dir / "res.pak"
    with open(pak, "rb") as f:
        header = struct.unpack_from("<i", f.read(12), 4)[0]
        f.seek(0)
        entries, off = pak_extract.read_tree(f.read(header), pak.name)
        by_path = {e.path: e for e in entries}
        for uid, g in gfx.items():
            e = by_path.get(g.get("file")) if isinstance(g, dict) else None
            if e is None:
                continue
            f.seek(off + e.pos)
            try:
                img = Image.open(io.BytesIO(f.read(e.size))).convert("RGBA")
            except Exception:
                continue
            n = int(g.get("size") or img.width)
            x, y = int(g.get("x") or 0) * n, int(g.get("y") or 0) * n
            if x + n > img.width or y + n > img.height:
                n = min(img.width, img.height)
                x = y = 0
            imgcache.save(img.crop((x, y, x + n, y + n)).resize(
                (IMG_PX, IMG_PX), Image.LANCZOS),
                out_dir / f"{uid}.webp", quality=80, method=6)
