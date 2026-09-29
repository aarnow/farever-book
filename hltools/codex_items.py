"""The item codex's catalogue: the items the game counts in
Progress.itemProgress — crafting components, plus the raw ores, cloth and
leather (measured 2026-09-28: 81 entries on a character, every one of these
types) — with how each is obtained, from data.cdb:

* dropped: the loot tables of monster families (unitType.lootTable) and of
  single units (a unit's lootTable / bossLootTable), with their chances;
* gathered: a gatherable's loot / hitLoot table (ore lodes, plants...);
* crafted: the craft sheet's recipes that make it (job, level, inputs);
and how many recipes use it."""
import json
from collections import defaultdict
from pathlib import Path

import pak_extract
from collection_data import _images, _table_items

TYPES = ("CraftingComponent", "Ore", "Cloth", "Leather")


def build(game_dir, img_dir=None):
    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}

    def rows(name):
        return {ln["id"]: ln for ln in sheets[name].get("lines") or ()
                if isinstance(ln.get("id"), str)}

    items, units = rows("item"), rows("unit")
    tables, gathers = rows("lootTable"), rows("gatherable")
    wanted = [iid for iid, r in items.items() if r.get("type") in TYPES]
    wanted_set = set(wanted)
    src = defaultdict(list)

    # who rolls which table
    users = defaultdict(list)
    for tid, r in rows("unitType").items():
        if r.get("lootTable"):
            users[r["lootTable"]].append({"k": "family", "id": tid})
    for uid, r in units.items():
        props = r.get("props") or {}
        for key in ("lootTable", "bossLootTable"):
            if props.get(key):
                users[props[key]].append({"k": "unit", "id": uid})

    def gather_root(gid):
        """A gatherable's own row, or the first ancestor's with loot."""
        seen = set()
        while gid in gathers and gid not in seen:
            seen.add(gid)
            g = gathers[gid]
            if g.get("loot") or g.get("hitLoot"):
                return g
            gid = g.get("inherit")
        return {}

    # the Spark recycler (the game's ScrapStation) rolls these two
    for tid in ("Scrap", "Scrap_Rare"):
        if tid in tables:
            users[tid].append({"k": "scrap", "id": tid})

    for gid, g in gathers.items():
        root = gather_root(gid)
        for key in ("loot", "hitLoot"):
            tid = g.get(key) or root.get(key)
            if tid:
                users[tid].append({"k": "gather", "id": gid})

    for tid, us in users.items():
        for iid, p in _table_items(tables, tid).items():
            if iid in wanted_set and p > 0:
                for u in us:
                    s = dict(u, chance=round(p, 6))
                    if s not in src[iid]:
                        src[iid].append(s)

    # combining: an item whose props.completable turns N of one kind into
    # another (5 water motes -> 1 water fragment)
    for iid, r in items.items():
        comp = (r.get("props") or {}).get("completable") or {}
        for rew in comp.get("reward") or ():
            if rew.get("kind") in wanted_set:
                src[rew["kind"]].append({
                    "k": "combine",
                    "from": [[q.get("kind"), q.get("amount") or 1]
                             for q in comp.get("require") or ()]})

    # salvaging gear (the constants' item tiers): resources by the gear's
    # level, one enchanting material by its rarity
    consts = rows("constant")
    salv = defaultdict(lambda: {"lvl": [99, 0], "rarity": None})
    for tier in (((consts.get("Tiers") or {}).get("v") or {}).get("other")
                 or {}).get("itemTiers") or ():
        rng = (tier.get("level") or {}).get("range") or {}
        lo, hi = rng.get("min") or 1, rng.get("max") or 25
        picks = [(r.get("ref"), None) for r in tier.get("resources") or ()]
        picks += [(e.get("item"), e.get("rarity"))
                  for e in tier.get("enchant") or ()]
        for iid, rar in picks:
            if iid in wanted_set:
                s = salv[iid]
                s["lvl"] = [min(s["lvl"][0], lo), max(s["lvl"][1], hi)]
                s["rarity"] = rar
    for iid, s in salv.items():
        src[iid].append({"k": "salvage", "lvl": s["lvl"],
                         "rarity": s["rarity"]})

    # recipes: what makes each item, and how many use it
    uses = defaultdict(int)
    for r in sheets["craft"].get("lines") or ():
        out = r.get("item")
        for inp in r.get("input") or ():
            if inp.get("item") in wanted_set:
                uses[inp["item"]] += 1
        if out in wanted_set:
            src[out].append({"k": "craft", "job": r.get("job"),
                             "lvl": r.get("level"),
                             "n": r.get("count") or 1,
                             "input": [[i.get("item"), i.get("count") or 1]
                                       for i in r.get("input") or ()]})

    out = [{"id": iid, "rarity": items[iid].get("rarity") or "",
            "type": items[iid].get("type"), "src": src.get(iid, []),
            "uses": uses.get(iid, 0)} for iid in wanted]
    if img_dir is not None:
        _images(game_dir, img_dir, {iid: items[iid].get("gfx")
                                    for iid in wanted})
    return out
