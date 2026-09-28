"""The bestiary: every monster the game places in its world, dungeons and
rifts, for the app's hunting log. The kill counts themselves are the game's
own (Progress.unitsProgress, read by the hook); this is the list to count
against.

A monster is a unit some level spawns — a spawner's `unit`, or the units of
its `unitGroup` — minus what is not a monster: companions (Critter), mounts,
totems, scenery, and the units data.cdb keeps out of the codex. Each comes
with its family (unit.type -> unitType), the zones it spawns in and their
region, and its codex tier (codex_units.json: elite / big / foe)."""
import json
import re
import struct
from collections import defaultdict
from pathlib import Path

import pak_extract
from collection_data import _levels

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

    def region(z):
        seen = set()
        while z in zones and z not in seen:
            seen.add(z)
            if zones[z].get("type") == 0:
                return z
            z = zones[z].get("parent")
        return None

    spawned = defaultdict(set)          # unit -> zones

    def walk(o, zone):
        if isinstance(o, dict):
            props = o.get("props") if isinstance(o.get("props"), dict) else {}
            zone = props.get("zoneBaked") or o.get("zoneBaked") or zone
            if o.get("$cdbtype") == "spawner" or "unitGroup" in o:
                if isinstance(o.get("unit"), str):
                    spawned[o["unit"]].add(zone)
                g = groups.get(o.get("unitGroup"))
                for c in (g or {}).get("composition") or ():
                    for m in c.get("group") or ():
                        if m.get("unit"):
                            spawned[m["unit"]].add(zone)
            for v in o.values():
                walk(v, zone)
        elif isinstance(o, list):
            for v in o:
                walk(v, zone)

    for _path, level in _levels(game_dir):
        walk(level, None)

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

    if img_dir is not None:
        type_gfx = {tid: r.get("gfx") for tid, r in rows("unitType").items()}
        gfx = {uid: units[uid].get("gfx") for uid in every}
        gfx.update({f"family_{f}": type_gfx.get(f) for f in families})
        _images(game_dir, img_dir, gfx)
    return {"placed": placed, "units": every, "families": families}


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
            img.crop((x, y, x + n, y + n)).resize(
                (IMG_PX, IMG_PX), Image.LANCZOS).save(
                out_dir / f"{uid}.webp", quality=80, method=6)
