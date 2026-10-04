"""The collection catalogue: every mount, glider, companion and armour
appearance, with how each is obtained, for the app's Collection tab.

Sources: data.cdb (items, Critter units, loot tables and who rolls them,
achievement rewards, unitGroup spawn weights, starting gliders) and the
levels (HBSON prefabs: shops, placed chests, spawners, with zoneBaked).

A loot table flagged Weights gives ONE line, by weight; otherwise each line
rolls on its own. Nested tables multiply."""
import io
import json
import struct
from collections import defaultdict
from pathlib import Path

import hbson
import imgcache
import pak_extract

CATEGORIES = (("mounts", "Mount"), ("gliders", "GearGlider"))
# Flags are read by name: a patch that edits the enum moves their bits.
# itemType AppearanceCollection: what Collection.gears counts. item WorldLoot:
# dropped at random around its level.


def flag_bit(sheets, sheet, name, column="flags"):
    """The bit of one flag of a cdb flags column, from its definition
    ("10:NoStack,NoAutoDisplay,..."); 0 when the flag is gone."""
    for c in sheets[sheet]["columns"]:
        if c.get("name") == column and isinstance(c.get("typeStr"), str):
            names = c["typeStr"].split(":", 1)[-1].split(",")
            if name in names:
                return 1 << names.index(name)
    return 0
# the factions whose gear their activities and chests give
# (constant Loot_FactionDropRate: Activity 20 %, Element 5 %, Foes 0)
FACTIONS = ("Bee", "Crimson", "Demon", "Kobold", "Manfish")
IMG_PX = 128
CLASS_UNITS = {"Warrior": "warrior", "Mage": "mage", "Rogue": "rogue",
               "Priest": "priest"}


def _levels(game_dir):
    """(path, decoded level) for every HBSON prefab of the level paks."""
    for name in ("res.levels.pak", "res.map.pak"):
        pak = game_dir / name
        with open(pak, "rb") as f:
            header = struct.unpack_from("<i", f.read(12), 4)[0]
            f.seek(0)
            entries, off = pak_extract.read_tree(f.read(header), pak.name)
            for e in entries:
                if not e.path.endswith(".prefab"):
                    continue
                f.seek(off + e.pos)
                raw = f.read(e.size)
                if raw[:5] != b"HBSON":
                    continue
                try:
                    yield e.path, hbson.loads(raw)
                except hbson.HBSONError:
                    continue


def _table_items(tables, tid, seen=()):
    """item -> chance for one loot table, nested tables included."""
    t = tables.get(tid) or {}
    lines = t.get("loot") or []
    weighted = bool((t.get("flags") or 0) & 1)
    total = sum(float(ln.get("proba") or 0) for ln in lines) or 1.0
    out = defaultdict(float)
    for ln in lines:
        p = float(ln.get("proba") or 0)
        p = p / total if weighted else min(p, 1.0)
        if ln.get("item"):
            out[ln["item"]] = max(out[ln["item"]], p)
        if ln.get("lootTable") and ln["lootTable"] not in seen:
            for iid, q in _table_items(tables, ln["lootTable"],
                                       seen + (tid,)).items():
                out[iid] = max(out[iid], p * q)
    return out


def build(game_dir, img_dir=None):
    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}

    def rows(name):
        return {ln["id"]: ln for ln in sheets[name].get("lines") or ()
                if isinstance(ln.get("id"), str)}

    items, units = rows("item"), rows("unit")
    tables, groups = rows("lootTable"), rows("unitGroup")
    wanted = {cat: [iid for iid, r in items.items() if r.get("type") == t]
              for cat, t in CATEGORIES}
    itypes = rows("itemType")
    appearance_flag = flag_bit(sheets, "itemType", "AppearanceCollection")
    world_loot_flag = flag_bit(sheets, "item", "WorldLoot")

    def appearance(t):
        seen = set()
        while t in itypes and t not in seen:
            seen.add(t)
            if (itypes[t].get("flags") or 0) & appearance_flag:
                return True
            t = itypes[t].get("inherit")
        return False
    wanted["gears"] = [iid for iid, r in items.items()
                       if appearance(r.get("type"))]
    collectible = {iid for ids in wanted.values() for iid in ids}
    gear_set = set(wanted["gears"])
    # unnamed Critter units are scenery
    critters = [uid for uid, r in units.items() if r.get("type") == "Critter"
                and (r.get("texts") or {}).get("name")]
    critter_set = set(critters)
    src = defaultdict(list)             # id -> [source]

    def add(iid, s):
        if s not in src[iid]:
            src[iid].append(s)

    # -- who rolls which table (cdb side)
    table_users = defaultdict(list)
    for tid, r in rows("unitType").items():
        if r.get("lootTable"):
            table_users[r["lootTable"]].append({"k": "family", "id": tid})
    for uid, r in units.items():
        props = r.get("props") or {}
        for key in ("lootTable", "bossLootTable"):
            if props.get(key):
                table_users[props[key]].append({"k": "unit", "id": uid})
        # a class's starting glider sits in its unit row
        if uid in CLASS_UNITS:
            for iid in collectible:
                if f'"{iid}"' in json.dumps(r):
                    add(iid, {"k": "starter", "cls": CLASS_UNITS[uid],
                              "gear": iid in gear_set})

    # -- achievements. A tier's name and text are its parent's.
    achs = rows("ach")
    for aid, r in achs.items():
        chain, p = [aid], r.get("parent")
        while p in achs and p not in chain:
            chain.append(p)
            p = achs[p].get("parent")
        v = next(((o.get("value") or {}).get("v")
                  for o in r.get("objectives") or () if o.get("value")), None)
        for it in ((r.get("reward") or {}).get("items") or ()):
            if it.get("item") in collectible:
                add(it["item"], {"k": "ach", "id": aid, "chain": chain,
                                 "v": v})

    # -- the levels: merchants, chests, critter spawners
    group_zones = defaultdict(set)
    fac_acts = defaultdict(dict)        # faction -> {activity id: kind}
    fac_chests = defaultdict(set)       # faction -> {chest id}

    def walk(o, zone):
        if isinstance(o, dict):
            props = o.get("props") if isinstance(o.get("props"), dict) else {}
            zone = props.get("zoneBaked") or o.get("zoneBaked") or zone
            fac = props.get("faction") or o.get("faction")
            oid = o.get("id")
            if fac in FACTIONS and isinstance(oid, str):
                if o.get("$cdbtype") == "activity":
                    fac_acts[fac][oid] = o.get("inherit") or "?"
                elif o.get("$cdbtype") == "element" and "Chest" in oid:
                    fac_chests[fac].add(oid)
            if isinstance(props.get("shop"), list):
                npc = ((props.get("npc") or {}).get("unit")
                       if isinstance(props.get("npc"), dict) else None)
                for s in props["shop"]:
                    iid = s.get("item") if isinstance(s, dict) else None
                    pet = iid[len("Critter_"):] if iid and \
                        iid.startswith("Critter_") else None
                    # {item, qty} or {kind, amount} (2026-09-30 patch)
                    cost = [{"item": c.get("item") or c.get("kind"),
                             "n": c.get("qty") or c.get("count")
                             or c.get("amount")}
                            for c in s.get("cost") or () if isinstance(c, dict)
                            # "1 Gold" is a placeholder (the game prices it)
                            and not (c.get("kind") == "Gold"
                                     and (c.get("amount") or 1) <= 1)]
                    entry = {"k": "shop", "npc": npc or o.get("name"),
                             "zone": zone, "cost": cost}
                    if iid in collectible:
                        add(iid, entry)
                    elif pet in critter_set:
                        add(pet, entry)
            if props.get("lootTable") and isinstance(props["lootTable"], str):
                table_users[props["lootTable"]].append(
                    {"k": "chest", "id": o.get("name") or "Chest",
                     "zone": zone})
            for li in props.get("lootItems") or ():
                if isinstance(li, dict) and li.get("item") in collectible:
                    rate = li.get("dropRate")
                    add(li["item"], {"k": "chest",
                                     "id": o.get("name") or "Chest",
                                     "zone": zone,
                                     "chance": float(rate) if rate else 1.0})
            if isinstance(o.get("unitGroup"), str) and zone:
                group_zones[o["unitGroup"]].add(zone)
            if isinstance(o.get("unit"), str) and o["unit"] in critter_set \
                    and zone:
                add(o["unit"], {"k": "spawn", "zones": [zone],
                                "chance": 1.0})
            for v in o.values():
                walk(v, zone)
        elif isinstance(o, list):
            for v in o:
                walk(v, zone)

    for _path, level in _levels(game_dir):
        walk(level, None)

    # -- loot tables -> the collectibles they can give
    for tid, users in table_users.items():
        for iid, p in _table_items(tables, tid).items():
            if iid not in collectible or p <= 0:
                continue
            for u in users:
                add(iid, dict(u, chance=round(p, 6)))

    # -- recipes that make a collectible (crafted armour)
    for r in sheets["craft"].get("lines") or ():
        if r.get("item") in collectible:
            add(r["item"], {"k": "craft", "job": r.get("job"),
                            "lvl": r.get("level"), "n": r.get("count") or 1,
                            "input": [[i.get("item"), i.get("count") or 1]
                                      for i in r.get("input") or ()]})

    # -- critters: their spawn groups, by weight, and where those stand
    for gid, g in groups.items():
        comp = g.get("composition") or []
        total = sum(float(c.get("weight") or 0) for c in comp) or 1.0
        for c in comp:
            for m in c.get("group") or ():
                uid = m.get("unit")
                if uid in critter_set:
                    add(uid, {"k": "spawn",
                              "zones": sorted(group_zones.get(gid, ())),
                              "rift": gid.startswith("Rift_"),
                              "chance": round(float(c.get("weight") or 0)
                                              / total, 4)})

    # -- armour generated rather than listed: faction pieces and WorldLoot
    for iid in wanted["gears"]:
        row = items[iid]
        if row.get("faction") in FACTIONS:
            add(iid, {"k": "faction", "f": row["faction"]})
        elif (row.get("flags") or 0) & world_loot_flag:
            add(iid, {"k": "world", "lvl": row.get("level") or 1})

    def entry(iid, row):
        e = {"id": iid, "rarity": row.get("rarity") or "",
             "src": src.get(iid, [])}
        if iid in gear_set:
            e["slot"] = row.get("type")
            e["apt"] = [a.get("ref") for a in row.get("aptitudes") or ()
                        if a.get("ref")]
            e["lvl"] = row.get("level")
        return e

    out = {cat: [entry(i, items[i]) for i in ids]
           for cat, ids in wanted.items()}
    out["pets"] = [entry(u, units[u]) for u in critters]
    out["factions"] = {
        f: {"dungeons": sorted(a for a, k in fac_acts[f].items()
                               if k == "Dungeon"),
            "acts": {k: sum(1 for v in fac_acts[f].values() if v == k)
                     for k in sorted(set(fac_acts[f].values()) - {"Dungeon"})},
            "chests": len(fac_chests[f])}
        for f in FACTIONS}

    if img_dir is not None:
        _images(game_dir, img_dir,
                {i: items[i].get("gfx") for ids in wanted.values()
                 for i in ids} | {u: units[u].get("gfx") for u in critters})
    return out


def _images(game_dir, out_dir, gfx):
    """Each collectible's picture, out_dir/<id>.webp."""
    from PIL import Image
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pak = game_dir / "res.pak"
    with open(pak, "rb") as f:
        header = struct.unpack_from("<i", f.read(12), 4)[0]
        f.seek(0)
        entries, off = pak_extract.read_tree(f.read(header), pak.name)
        by_path = {e.path: e for e in entries}
        cache = {}
        for iid, g in gfx.items():
            e = by_path.get(g.get("file")) if isinstance(g, dict) else None
            if e is None:
                continue
            if e.path not in cache:
                f.seek(off + e.pos)
                try:
                    cache[e.path] = Image.open(
                        io.BytesIO(f.read(e.size))).convert("RGBA")
                except Exception:
                    cache[e.path] = None
            img = cache[e.path]
            if img is None:
                continue
            n = int(g.get("size") or img.width)
            x, y = int(g.get("x") or 0) * n, int(g.get("y") or 0) * n
            if x + n > img.width or y + n > img.height:
                continue
            tile = img.crop((x, y, x + n, y + n)).resize((IMG_PX, IMG_PX),
                                                         Image.LANCZOS)
            imgcache.save(tile, out_dir / f"{iid}.webp", quality=82, method=4)
