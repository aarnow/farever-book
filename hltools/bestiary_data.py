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
  item with its chance per kill (collection_data._table_items).
* where else: a wave spawner's units (`waveSpawner.group`: the open
  world's fight stones, the rifts, a boss's adds) count as spawned too, at
  the spawner; a unit spawned in an instance (dungeon, boss, rift level)
  keeps that instance's activity, whose entrance in the open world (an
  element with a `targetActivity`: the instance orbs, the rift entrances)
  is placed like a spawner; and a unit no level spawns names who summons it
  (a skill or unit script that refers to it).
* summoning altars: an element that spawns a unit when used (`spawnUnit`,
  the soulstone altars) stands where it spawns it, and its `interactible`
  cost names the item it takes (the soulstone)."""
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
    inside = defaultdict(set)           # unit -> instance levels it spawns in
    level_act = {}                      # instance level -> its activity
    doors = defaultdict(set)            # activity -> {(x, y, zone)}, entrance
    keys = defaultdict(set)             # unit -> items its altar takes
    loading = {}                        # activity -> its loading screen

    def walk(o, zone, px, py, rot, world, lvl):
        if isinstance(o, dict):
            props = o.get("props") if isinstance(o.get("props"), dict) else {}
            zone = props.get("zoneBaked") or o.get("zoneBaked") or zone
            x, y = o.get("x"), o.get("y")
            wx, wy, wr = px, py, rot
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                wx, wy = px + x * c - y * s, py + x * s + y * c
                wr = rot + float(o.get("rotationZ") or 0)
            if o.get("$cdbtype") == "activity" and not world:
                level_act[lvl] = o.get("id")
            if o.get("$cdbtype") == "activity":
                ls = props.get("loadingScreen") or o.get("loadingScreen")
                if isinstance(ls, str) and isinstance(o.get("id"), str):
                    loading[o["id"]] = ls
            if world and isinstance(o.get("targetActivity"), str):
                doors[o["targetActivity"]].add((round(wx), round(wy),
                                                zone or ""))
            # an altar: used (and paid), it spawns its unit where it stands
            altar = o.get("spawnUnit")
            if isinstance(altar, dict) and isinstance(altar.get("unit"), str):
                u = altar["unit"]
                spawned[u].add(zone)
                if world:
                    spots[u].add((round(wx), round(wy), zone or ""))
                for c in ((o.get("interactible") or {}).get("cost") or ()):
                    if c.get("item"):
                        keys[u].add(c["item"])
            wave = o.get("waveSpawner")
            if o.get("$cdbtype") == "spawner" or "unitGroup" in o \
                    or isinstance(wave, dict):
                here = []
                if isinstance(o.get("unit"), str):
                    here.append(o["unit"])
                g = groups.get(o.get("unitGroup")
                               or (wave or {}).get("group"))
                for c in (g or {}).get("composition") or ():
                    for m in c.get("group") or ():
                        if m.get("unit"):
                            here.append(m["unit"])
                for u in here:
                    spawned[u].add(zone)
                    if world:
                        spots[u].add((round(wx), round(wy), zone or ""))
                    else:
                        inside[u].add(lvl)
            for k, v in o.items():
                # an object's props and children stand where it does
                if k in ("children", "props"):
                    walk(v, zone, wx, wy, wr, world, lvl)
                else:
                    walk(v, zone, px, py, rot, world, lvl)
        elif isinstance(o, list):
            for v in o:
                walk(v, zone, px, py, rot, world, lvl)

    for path, level in _levels(game_dir):
        walk(level, None, 0.0, 0.0, 0.0,
             path.startswith("Level/World/W1_Siagarta.dat/gameplayData/"),
             re.sub(r"\.dat/.*|\.prefab$", "", path).split("/")[-1])

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
        zs = sorted(z for z in spawned.get(uid, ()) if z)
        every[uid] = {"family": units[uid].get("type") or "",
                      "tier": tier(uid),
                      "region": "rift" if "Rift" in uid or "Portal" in uid
                      else f"Z{m.group(1)}_Region" if m else "",
                      "zones": zs,
                      "regions": sorted({r for r in map(region, zs) if r})}
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

    # who summons the units no level spawns: the owners of a skill that
    # names it, and the units whose script or props name it
    owners = defaultdict(set)
    for uid, u in units.items():
        for sk in u.get("skills") or ():
            owners[sk.get("skill")].add(uid)
    loose = [u for u in every if u not in spawned]
    by = defaultdict(set)
    for sid, sk in rows("skill").items():
        text = json.dumps(sk)
        for u in loose:
            if f'"{u}"' in text or f"Unit.{u}" in text:
                by[u] |= owners.get(sid, set())
    for uid, u in units.items():
        text = json.dumps(u.get("script") or "") + json.dumps(u.get("props"))
        for t in loose:
            if t != uid and (f'"{t}"' in text or f"Unit.{t}" in text):
                by[t].add(uid)
    where = {}
    for uid in every:
        acts = sorted({level_act.get(lv, lv) for lv in inside.get(uid, ())})
        sums = sorted(s for s in by.get(uid, ()) if s != uid and s in units)
        if acts or sums or keys.get(uid):
            where[uid] = {"acts": acts, "by": sums,
                          "items": sorted(keys.get(uid, ()))}
    entrances = {a: sorted([x, y, z] for x, y, z in pts)
                 for a, pts in doors.items()}

    # each region's first illustration in the game's codex (Z2: the sunset
    # over the mill): the picture of a dungeon that has none of its own
    region_art = {}
    for cid, c in rows("codexCategory").items():
        for f in re.findall(r'"file": *"([^"]*LoadingScreen/Background/[^"]+)"',
                            json.dumps(c)):
            region_art.setdefault(cid, f)
            break

    if img_dir is not None:
        type_gfx = {tid: r.get("gfx") for tid, r in rows("unitType").items()}
        gfx = {uid: units[uid].get("gfx") for uid in every}
        gfx.update({f"family_{f}": type_gfx.get(f) for f in families})
        _images(game_dir, img_dir, gfx)
        # the screens the instances name, and the ones named after a boss
        # that no instance claims (Munster_Chuck: the Gorgon's Hollow's)
        named = {e.path for e in _pak_entries(game_dir)
                 if e.path.startswith("UI/Window/LoadingScreen/Background/")
                 and not re.search(r"/(loading_screen\d+|Default)\.png$", e.path)}
        screens = set(loading.values()) | named | set(region_art.values())
        _backdrops(game_dir, Path(img_dir).parent / "dungeon_bg", screens,
                   full=screens)
    # each monster's line of descent (itself, then what it inherits from):
    # a variant's description and faction are often its base monster's
    def chain(uid):
        out, todo = [], [uid]
        while todo:
            u = todo.pop(0)
            if u in out or u not in units:
                continue
            out.append(u)
            todo += [i.get("ref") for i in units[u].get("inherit") or () if i.get("ref")]
        return out
    chains = {uid: chain(uid) for uid in every}
    factions = {}
    for uid, ch in chains.items():
        f = next((units[u].get("faction") for u in ch if units[u].get("faction")), None)
        if f:
            factions[uid] = f
    return {"placed": placed, "units": every, "families": families,
            "spawns": spawns, "lvl": lvls, "famLoot": fam_loot,
            "unitLoot": unit_loot, "where": where, "entrances": entrances,
            "chain": {u: c for u, c in chains.items() if len(c) > 1},
            # an instance's loading screen (a dungeon's own, the rifts'),
            # by the file's name in dungeon_bg/
            "loading": {a: Path(p).stem for a, p in sorted(loading.items())},
            "regionArt": {r: Path(p).stem for r, p in sorted(region_art.items())},
            "faction": factions}


BACKDROP_W = 960


def _pak_entries(game_dir):
    pak = Path(game_dir) / "res.pak"
    with open(pak, "rb") as f:
        header = struct.unpack_from("<i", f.read(12), 4)[0]
        f.seek(0)
        return pak_extract.read_tree(f.read(header), pak.name)[0]


def _backdrops(game_dir, out_dir, paths, full=()):
    """The loading screens (1920x1080), halved and in WebP: backgrounds for
    the app's dungeon cards, under the app's 2 MB page. Those in `full` get
    a full-size copy too, "<name>_hd", for behind a whole page (the Rifts
    tab, a dungeon's): at half size it blurs."""
    import io
    from PIL import Image
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(paths):
        raw = pak_extract.read_entry(game_dir / "res.pak", path)
        if not raw:
            continue
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            continue
        h = round(img.height * BACKDROP_W / img.width)
        imgcache.save(img.resize((BACKDROP_W, h), Image.LANCZOS),
                      out_dir / f"{Path(path).stem}.webp", quality=70, method=6)
        if path in full:
            imgcache.save(img, out_dir / f"{Path(path).stem}_hd.webp",
                          quality=82, method=6)


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
