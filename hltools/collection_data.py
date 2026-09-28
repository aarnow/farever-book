"""The collection catalogue: every mount, glider and companion in the game,
with how each is obtained — for the app's Collection tab.

Everything comes from the game's own files:

* data.cdb (res.light.pak): the items (type Mount / GearGlider), the
  companions (units of type Critter), the loot tables and who rolls them
  (unitType.lootTable = every foe of that family; a unit's own lootTable /
  bossLootTable), the achievements' rewards, the critter spawn groups
  (unitGroup, with weights), each class's starting glider;
* the levels (.prefab, HBSON — see hbson.py): the merchants (props.shop), the
  chests placed in the world (props.lootTable / props.lootItems), the critter
  spawners (props.unitGroup), each with the zone it stands in (zoneBaked).

Chances are the data's: a table flagged Weights gives ONE of its lines, by
weight; otherwise each line rolls on its own. Nested tables multiply."""
import io
import json
import struct
from collections import defaultdict
from pathlib import Path

import hbson
import pak_extract

CATEGORIES = (("mounts", "Mount"), ("gliders", "GearGlider"))
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
    collectible = {iid for ids in wanted.values() for iid in ids}
    # A capturable companion has a name; the unnamed Critter units are
    # scenery (YellowRabbits).
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
                    add(iid, {"k": "starter", "cls": CLASS_UNITS[uid]})

    # -- achievements. A tier ("collect 25 mounts") has no name or text of
    # its own: they are its parent's, with the tier's target value.
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

    def walk(o, zone):
        if isinstance(o, dict):
            props = o.get("props") if isinstance(o.get("props"), dict) else {}
            zone = props.get("zoneBaked") or o.get("zoneBaked") or zone
            if isinstance(props.get("shop"), list):
                npc = ((props.get("npc") or {}).get("unit")
                       if isinstance(props.get("npc"), dict) else None)
                for s in props["shop"]:
                    iid = s.get("item") if isinstance(s, dict) else None
                    pet = iid[len("Critter_"):] if iid and \
                        iid.startswith("Critter_") else None
                    cost = [{"item": c.get("item"), "n": c.get("qty")
                             or c.get("count") or c.get("amount")}
                            for c in s.get("cost") or () if isinstance(c, dict)]
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

    def entry(iid, row):
        return {"id": iid, "rarity": row.get("rarity") or "",
                "src": src.get(iid, [])}

    out = {cat: [entry(i, items[i]) for i in ids]
           for cat, ids in wanted.items()}
    out["pets"] = [entry(u, units[u]) for u in critters]

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
            tile.save(out_dir / f"{iid}.webp", quality=82, method=6)
