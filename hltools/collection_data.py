"""The collection catalogue: every mount, glider, companion and armour
appearance, with how each is obtained, for the app's Collection tab — and
every item of the game with its sources, what it is used for and what it
holds, for the Encyclopedia tab ("encyclo").

Sources: data.cdb (items, Critter units, loot tables and who rolls them,
achievement rewards, unitGroup spawn weights, starting gliders) and the
levels (HBSON prefabs: shops, placed chests, spawners, with zoneBaked).

A loot table flagged Weights gives ONE line, by weight; otherwise each line
rolls on its own. Nested tables multiply."""
import io
import json
import math
import struct
from collections import defaultdict
from pathlib import Path

import hbson
import imgcache
import pak_extract
from build_data import JEWEL

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
    # what a build can wear (weapons, jewels...): its sources too, for the
    # Build tab's "where to find it"
    equip = {iid for iid, r in items.items()
             if r.get("aptitudes") or r.get("type") in JEWEL}
    # the caches (opened from the bag): what they give, at which rarity;
    # tracked too, for who sells them
    caches = {iid: r for iid, r in items.items()
              if r.get("type") == "LootableContainer"
              and ((r.get("props") or {}).get("gainItem") or {}).get("lootTable")}
    # every item: the Encyclopedia lists them all, with their sources
    tracked = set(items)
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
            if it.get("item") in tracked:
                add(it["item"], {"k": "ach", "id": aid, "chain": chain,
                                 "v": v})

    # -- the levels: merchants, chests, critter spawners
    group_zones = defaultdict(set)
    fac_acts = defaultdict(dict)        # faction -> {activity id: kind}
    fac_chests = defaultdict(set)       # faction -> {chest id}

    # world coordinates, as the Map tab places its points (map_data.py): a
    # child is placed through its parent (offset, rotation in degrees); only
    # the world level's are on the map
    in_world = [False]
    prefab_npcs = {}
    zones = rows("zone")

    def region(z):
        """A zone's region (its first ancestor of type 0), or None."""
        seen = set()
        while z in zones and z not in seen:
            seen.add(z)
            if zones[z].get("type") == 0:
                return z
            z = zones[z].get("parent")
        return None

    def chest_entry(o, zone, wx, wy, **more):
        """A chest as a source: in the open world, where it stands and its
        region (the map's pin)."""
        e = {"k": "chest", "id": o.get("name") or "Chest", "zone": zone,
             **more}
        if in_world[0]:
            e["at"] = [round(wx, 1), round(wy, 1)]
            e["region"] = region(zone) or ""
        else:
            e["lv"] = cur_level[0]      # its level: the dungeon, once known
        return e

    cur_level = [""]
    level_dungeon = {}                  # level path -> its dungeon

    def prefab_npc(source):
        """The NPC element a prefab places (its id, English name and unit):
        a merchant placed by reference names itself there, its translation
        keyed by that id (MountTamer.prefab: MountTamer_NPC)."""
        if source not in prefab_npcs:
            found = None
            try:
                raw = pak_extract.read_entry(game_dir / "res.pak", source)
                stack = [hbson.loads(raw)] if raw and raw[:5] == b"HBSON" else []
            except Exception:
                stack = []
            while stack and found is None:
                o = stack.pop()
                if isinstance(o, dict):
                    if o.get("$cdbtype") == "element" and isinstance(
                            (o.get("props") or {}).get("npc"), dict):
                        found = {"el": o.get("id"),
                                 "eln": (o.get("texts") or {}).get("name"),
                                 "npc": o["props"]["npc"].get("unit")}
                    stack.extend(o.values())
                elif isinstance(o, list):
                    stack.extend(o)
            prefab_npcs[source] = found
        return prefab_npcs[source]

    def walk(o, zone, px=0.0, py=0.0, rot=0.0, ref=None):
        if isinstance(o, dict):
            props = o.get("props") if isinstance(o.get("props"), dict) else {}
            zone = props.get("zoneBaked") or o.get("zoneBaked") or zone
            x, y = o.get("x"), o.get("y")
            wx, wy, wr = px, py, rot
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                wx, wy = px + x * c - y * s_, py + x * s_ + y * c
                wr = rot + float(o.get("rotationZ") or 0)
            # inside a prefab placed by reference: what it places
            if o.get("type") == "reference" and str(
                    o.get("source") or "").endswith(".prefab"):
                ref = o["source"]
            fac = props.get("faction") or o.get("faction")
            oid = o.get("id")
            if o.get("$cdbtype") == "activity" and o.get("inherit") == \
                    "Dungeon" and isinstance(oid, str):
                level_dungeon.setdefault(cur_level[0], oid)
            if fac in FACTIONS and isinstance(oid, str):
                if o.get("$cdbtype") == "activity":
                    fac_acts[fac][oid] = o.get("inherit") or "?"
                elif o.get("$cdbtype") == "element" and "Chest" in oid:
                    fac_chests[fac].add(oid)
            # the name the game shows over a merchant is the element's
            # ("Mira, Demon Huntress"), not its unit's
            el_name = (o.get("texts") or {}).get("name") \
                if isinstance(o.get("texts"), dict) else None
            # the developers' preview merchants ("Major Update Preview
            # Merchant (debug)") sell what no player can buy
            if isinstance(props.get("shop"), list) \
                    and "(debug)" not in str(el_name or ""):
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
                    if el_name and isinstance(o.get("id"), str):
                        entry["el"], entry["eln"] = o["id"], el_name
                    elif ref and prefab_npc(ref):
                        # unnamed here: the prefab's NPC, its name and unit
                        p = prefab_npc(ref)
                        if p.get("el") and p.get("eln"):
                            entry["el"], entry["eln"] = p["el"], p["eln"]
                        entry["npc"] = entry["npc"] or p.get("npc")
                    if in_world[0]:
                        entry["at"] = [round(wx, 1), round(wy, 1)]
                    if iid in tracked:
                        add(iid, entry)
                    if pet in critter_set:
                        add(pet, entry)
            if props.get("lootTable") and isinstance(props["lootTable"], str):
                table_users[props["lootTable"]].append(
                    chest_entry(o, zone, wx, wy))
            for li in props.get("lootItems") or ():
                if isinstance(li, dict) and li.get("item") in tracked:
                    rate = li.get("dropRate")
                    add(li["item"], chest_entry(
                        o, zone, wx, wy,
                        chance=float(rate) if rate else 1.0))
            if isinstance(o.get("unitGroup"), str) and zone:
                group_zones[o["unitGroup"]].add(zone)
            if isinstance(o.get("unit"), str) and o["unit"] in critter_set \
                    and zone:
                add(o["unit"], {"k": "spawn", "zones": [zone],
                                "chance": 1.0})
            for k, v in o.items():
                # its children, and the elements in its props (a merchant),
                # are where it is
                if k in ("children", "props"):
                    walk(v, zone, wx, wy, wr, ref)
                else:
                    walk(v, zone, px, py, rot, ref)
        elif isinstance(o, list):
            for v in o:
                walk(v, zone, px, py, rot, ref)

    for path, level in _levels(game_dir):
        in_world[0] = path.startswith("Level/World/W1_Siagarta.dat/gameplayData/")
        # a level's parts share its folder (….dat/gameplayData/…)
        cur_level[0] = path.split(".dat/")[0]
        walk(level, None)

    # -- a chest in an instance's level: that dungeon's, when it is one
    def placed(s):
        if s.get("k") == "chest" and "lv" in s:
            s = dict(s)
            dg = level_dungeon.get(s.pop("lv"))
            if dg:
                s["dg"] = dg
        return s
    for users in table_users.values():
        users[:] = [placed(u) for u in users]
    for iid in list(src):
        src[iid] = [placed(s) for s in src[iid]]

    # -- loot tables -> the collectibles they can give
    for tid, users in table_users.items():
        for iid, p in _table_items(tables, tid).items():
            if iid not in tracked or p <= 0:
                continue
            for u in users:
                add(iid, dict(u, chance=round(p, 6)))

    # -- caches: each piece they can give, at their forced rarity
    for cid, r in caches.items():
        gi = r["props"]["gainItem"]
        lv = gi.get("levelRange") or {}
        for iid, p in _table_items(tables, gi["lootTable"]).items():
            if iid in equip and p > 0:
                add(iid, {"k": "cache", "id": cid,
                          "rar": (gi.get("rarity") or {}).get("min"),
                          "lvl": [lv["min"], lv["max"]] if lv else None})

    # -- recipes that make a collectible (crafted armour)
    for r in sheets["craft"].get("lines") or ():
        if r.get("item") in tracked:
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
    for iid in sorted(gear_set | equip):
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
    out["equip"] = {i: src[i] for i in sorted(equip - gear_set) if src.get(i)}
    out["caches"] = {c: {"rar": r.get("rarity"), "src": src.get(c, [])}
                     for c, r in caches.items()}

    # -- the Encyclopedia: every item the game names (a template, ItemShell,
    # is none), with its sources, the recipes it goes into, and what a
    # container holds
    shell_flag = flag_bit(sheets, "item", "ItemShell")
    used = defaultdict(list)
    for r in sheets["craft"].get("lines") or ():
        for i in r.get("input") or ():
            if i.get("item") and r.get("item"):
                used[i["item"]].append([r["item"], i.get("count") or 1,
                                        r.get("job"), r.get("level")])
    encyclo = {}
    for iid, r in items.items():
        if not (r.get("texts") or {}).get("name") \
                or (r.get("flags") or 0) & shell_flag:
            continue
        e = {"t": r.get("type") or "", "r": r.get("rarity") or "",
             "src": src.get(iid, [])}
        for key, col in (("l", "level"), ("p", "sellPrice"),
                         ("f", "faction")):
            if r.get(col):
                e[key] = r[col]
        if r.get("aptitudes"):
            e["apt"] = [a.get("ref") for a in r["aptitudes"] if a.get("ref")]
        if used.get(iid):
            e["in"] = used[iid]
        gi = (r.get("props") or {}).get("gainItem") or {}
        if gi.get("lootTable"):
            lv = gi.get("levelRange") or {}
            e["gives"] = {"items": {i: round(p, 6) for i, p in
                                    _table_items(tables, gi["lootTable"]).items()
                                    if p > 0},
                          "rar": (gi.get("rarity") or {}).get("min"),
                          "lvl": [lv.get("min"), lv.get("max")] if lv else None}
        encyclo[iid] = e
    out["encyclo"] = encyclo
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
            # a tile may span several cells (width, height); size 1: pixels
            w, h = int(g.get("width") or 1) * n, int(g.get("height") or 1) * n
            if x + w > img.width or y + h > img.height:
                continue
            tile = img.crop((x, y, x + w, y + h))
            if w != h:                  # centred on a square, not stretched
                sq = Image.new("RGBA", (max(w, h),) * 2, (0, 0, 0, 0))
                sq.paste(tile, ((max(w, h) - w) // 2, (max(w, h) - h) // 2))
                tile = sq
            tile = tile.resize((IMG_PX, IMG_PX), Image.LANCZOS)
            imgcache.save(tile, out_dir / f"{iid}.webp", quality=82, method=4)
