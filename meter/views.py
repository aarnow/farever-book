"""Page builders: game data and saved state turned into the node specs the
window renders (collection, bestiary, achievements, rift rewards, character,
map, loot tables)."""
from __future__ import annotations

import math
import re
import time

from common import dec_sep, pct_sp, ANALYSIS, _n, _pretty_id, class_key, date_fr
from gamedata import (
    weapon_upgrade, weapon_upgrade_skill,
    RARITY_ORDER, _augments_data, _codex_thresholds, _element_done, build_data,
    _family_label, _fr_desc, _fr_names, _fr_ref, _item_flag, _skill_label,
    _spark_units, _unit_label, _zone_label, achievements_catalogue,
    bestiary_catalogue, codex_items_catalogue, collection_catalogue,
    dungeon_catalogue, dungeon_name, faction_label, item_icon, item_label,
    item_rarity, item_type, item_type_label, luck_data, rarity_label,
    rift_rewards_data, skill_tip, talent_data, world_map)
from gearstats import (
    EQUIP_SLOTS, HERO_SLOTS, SHEET_LEFT, SHEET_RIGHT, SLOT_ICON, _equip_by_slot,
    _gear_infusion, _hero_sheet, _infusion_sets, _scaled, gear_stats,
    infusion_bonus, slot_factor)
from combat import DUNGEON_DIFFICULTIES
from riftspot import RIFT_ACT, SPOTS
from i18n import tr


COLLECTION_CATS = (("mounts", "Montures", "monture"),
                   ("gliders", "Planeurs", "planeur"),
                   ("pets", "Compagnons", "compagnon"),
                   ("gears", "Équipements", "équipement"),
                   ("items", "Objets", "objet"))


# armour appearance slots, in the game's order
GEAR_SLOTS = (("Head", "Tête"), ("Shoulders", "Épaules"), ("Chest", "Torse"),
              ("Hands", "Mains"), ("Waist", "Taille"), ("Legs", "Jambes"),
              ("Feet", "Pieds"), ("Back", "Dos"))


# a piece's aptitudes -> the classes that wear it (unit.props.aptitudes:
# Warrior Fighter, Rogue Assassin, Mage Wizard, Priest Cleric); none = all
APTITUDE_CLASSES = {"Fighter": "warrior", "Assassin": "rogue",
                    "Wizard": "mage", "Cleric": "priest"}


GEAR_CLASSES = ("warrior", "mage", "rogue", "priest")


FACTION_ACTS = (("WorldElite", "{n} élite", "{n} élites"),
                ("FightStone", "{n} pierre de combat", "{n} pierres de combat"),
                ("ChestOrb", "{n} orbe à coffre", "{n} orbes à coffre"),
                ("WorldCamp", "{n} camp", "{n} camps"),
                ("TimerCollectRun", "{n} course", "{n} courses"),
                ("Ascension", "{n} ascension", "{n} ascensions"))


# the item codex's rank steps (codex_units.json "item": 1, 5, 25, 50)
ITEM_RANKS = 4


# the item codex's kinds (hltools/codex_items.py: from the models' folders)
ITEM_CATS = (("ore", "Minerais et gemmes"), ("metal", "Métaux"),
             ("plant", "Plantes"), ("cloth", "Tissus et cuirs"),
             ("creature", "Parties de créatures"),
             ("magic", "Magie et Étincelle"), ("cook", "Cuisine"),
             ("misc", "Divers"))


CLASS_LABELS = {"warrior": "Guerrier", "mage": "Mage", "rogue": "Voleur",
                "priest": "Prêtre"}


CHEST_LABELS = {"VaultChest": "Coffre-fort", "WorldChest": "Coffre",
                "BossChest": "Coffre du boss"}


def _source_text(s, bosses):
    """One way to obtain a collectible, in French."""
    k, ch = s.get("k"), s.get("chance")
    pct = ("" if ch is None else " — " + tr("garanti") if ch >= 1
           else f" — {_pct(ch)}")
    if k == "family":
        name = _fr_names("unitType").get(s.get("id")) or _pretty_id(s["id"])
        return tr("Butin des ennemis : {name}", name=name) + pct
    if k == "unit":
        dg = bosses.get(s.get("id"))
        where = f" ({dungeon_name(dg)})" if dg else ""
        return tr("Butin de {unit}", unit=_unit_label(s.get("id"))) \
            + where + pct
    if k == "chest":
        kind = tr(CHEST_LABELS.get(s.get("id"), "Coffre"))
        zone = f" — {_zone_label(s['zone'])}" if s.get("zone") else ""
        return f"{kind}{zone}{pct}"
    if k == "shop":
        who = _seller(s)
        zone = f" — {_zone_label(s['zone'])}" if s.get("zone") else ""
        cost = ", ".join(f"{c['n']} × {item_label(c['item'])}"
                         if c.get("n") else item_label(c["item"])
                         for c in s.get("cost") or () if c.get("item"))
        return tr("Vendu par {who}", who=who) + zone + (
            " " + tr("(prix : {cost})", cost=cost) if cost else "")
    if k == "ach":
        chain = s.get("chain") or [s.get("id")]
        name = next((_fr_names("ach").get(a) for a in chain
                     if _fr_names("ach").get(a)), None)
        desc = next((_fr_desc("ach").get(a) for a in chain
                     if _fr_desc("ach").get(a)), "")
        v = s.get("v")
        desc = _fr_ref(desc.replace("::targetValue::", str(v)
                                    if v is not None else "…"))
        name = _fr_ref(name) if name else desc or _pretty_id(s.get("id"))
        if name and v and len(chain) > 1:
            name = f"{name} ({v})"
        return (tr("Succès « {name} » : {desc}", name=name, desc=desc)
                if desc and desc != name else tr("Succès « {name} »", name=name))
    if k == "starter":
        cls = tr(CLASS_LABELS.get(s.get("cls"), "?"))
        return (tr("Équipement de départ du {cls}", cls=cls) if s.get("gear")
                else tr("Planeur de départ du {cls}", cls=cls))
    if k == "world":
        lvl = s.get("lvl") or 1
        return tr("Butin aléatoire du monde (ennemis, coffres, activités) "
                  "de niveau {lo} à {hi}", lo=max(1, lvl - 1), hi=lvl + 2)
    if k == "faction":
        f = s.get("f")
        info = (collection_catalogue().get("factions") or {}).get(f) or {}
        name = faction_label(f)
        dgs = [_fr_names("activity").get(a) or _pretty_id(a)
               for a in info.get("dungeons") or ()]
        acts = [tr(one if n == 1 else many, n=n)
                for key, one, many in FACTION_ACTS
                for n in [(info.get("acts") or {}).get(key)] if n]
        n = info.get("chests") or 0
        lines = [tr("Butin de la faction {name} : 20 % par activité, "
                    "5 % par coffre", name=name) if n else
                 tr("Butin de la faction {name} : 20 % par activité",
                    name=name)]
        if dgs:
            lines.append(tr("Donjons : {list}", list=", ".join(dgs)))
        if acts:
            lines.append(tr("Activités du monde : {list}",
                            list=", ".join(acts)))
        if n:
            lines.append(tr("Coffres de la faction : {n}", n=n))
        return "\n".join(lines)
    if k == "gather":
        name = _fr_names("gatherable").get(s.get("id")) or _pretty_id(
            s.get("id"))
        return tr("Récolte : {name}", name=name) + pct
    if k == "craft":
        job = _fr_names("job").get(s.get("job")) or _pretty_id(s.get("job"))
        inputs = " + ".join(f"{n} × {item_label(i)}"
                            for i, n in s.get("input") or ())
        made = f" (×{s['n']})" if (s.get("n") or 1) > 1 else ""
        return (tr("Fabrication{made} : {job} niv. {lvl}", made=made,
                   job=job, lvl=s.get("lvl") or 1)
                + (f" — {inputs}" if inputs else ""))
    if k == "scrap":
        return (tr("Recyclage d'un objet rare à la station d'Étincelle")
                if s.get("id") == "Scrap_Rare" else
                tr("Recyclage d'un objet à la station d'Étincelle")) + pct
    if k == "combine":
        return tr("Combinaison : {items}", items=" + ".join(
            f"{n} × {item_label(i)}" for i, n in s.get("from") or ()))
    if k == "salvage":
        lo, hi = (s.get("lvl") or [1, 25])[:2]
        rar = s.get("rarity")
        if rar:
            return tr("Démontage d'un équipement {rarity} de niveau {lo} à "
                      "{hi}", rarity=rarity_label(rar).lower(), lo=lo, hi=hi)
        return tr("Démontage d'un équipement de niveau {lo} à {hi}",
                  lo=lo, hi=hi)
    if k == "spawn":
        zones = ", ".join(_zone_label(z) for z in s.get("zones") or ())
        where = tr("en faille") if s.get("rift") else (
            zones or tr("dans le monde"))
        rate = ("" if ch is None or ch >= 1
                else " · " + tr("{pct} des apparitions", pct=_pct(ch)))
        return tr("Se capture : {where}", where=where) + rate
    return k or "?"


# a source's kind, for its tag in "how to get it"
WHERE_TAGS = {"dungeon": "Donjon", "rift": "Faille", "unit": "Butin",
              "family": "Butin",
              "chest": "Coffre", "shop": "Marchand", "craft": "Fabrication",
              "faction": "Faction", "world": "Monde", "ach": "Succès",
              "starter": "Départ", "cache": "Coffret"}


def _seller(s):
    """A merchant's name: the one over its head (the element's), else its
    unit's."""
    npc = s.get("npc")
    return ((_fr_names("element").get(s["el"]) or s.get("eln"))
            if s.get("el") else None) \
        or (_fr_names("unit").get(npc) if npc else None) or tr("un marchand")


def weapon_rarity_odds(level, min_rar=None):
    """A dropped weapon's rarities and their weights at a loot level, from
    `min_rar` up (ent.Hero.makeLootItem, read in hlboot.dat): {rar: weight
    share}, the legendary one being the base of the legendary weapon luck.
    {} without the game's rarity table."""
    table = build_data().get("rarityChance") or {}
    low = RARITY_ORDER.get(min_rar, 0)
    w = {}
    for r, brackets in table.items():
        if RARITY_ORDER.get(r, -1) < low:
            continue
        for lo, hi, ch in brackets:
            if (lo or 0) <= level <= (hi or 10 ** 6) and ch:
                w[r] = float(ch)
    total = sum(w.values())
    return {r: c / total for r, c in w.items()} if total else {}


def dungeon_weapon_odds(dg, entry):
    """{difficulty: weapon_rarity_odds} for a weapon in a dungeon's boss
    loot: the bossLootTable is dropped rare at least, epic in Heroic, the
    unit's lootTable with no minimum (DungeonContext.dropBossLoot), at the
    dungeon's level in Normal and Hero.MaxLevel in Veteran and Heroic
    (st.Activity.getLevel)."""
    top = int(build_data().get("maxLevel") or 25)
    out = {}
    for d in range(entry.get("diff") or 0, 3):
        level = dg.get("level") if d == 0 else top
        if level is None:
            continue
        low = (("Epic" if d == 2 else "Rare") if entry.get("bossTable")
               else None)
        odds = weapon_rarity_odds(level, low)
        if odds:
            out[d] = odds
    return out


def item_where(iid, rar=None, rows_only=False):
    """How to get a piece of gear AT a rarity, for the Build tab: every
    source gives the item's own rarity, except a cache that forces one
    (Hero Weapon Cache: Epic), a dungeon's heroic table (Epic) and a
    weapon's legendary luck (dungeon end chest and boss). {"rar": the asked
    rarity, "base": the item's, "rows": the sources at it, "other": the
    others when none gives it}; a row: {k, t, sub, img, r}."""
    base = (build_data().get("items") or {}).get(iid, {}).get("rar") or ""
    weapon = bool((build_data().get("items") or {}).get(iid, {}).get("hands"))
    rar = rar or base
    rows, seen = [], set()

    def add(k, text, rars, sub="", img="", boss=""):
        if text and text not in seen:
            seen.add(text)
            # boss: the dungeon's boss, whose portrait the window shows
            rows.append({"k": tr(WHERE_TAGS.get(k, "Butin")), "t": text,
                         "sub": sub, "img": img, "boss": boss, "rars": rars,
                         "r": " · ".join(rarity_label(x) for x in rars)})

    def dropped(r):
        """The rarities a weapon dropped at `r` may have: also legendary,
        by the legendary weapon luck (loot, caches, rift and dungeon
        bosses)."""
        return [r] + (["Legendary"] if weapon and r != "Legendary" else [])

    diffs = {d: tr(t) for d, t in DUNGEON_DIFFICULTIES.items()}
    bosses, listed = {}, set()
    rift_bosses = {b.get("id") for b in rift_rewards_data().get("bosses") or ()}
    for dg in dungeon_catalogue():
        bosses[dg.get("boss")] = dg.get("kind")
        name, boss = dungeon_name(dg.get("kind")), dg.get("boss") or ""
        for e in dg.get("loot") or ():
            if e.get("item") != iid:
                continue
            listed.add(name)
            src, diff = e.get("src"), e.get("diff")
            when = (diffs.get(diff) if diff is not None
                    else tr("toutes difficultés"))
            rars = dropped(base)
            if src in ("coffre", "boss"):
                what = (tr("{dungeon} : coffre de fin, {when}") if src == "coffre"
                        else tr("{dungeon} : mort du boss, {when}"))
                # a weapon's rarity: the game's rarity table at the
                # difficulty's level and minimum, the difficulties that give
                # the asked one
                odds = dungeon_weapon_odds(dg, e) if weapon else {}
                if odds:
                    rars = sorted({r for o in odds.values() for r in o},
                                  key=lambda r: RARITY_ORDER.get(r, 9))
                    at = [d for d in sorted(odds) if rar in odds[d]]
                    if at and len(at) < 3:
                        when = tr(" ou ").join(diffs[d] for d in at)
                    elif at:
                        when = tr("toutes difficultés")
                add("dungeon", what.format(dungeon=name, when=when), rars,
                    boss=boss)
            elif src == "faction":
                add("dungeon", tr("{dungeon} : pièce de la faction, en "
                                  "{a} et {b}", dungeon=name, a=diffs[0],
                                  b=diffs[1]), ["Rare"], boss=boss)
            elif src == "heroic":
                add("dungeon", tr("{dungeon} : pièce épique, en {diff}",
                                  dungeon=name, diff=diffs[2]), ["Epic"],
                    boss=boss)
    cat = collection_catalogue()
    srcs = (cat.get("equip") or {}).get(iid)
    if srcs is None:
        srcs = next((e.get("src") or [] for e in cat.get("gears") or ()
                     if e.get("id") == iid), [])
    def shop_pins(lst):
        """Where the merchants of a source list stand on the world map."""
        return [{"x": s["at"][0], "y": s["at"][1],
                 "t": _zone_label(s.get("zone")) if s.get("zone") else ""}
                for s in lst if s.get("k") == "shop" and s.get("at")]

    def shop_texts(lst):
        """The merchants of a source list, one line each: a merchant in
        several towns names them together."""
        towns = {}
        for s in lst:
            if s.get("k") == "shop" and s.get("zone"):
                towns.setdefault((s.get("npc"), str(s.get("cost"))),
                                 []).append(_zone_label(s["zone"]))
        out = []
        for s in lst:
            if s.get("k") != "shop":
                continue
            text = _source_text(s, bosses).split("\n")[0]
            if s.get("zone"):
                text = text.replace(_zone_label(s["zone"]), ", ".join(
                    towns[(s.get("npc"), str(s.get("cost")))]), 1)
            if text not in out:
                out.append(text)
        return out

    for s in srcs:
        k = s.get("k")
        # a dungeon boss's drop or a chest inside a dungeon: said above
        if (k == "unit" and s.get("id") in bosses and listed) or (
                k == "chest" and s.get("zone")
                and _zone_label(s["zone"]) in listed):
            continue
        if k == "cache":
            cid = s.get("id")
            lvl = s.get("lvl")
            text = item_label(cid) + (
                " " + tr("(niv. {n})", n=lvl[0]) if lvl and lvl[0] == lvl[1]
                else "")
            sellers = [x for x in ((cat.get("caches") or {}).get(cid)
                                   or {}).get("src") or ()
                       if x.get("k") == "shop"]
            # who sells it, its towns on the map below
            got = list(dict.fromkeys(
                tr("Vendu par {who}", who=_seller(x)) for x in sellers))
            add("cache", text, dropped(s.get("rar") or base),"\n".join(got),
                item_icon(cid))
            rows[-1]["pins"] = shop_pins(((cat.get("caches") or {}).get(cid)
                                          or {}).get("src") or ())
            continue
        if k == "shop":
            same = [x for x in srcs if x.get("npc") == s.get("npc")
                    and x.get("cost") == s.get("cost")]
            for text in shop_texts(same):
                if text not in seen:
                    add(k, text, [base])
                    rows[-1]["pins"] = shop_pins(same)
            continue
        if k == "craft":
            # the job with its tool, then the recipe, each ingredient with
            # its icon
            job = _fr_names("job").get(s.get("job")) or _pretty_id(s.get("job"))
            made = f" (×{s['n']})" if (s.get("n") or 1) > 1 else ""
            add(k, tr("{job} niv. {lvl}", job=job, lvl=s.get("lvl") or 1)
                + made, [base], img=item_icon("job_" + str(s.get("job"))))
            rows[-1]["parts"] = [{"n": n, "t": item_label(i),
                                  "img": item_icon(i)}
                                 for i, n in s.get("input") or ()]
            continue
        # faction loot in the world (activities, chests) gives its rare
        # pieces: an epic one is heroic only
        if k == "unit" and s.get("id") in rift_bosses:
            names = _fr_names("zone")
            # the boss's name as the Codex shows it (the game's French)
            add("rift", tr("Butin du boss de faille {name}",
                           name=_unit_label(s["id"])),
                # the rift boss chest's weapon: the game's rarity table at
                # the player's level, rare at least, legendary by the luck
                # (st.activity.RiftContext, read in hlboot.dat)
                ["Rare", "Epic", "Legendary"] if weapon else [base],
                tr("La faille s'ouvre chaque heure, au choix du jeu : {a} ou {b}.",
                   a=names.get(SPOTS[1], SPOTS[1]),
                   b=names.get(SPOTS[0], SPOTS[0])))
            rows[-1]["unit"] = s["id"]
            rows[-1]["pinCls"] = "rift"
            rows[-1]["pins"] = [
                {"x": x, "y": y, "t": names.get(z, z)}
                for x, y, z in (bestiary_catalogue().get("entrances") or {})
                .get(RIFT_ACT) or ()]
            continue
        if k == "faction":
            # faction loot is the faction's rare armour: no weapon, no epic
            # piece (heroic only) from it
            if weapon or base != "Rare":
                continue
            add(k, tr("Butin de la faction {name} : activités et coffres de "
                      "la faction", name=faction_label(s.get("f"))), ["Rare"])
            continue
        # no drop rate here: where, not how often
        # merchants and crafts sell it as it is, any loot may be legendary
        add(k, _source_text(dict(s, chance=None), bosses).split("\n")[0],
            [base] if k in ("shop", "craft", "ach", "starter")
            else dropped(base))
    if rows_only:
        return rows, base
    at = [r for r in rows if rar in r["rars"]]
    # a cache at this rarity already says who sells it: the piece's
    # merchants would repeat it
    if any(r["k"] == tr(WHERE_TAGS["cache"]) for r in at):
        at = [r for r in at if r["k"] != tr(WHERE_TAGS["shop"])]
    # none at the asked rarity: the sources at the item's own one, if it is
    # another (none at all: not in the game yet, it seems)
    other = ([r for r in rows if base in r["rars"]]
             if not at and rar != base else [])
    shown = at or other
    return {"rar": rarity_label(rar), "base": rarity_label(base),
            "rows": at, "other": other,
            # the world map, once, when a merchant is to be shown on it
            "meta": world_map().get("meta") if any(
                r.get("pins") for r in shown) else None}


_RARITIES = {}


def item_rarities(iid):
    """The rarities a piece exists at in the game: those its sources give
    (a cloak is never legendary). The item's own when it has no source.
    Cached for the data in memory (a regeneration reloads it)."""
    key = (iid, id(collection_catalogue()), id(dungeon_catalogue()))
    if key not in _RARITIES:
        rows, base = item_where(iid, rows_only=True)
        got = {r for row in rows for r in row["rars"] if r}
        _RARITIES[key] = sorted(got or ({base} if base else set()),
                                key=lambda r: RARITY_ORDER.get(r, 9))
    return _RARITIES[key]


def collection_view(owned, item_codex=None):
    """The Collection page's data: categories with counts, every item with
    its name, rarity, whether it is owned, description and sources — and
    for the "items" category (the game's item codex) each one's count and
    rank."""
    cat = dict(collection_catalogue())
    item_codex = item_codex or {}
    cat["items"] = codex_items_catalogue()
    owned = dict(owned, items=[k for k, v in item_codex.items()
                               if v and v[0] > 0])
    bosses = {d.get("boss"): d.get("kind") for d in dungeon_catalogue()}
    cats, items = [], []
    for key, label, one in COLLECTION_CATS:
        mine = set(owned.get(key) or ())
        rows = cat.get(key) or []
        got = sum(1 for e in rows if e["id"] in mine)
        cats.append({"v": key, "t": tr(label), "one": tr(one), "n": len(rows),
                     "got": got})
        for e in rows:
            iid = e["id"]
            pet = key == "pets"
            spark = pet and iid in _spark_units()
            rar = e.get("rarity") or ""
            srcs = []
            spawns = {}
            for s in e.get("src") or ():
                if s.get("k") == "spawn":
                    # one line per rate, with every zone it spawns in
                    key2 = (bool(s.get("rift")), s.get("chance"))
                    if key2 not in spawns:
                        spawns[key2] = dict(s, zones=[])
                        srcs.append(spawns[key2])
                    spawns[key2]["zones"] += [z for z in s.get("zones") or ()
                                              if z not in spawns[key2]["zones"]]
                else:
                    srcs.append(s)
            count, rank = (item_codex.get(iid) or [0, 0])[:2] \
                if key == "items" else (None, None)
            items.append({
                "id": iid, "c": key, "own": iid in mine,
                "count": count, "rank": rank,
                "rmax": ITEM_RANKS if key == "items" else None,
                "uses": e.get("uses") if key == "items" else None,
                "name": _unit_label(iid) if pet else item_label(iid),
                "rk": "legendary" if spark else rar.lower(),
                "rar": tr("Étincelle") if spark else
                       (rarity_label(rar) if rar else ""),
                "desc": _fr_ref(_fr_desc("item").get(iid)) if not pet else "",
                "sl": e.get("slot"),
                "ic": e.get("cat") if key == "items" else None,
                "slot": tr(dict(GEAR_SLOTS).get(e.get("slot"))),
                "cls": [APTITUDE_CLASSES[a] for a in e.get("apt") or ()
                        if a in APTITUDE_CLASSES],
                "apt": (", ".join(tr(CLASS_LABELS[APTITUDE_CLASSES[a]])
                                  for a in e.get("apt") or ()
                                  if a in APTITUDE_CLASSES)
                        or tr("toutes")) if key == "gears" else "",
                "lvl": e.get("lvl"),
                "src": [line for s in srcs
                        for line in _source_text(s, bosses).split("\n")]})
    return {"cats": cats, "items": items,
            "slots": [{"v": v, "t": tr(t)} for v, t in GEAR_SLOTS],
            "classes": [{"v": c, "t": tr(CLASS_LABELS[c])}
                        for c in GEAR_CLASSES],
            "itemCats": [{"v": v, "t": tr(t)} for v, t in ITEM_CATS
                         if any(e.get("cat") == v for e in cat["items"])]}


HUNT_TIERS = {"elite": "Élite / boss", "big": "Grand", "foe": ""}


HUNT_REGIONS = ("Z1_Region", "Z2_Region", "Z3_Region", "rift")


def bestiary_view(ranks, owned=None):
    """The hunting log's data: regions, every monster with its name, family,
    kills and codex rank, and every family with its total and the
    collectibles its loot table can give. Monsters the codex holds but the
    levels don't place (rift waves, invasions, summons) are listed too."""
    thresholds = _codex_thresholds()
    cat = bestiary_catalogue()
    every = cat.get("units") or {}
    rows, known = [], set()
    for e in cat.get("placed") or ():
        known.add(e["id"])
        rows.append(e)
    for uid in ranks:
        if uid in known or uid.startswith("TODO") or uid == "Dummy":
            continue
        meta = every.get(uid) or {}
        rows.append({"id": uid, "family": meta.get("family") or "",
                     "tier": meta.get("tier") or "foe",
                     "regions": meta.get("regions") or (
                         [meta["region"]] if meta.get("region") else []),
                     "zones": meta.get("zones") or []})
    regions = []
    for r in HUNT_REGIONS + ("",):
        n = sum(1 for e in rows if (e.get("regions") or [""])[0] == r)
        if n:
            regions.append({"v": r or "other",
                            "t": tr("Failles et invasions") if r == "rift"
                            else _fr_names("zone").get(r) or tr("Autres")
                            if r else tr("Autres"), "n": n})
    # the bosses: their dungeon (its kills are its completions), or the rift
    dg_of = {d.get("boss"): dungeon_name(d.get("kind"))
             for d in dungeon_catalogue() if d.get("boss")}
    for b in rift_rewards_data().get("bosses") or ():
        dg_of.setdefault(b.get("id"), tr("Faille"))
    items = []
    for e in rows:
        kills, rank = (ranks.get(e["id"]) or [0, 0])[:2]
        steps = thresholds.get(e.get("tier") or "foe") or []
        nxt = next((t for t in steps if t > kills), None)
        fam = e.get("family") or ""
        reg = (e.get("regions") or [""])[0]
        items.append({
            "id": e["id"], "name": _unit_label(e["id"]),
            "fam": _family_label(fam), "famId": fam,
            "reg": reg if reg in HUNT_REGIONS else "other",
            "zones": ", ".join(_zone_label(z) for z in e.get("zones") or ()
                               [:4]),
            "tier": tr(HUNT_TIERS.get(e.get("tier"), "")),
            "kills": int(kills), "rank": int(rank),
            "max": len(steps) or 3,
            "next": nxt, "dg": dg_of.get(e["id"], "")})
    fams = {}
    for it in items:
        f = it["famId"]
        if not f:
            continue
        a = fams.setdefault(f, {"id": f, "name": it["fam"], "kills": 0,
                                "species": 0, "hunted": 0,
                                "top": (-1, "")})
        a["top"] = max(a["top"], (it["kills"], it["id"]))
        a["kills"] += it["kills"]
        a["species"] += 1
        a["hunted"] += 1 if it["kills"] else 0
    for f, a in fams.items():
        # the family's own picture, else its most hunted species'
        a["img"] = (f"family_{f}"
                    if (ANALYSIS / "bestiary_img" / f"family_{f}.webp")
                    .exists() else a["top"][1])
        del a["top"]
    return {"regions": regions, "items": items,
            "families": sorted(fams.values(), key=lambda a: -a["kills"]),
            "farm": _farm_view(items, fams, owned or {}),
            "total": sum(i["kills"] for i in items)}


def hunt_detail_view(uid, ranks):
    """One monster's page: who it is, where it spawns in the open world (on
    the Map tab's tiles), and what a kill can give — its family's table and
    its own, each item with its chance per kill."""
    cat = bestiary_catalogue()
    placed = next((e for e in cat.get("placed") or () if e["id"] == uid), None)
    meta = (cat.get("units") or {}).get(uid) or {}
    fam = (placed or meta).get("family") or ""
    tier = (placed or meta).get("tier") or "foe"
    kills, rank = (ranks.get(uid) or [0, 0])[:2]
    steps = _codex_thresholds().get(tier) or []
    zones = (placed or meta).get("zones") or []
    regions = (placed or meta).get("regions") or (
        [meta["region"]] if meta.get("region") else [])
    spawns = [{"x": x, "y": y, "z": _zone_label(z) if z else ""}
              for x, y, z in (cat.get("spawns") or {}).get(uid) or ()]
    # instances it spawns in (dungeon, lair, rift) with their world
    # entrances, and who summons it when no level places it
    where = (cat.get("where") or {}).get(uid) or {}
    insts = []
    for act in where.get("acts") or ():
        rift = act.startswith("POI_Rift_")
        t = (tr("Faille {n}", n=int(act[-2:])) if rift and act[-2:].isdigit()
             else dungeon_name(act))
        insts.append({"t": t, "kind": "Faille" if rift else "Donjon",
                      "doors": [{"x": x, "y": y}
                                for x, y, _z in (cat.get("entrances") or {})
                                .get(act) or ()]})
    by = [{"id": s, "name": _unit_label(s)} for s in where.get("by") or ()]
    # what its summoning altar takes (a soulstone)
    keys = [{"img": item_icon(i), "name": item_label(i)}
            for i in where.get("items") or ()]
    note = ""
    if not zones and not spawns and not insts and not by:
        note = (tr("Apparaît lors d’une invasion déclenchée par une pierre "
                   "d’âme.") if "Soulstone" in uid else
                tr("Source inconnue : il n’apparaît que lors d’événements ou "
                   "comme invocation."))

    rows = []
    src_label = {"family": "Famille", "unit": "Ce monstre", "boss": "Boss"}
    loot = {i: [p, "family", None]
            for i, p in ((cat.get("famLoot") or {}).get(fam) or {}).items()}
    for i, (p, src, need) in ((cat.get("unitLoot") or {}).get(uid)
                              or {}).items():
        if i not in loot or p >= loot[i][0]:
            loot[i] = [p, src, need]
    for i, (p, src, need) in loot.items():
        rk = (item_rarity(i) or "").lower()
        rows.append({"img": item_icon(i), "name": item_label(i), "rk": rk,
                     "type": item_type_label(item_type(i)) if item_type(i)
                     else "",
                     "src": tr(src_label.get(src, src))
                     + (f" · {tr(DUNGEON_DIFFICULTIES.get(need, '?'))}"
                        if need else ""),
                     "chance": tr("garanti") if p >= 1 else _pct(p), "cv": p})
    # rarest first, then the least likely
    rows.sort(key=lambda r: (-RARITY_ORDER.get(r["rk"].capitalize(), 0),
                             r["cv"], r["name"]))
    wm = world_map()
    return {"uid": uid, "name": _unit_label(uid), "fam": _family_label(fam),
            "tier": tr(HUNT_TIERS.get(tier, "")),
            "lvl": (cat.get("lvl") or {}).get(uid),
            "kills": int(kills), "rank": int(rank), "max": len(steps) or 3,
            "next": next((t for t in steps if t > kills), None),
            "zones": [_zone_label(z) for z in zones],
            "regions": [_zone_label(r) if r != "rift" else tr("Failles")
                        for r in regions],
            "spawns": spawns, "insts": insts, "by": by, "keys": keys,
            "note": note,
            "meta": wm.get("meta") or {}, "loot": rows,
            # a variant often has its base monster's words and allegiance
            "desc": next((_fr_ref(_fr_desc("unit").get(u))
                          for u in (cat.get("chain") or {}).get(uid) or [uid]
                          if _fr_desc("unit").get(u)), ""),
            "faction": _fr_names("faction").get(
                (cat.get("faction") or {}).get(uid) or "") or ""}


def _farm_view(items, fams, owned):
    """Mounts and gliders that monsters drop, with their sources (a family
    or one unit), the kills, the chance per kill and of having seen it drop
    by now (1 - (1-p)^kills, each kill an independent roll)."""
    by_id = {it["id"]: it for it in items}
    out = []
    for cat, label in (("mounts", "Monture"), ("gliders", "Planeur")):
        mine = set(owned.get(cat) or ())
        for e in collection_catalogue().get(cat) or ():
            sources, miss = [], 1.0
            for s in e.get("src") or ():
                p = s.get("chance")
                if s.get("k") not in ("family", "unit") or not p:
                    continue
                if s["k"] == "family":
                    fam = fams.get(s["id"]) or {}
                    kills = fam.get("kills", 0)
                    species = sorted((it for it in items
                                      if it["famId"] == s["id"]
                                      and it["kills"]),
                                     key=lambda it: -it["kills"])
                    sources.append({
                        "kind": "family", "fid": s["id"],
                        "img": fam.get("img") or "",
                        "name": _family_label(s["id"]),
                        "sub": tr("toute la famille · {n} espèces",
                                  n=fam.get("species", 0)),
                        "species": [{"name": it["name"], "k": it["kills"]}
                                    for it in species[:8]],
                        "kills": kills, "p": p})
                else:
                    it = by_id.get(s["id"]) or {}
                    kills = it.get("kills", 0)
                    dg = next((d.get("kind") for d in dungeon_catalogue()
                               if d.get("boss") == s["id"]), None)
                    sources.append({
                        "kind": "unit", "img": s["id"],
                        "name": _unit_label(s["id"]),
                        "sub": (tr("boss de {dungeon}",
                                   dungeon=dungeon_name(dg)) if dg else
                                it.get("fam") or tr("monstre")),
                        "kills": kills, "p": p})
                miss *= (1 - p) ** kills
            if not sources:
                continue
            # chance per kill: a range when the sources differ
            total = sum(s["kills"] for s in sources)
            ps = sorted({s["p"] for s in sources})
            pct = (_pct(ps[0]) if len(ps) == 1
                   else tr("{lo} à {hi}", lo=_pct(ps[0]), hi=_pct(ps[-1])))
            odds = (tr("1 chance sur {n}", n=_n(round(1 / ps[0])))
                    if len(ps) == 1 else tr("selon le monstre"))
            mobs = []
            for s in sources:
                if s["kind"] == "family":
                    mobs += [{"img": it["id"], "name": it["name"],
                              "k": it["kills"]}
                             for it in items if it["famId"] == s["fid"]]
                else:
                    mobs.append({"img": s["img"], "name": s["name"],
                                 "k": s["kills"]})
            mobs.sort(key=lambda x: -x["k"])
            out.append({"id": e["id"], "name": item_label(e["id"]),
                        "cat": label, "rk": (e.get("rarity") or "").lower(),
                        "own": e["id"] in mine, "kills": total,
                        "pct": pct, "odds": odds,
                        "had": _pct(1 - miss) if total else "",
                        "mobs": mobs})
    # what is still to farm first, the most advanced of those first
    out.sort(key=lambda m: (m["own"], -m["kills"]))
    return out


NOT_GEAR = {"GearPickaxe", "GearSickle", "Bag", "Misc", "Mount",
            "GearGlider", "Consumable", "Usable", "HealthPotion", "Quest",
            "Currency"}


CLASS_FR = {"Warrior": "Guerrier", "Mage": "Mage", "Priest": "Prêtre",
            "Rogue": "Voleur"}


# augment type -> the style of its line on the profile
AUGMENT_KIND = {"AugmentDemon": "gift", "AugmentDemonSigil": "sigil",
                "AugmentJeweller": "gem"}


# The augment shown on a piece's top right corner: the one its type is
# known for (a ring's gem, a cape's embroidery...), first that it accepts.
CHIP_KINDS = ("AugmentJeweller", "AugmentOutfitter", "AugmentBlacksmith",
              "AugmentEnchantHands", "AugmentEnchantFeet",
              "AugmentEnchantWeapon", "AugmentDemonSigil", "AugmentDemon")


def _augment_chips(item_type, gslots):
    """[{kind, img, name}], one per augment slot the piece's type has (a
    weapon: its formula, then its corrupted gift) — img None when nothing
    is set in it."""
    d = build_data()
    accepts = (d.get("accepts") or {}).get(item_type) or ()
    out = []
    for kind in (k for k in CHIP_KINDS if k in accepts):
        items = set(((d.get("augments") or {}).get(kind) or {}).get("items")
                    or ())
        aid = next((g for g in gslots or () if g in items), None)
        out.append({"kind": kind, "img": item_icon(aid) if aid else None,
                    "name": item_label(aid) if aid else ""})
    return out


def _augment_view(aid, factor=1):
    """One augment on a piece: its name and effects (attribute bonuses and
    maluses, or the skill it grants). Corrupted gifts share one name: the
    effect tells them apart."""
    a = _augments_data().get(aid) or {}
    t = a.get("t") or ""
    fx = []
    for atb, val in a.get("a") or ():
        name = _fr_names("attribute").get(atb) or _pretty_id(atb)
        val = _scaled(val, factor)
        sign = "+" if val > 0 else "\u2212"
        fx.append(f"{name} {sign}{abs(val):g}")
    name = item_label(aid)
    # a formula is already named after its enchantment: not said twice
    fx += [sk for sk in (_skill_label(s) for s in a.get("s") or ())
           if sk not in name]
    return {"k": AUGMENT_KIND.get(t, "enchant" if "Enchant" in t else "aug"),
            "name": name, "fx": " · ".join(fx)}


def _talent_tree(cls, ranks, granted=()):
    """A class's talent tree laid out as the game draws it: the root, then
    per tier (1-4) the three branches, each talent with its points. A talent
    the hero has without having put points in it is one its gear gives."""
    tree = (talent_data().get("trees") or {}).get(cls)
    if not tree:
        return None
    granted = set(granted)
    def cell(t):
        pts = int(ranks.get(t["s"]) or 0)
        gift = not pts and t["s"] in granted
        return {"id": t["s"], "name": _skill_label(t["s"])
                + (" " + tr("(offert par l'équipement)") if gift else ""),
                "pts": t["max"] if gift else pts, "max": t["max"],
                "gift": gift}
    root = next((t for t in tree["talents"] if t["tier"] == 0), None)
    tiers = []
    for tier in (1, 2, 3, 4):
        tiers.append([[cell(t) for t in tree["talents"]
                       if t["tier"] == tier and t["branch"] == b]
                      for b in ("Left", "Center", "Right")])
    spent = sum(int(v or 0) for v in ranks.values())
    # points a tier needs in the lower tiers of its branch
    # (Talents_TierThresholds, implSetTalentRank)
    thresholds = build_data().get("tiers") or [0, 1, 2, 4, 8]
    # shown as the game does: the root's point apart (tier 1 needs only it)
    cost = [""] + [str(thresholds[k] - thresholds[1])
                   for k in range(2, min(5, len(thresholds)))]
    return {"root": cell(root) if root else None, "tiers": tiers,
            "cost": cost, "spent": spent}


def class_keys():
    """The class skills' keys as the game binds them by default: A E R G on
    a French keyboard, Q E R G on an English one (by the interface's
    language)."""
    import i18n
    return ("A", "E", "R", "G") if i18n.lang() == "fr" else ("Q", "E", "R", "G")


def _spell_bar(prof):
    """The action bar as the game shows it: the four weapon skill slots
    (1-4), the next prayer, then the four class skills (A E R G)."""
    if not prof.get("weaponSkills") and not prof.get("slots"):
        return []
    def sk(sid, key):
        return {"id": sid, "name": _skill_label(sid) if sid else "",
                "key": key, "empty": not sid,
                "tip": skill_tip(sid) if sid else None}
    weap = list(prof.get("weaponSkills") or [])[:4]
    weap += [None] * (4 - len(weap))
    out = [sk(s, str(i + 1)) for i, s in enumerate(weap)]
    prayers = prof.get("prayers") or []
    if prayers:
        out.append(dict(sk(prayers[0], "T"), sep=True, big=True,
                        seq=[_skill_label(p) for p in prayers]))
    keys = class_keys()
    for s, key in zip(list(prof.get("slots") or [])[:4], keys):
        out.append(dict(sk(s, key), sep=key == keys[0]))
    return out


def _runes_view(runes):
    """The runes, each under the skill it sits on (rune ids carry it)."""
    owner = talent_data().get("runes") or {}
    by_skill = {}
    for r in runes or ():
        sid = owner.get(r) or r.rsplit("_M", 1)[0]
        by_skill.setdefault(sid, []).append({"id": r, "name": _skill_label(r)})
    return [{"id": sid, "name": _skill_label(sid), "runes": rs}
            for sid, rs in by_skill.items()]


def _weapon_skills(prof, kind, t):
    """The skills chosen for a weapon (Specialization.arsenals, keyed by the
    weapon's item kind or its type)."""
    ars = prof.get("arsenals") or {}
    got = ars.get(kind) or ars.get(t) or []
    return [{"id": s, "name": _skill_label(s)} for s in got if s]


def _sheet(prof, entries):
    """The character sheet: the gear around the hero, the weapons with
    their skills."""
    by = _equip_by_slot(entries)
    def cell(slot, label):
        g = by.get(slot)
        return {"slot": slot, "label": label,
                "icon": SLOT_ICON.get(slot, slot), "g": g}
    def weapon(slot, label):
        g = by.get(slot)
        return {"label": label, "g": g,
                "skills": _weapon_skills(prof, g["id"], g["t"]) if g else []}
    return {"left": [cell(*s) for s in SHEET_LEFT],
            "right": [cell(*s) for s in SHEET_RIGHT],
            "weapons": [weapon("Weapon1", tr("Main principale")),
                        weapon("OffhandWeapon", tr("Main secondaire"))],
            "arsenal": weapon("Weapon2", tr("Arme de rechange"))}


def character_view(roster, profiles, sel, waiting, live):
    """The Character tab: the players around (to analyse), the profiles
    already built, and the open one."""
    near = []
    for r in sorted(roster, key=lambda r: (not r.get("me"),
                                           -(r.get("lvl") or 0),
                                           r.get("n") or "")):
        near.append({"n": r.get("n"), "lvl": r.get("lvl"),
                     "cls": tr(CLASS_FR.get(r.get("k"), r.get("k") or "")),
                     "ck": class_key(r.get("k")), "me": bool(r.get("me")),
                     "saved": r.get("n") in profiles,
                     "busy": r.get("n") == waiting})
    saved = [{"n": n, "lvl": p.get("lvl"),
              "cls": tr(CLASS_FR.get(p.get("k"), p.get("k") or "")),
              "ck": class_key(p.get("k")),
              "when": date_fr(time.localtime(p.get("at") or 0))}
             for n, p in sorted(profiles.items(),
                                key=lambda kv: -(kv[1].get("at") or 0))]
    view = {"near": near, "count": len(near), "saved": saved, "live": live,
            "open": None}
    prof = profiles.get(sel) if sel else None
    if prof:
        gear, other, cells = [], [], []
        for idx, slot in enumerate(prof.get("equip") or ()):
            if not slot:
                continue
            kind, rar, lvl, upg, gslots, effects, infu, istat, iflags = (
                list(slot) + [None] * 9)[:9]
            prism = _item_flag(iflags, "Prismatic")
            rar = rar or item_rarity(kind) or ""
            t = item_type(kind)
            cell = EQUIP_SLOTS[idx] if idx < len(EQUIP_SLOTS) else None
            fac = slot_factor(cell) if cell else 1
            extras = [_augment_view(g, fac) for g in gslots or ()
                      if g and not str(g).startswith("[")]
            for e in effects or ():
                if e and not str(e).startswith("["):
                    extras.append({"k": "enchant", "name": tr("Enchantement"),
                                   "fx": _skill_label(e)})
            # a weapon's upgrade effect: its type's, at its rarity, from the
            # upgrade level the game opens it at
            up = weapon_upgrade(t, rar) if t else None
            if up:
                text, at = up
                on = isinstance(upg, int) and upg >= at
                extras.append({"k": "upgrade", "name": tr("Amélioration"),
                               "fx": text if on else tr(
                                   "{effect} (à partir de l'amélioration {n})",
                                   effect=text.rstrip(". "), n=at),
                               "off": not on})
            entry = {"id": kind, "name": item_label(kind),
                     "img": item_icon(kind), "rk": rar.lower(),
                     "rar": rarity_label(rar) if rar else "",
                     "type": item_type_label(t) if t else "",
                     "lvl": lvl if isinstance(lvl, int) and lvl > 0 else None,
                     "up": upg if isinstance(upg, int) and upg > 0 else 0,
                     "extras": extras,
                     "prism": prism,
                     "inf": _gear_infusion(kind, infu, istat, prism),
                     "chips": _augment_chips(t, gslots),
                     "plan": (_fr_names("attribute").get(istat)
                              or _pretty_id(istat))
                     if istat and not infu else None,
                     "t": t,
                     # its upgrade skill at work, for the sheet's attributes
                     "upskill": weapon_upgrade_skill(t, rar)
                     if up and on else None}
            st = gear_stats(kind, rar, lvl, upg, gslots, iflags)
            inf = entry["inf"]
            if st and inf and istat:
                bonus = infusion_bonus(kind, rar, st[0], istat)
                if bonus:
                    inf["val"] = _scaled(bonus, fac)
            if st:
                entry["il"] = st[0]
                entry["stats"] = [{"k": k, "t": n, "v": _scaled(v, fac)}
                                  for k, n, v in st[1]]
            entry["augs"] = [[(atb, _scaled(val, fac)) for atb, val in
                              (_augments_data().get(g) or {}).get("a") or ()]
                             for g in gslots or ()
                             if g and not str(g).startswith("[")]
            if fac != 1:
                entry["eff"] = round(fac * 100)
            (other if t in NOT_GEAR else gear).append(entry)
            if t not in NOT_GEAR:
                cells.append((idx, entry))
        view["open"] = {
            "n": prof.get("n"), "lvl": prof.get("lvl"),
            "cls": tr(CLASS_FR.get(prof.get("k"), prof.get("k") or "")),
            "ck": class_key(prof.get("k")), "me": bool(prof.get("me")),
            "when": date_fr(time.localtime(prof.get("at") or 0)),
            "gear": gear, "other": other, "sheet": _sheet(prof, cells),
            "atbs": _hero_sheet(prof, gear),
            "tree": _talent_tree(
                prof.get("k"),
                prof["talents"] if isinstance(prof.get("talents"), dict)
                else {t: 1 for t in prof.get("talents") or ()},
                prof.get("skills") or ()),
            "ranked": isinstance(prof.get("talents"), dict),
            "slots": [{"id": t, "name": _skill_label(t)}
                      for t in prof.get("slots") or ()],
            "runes": _runes_view(prof.get("masteries")),
            "bar": _spell_bar(prof),
            "passives": [{"id": t, "name": _skill_label(t),
                          "tip": skill_tip(t)}
                         for t in dict.fromkeys(prof.get("skills") or ())
                         if t and (t.endswith("_Passive")
                                   or t.endswith("_P"))],
            "infusions": _infusion_sets(gear),
            # the hero in 3D, wearing the player's armour (as a build's)
            "model": "hero:" + ".".join(
                f"{EQUIP_SLOTS[i]}={e['id']}" for i, e in cells
                if i < len(EQUIP_SLOTS) and EQUIP_SLOTS[i] in HERO_SLOTS
                and e.get("id"))}
    return view


# The loot luck counters (analysis_out/luck.json), in display order.
LUCK_LABELS = (("Luck_Mount", "Monture"), ("Luck_Glider", "Planeur"),
               ("Luck_LegendaryWeapon", "Arme légendaire"),
               ("Luck_RareMaterial", "Matériau rare"),
               ("Luck_PrismaticGear", "Équipement prismatique"))


def soulwell_name():
    """The Soulwell's name in the interface's language (the game's)."""
    return _fr_names("element").get("Soulwell") or "Soulwell"


# an item of the game standing for each counter (the Soulwell statuses
# share one icon)
LUCK_ICONS = {"Luck_Mount": "Mount_Boar_01",
              "Luck_Glider": "Glider_Butterfly_Blue",
              "Luck_LegendaryWeapon": "Sword_Swarm",
              "Luck_RareMaterial": "Nightblood",
              "Luck_PrismaticGear": "PrismaticFragment"}


# Progress.counters shown as statistics (the others are internal flags).
# The rift ones head the Failles tab.
RIFT_STAT_LABELS = (("Rift_NbCompleted", "Failles terminées"),
                    ("Rift_NbGatesClosed", "Portails de faille fermés"),
                    ("Rift_NbGatesClosed_InOneRift",
                     "Record de portails fermés en une faille"))
# their badges (assets/charsheet)
RIFT_STAT_ICONS = {"Rift_NbCompleted": "rift_done",
                   "Rift_NbGatesClosed": "rift_portal",
                   "Rift_NbGatesClosed_InOneRift": "rift_record"}


STAT_LABELS = (("Gold_TotalEarned", "Or gagné"),
               ("Gold_TotalEarned_FromActivity", "Or gagné en activités"),
               ("Scrap_NbItemsScrapped", "Objets recyclés"),
               ("CraftPoint_TotalEarned", "Points d'artisanat gagnés"),
               ("CraftPoint_TotalSpent", "Points d'artisanat dépensés"),
               ("Jobs_NbLearnt", "Métiers appris"))


def _pct2(v):
    return f"{v * 100:.1f}".rstrip("0").rstrip(".").replace(".", dec_sep()) + pct_sp()


def _profile_luck(prof):
    """Each loot luck counter: the count the game keeps, the bonus it gives
    (base + count * increment, capped), how many more steps to the cap, and
    whether the Soulwell status that carries it is on (time left). None when
    the counters are not readable (another player: not replicated)."""
    counters = prof.get("counters")
    if not isinstance(counters, dict):
        return None
    now = prof.get("now")
    active = {}
    for k, start, dur, stop in prof.get("luckStatuses") or ():
        end = stop if stop and stop > 0 else (
            start + dur if start is not None and dur and dur > 0 else None)
        left = end - now if end is not None and isinstance(
            now, (int, float)) else None
        active[k] = left if left is None or left > 0 else 0
    out = []
    for cid, label in LUCK_LABELS:
        p = luck_data().get(cid)
        if not p:
            continue
        n = counters.get(cid) or 0
        n = n if isinstance(n, (int, float)) else 0
        base, inc, cap = p.get("base") or 0, p.get("increment") or 0,             p.get("max") or 0
        bonus = min(cap, base + n * inc) if cap else base + n * inc
        steps = (max(0, math.ceil(round((cap - base) / inc, 6)) - int(n))
                 if inc else 0)
        st = p.get("status")
        out.append({"t": tr(label), "n": int(n), "bonus": _pct2(bonus),
                    "img": item_icon(LUCK_ICONS.get(cid)),
                    "cap": _pct2(cap), "full": bonus >= cap,
                    # how far to the cap, for a bar
                    "f": round(min(1.0, bonus / cap), 4) if cap else 1.0,
                    "grows": bool(inc), "inc": _pct2(inc),
                    "steps": steps,
                    "on": st in active,
                    "left": (round(active[st] / 60)
                             if st in active and active[st] is not None
                             else None)})
    return out


def _profile_stats(prof):
    counters = prof.get("counters")
    if not isinstance(counters, dict):
        return None
    return [{"t": tr(label), "v": counters[k]} for k, label in STAT_LABELS
            if isinstance(counters.get(k), (int, float))]


# Collect objectives by item type ([9, type]) / unit type ([4, type]): the
# collection list that counts them.
COLLECT_LISTS = {"Mount": "mounts", "GearGlider": "gliders", "Gear": "gears",
                 "Critter": "pets"}


# ElementCompleted's element kinds -> the map's point category
ELEMENT_CATS = {"LEVEL_Obelisk": "obelisk", "LEVEL_WorldChest": "chest",
                "RedOrb_World": "orb"}


def _ach_progress(a, done, counters, owned, states):
    """[have, need] for an achievement's first objective the app can
    measure, else None."""
    owned_sets = {k: set(owned.get(k) or ()) for k in COLLECT_LISTS.values()}
    for o in a.get("obj") or ():
        ref, v, ts = o.get("ref"), o.get("v"), o.get("t") or []
        if ref == "CounterValue" and ts and isinstance(v, (int, float)):
            name = ts[0][1] if isinstance(ts[0], list) else None
            have = counters.get(name)
            if isinstance(have, (int, float)):
                return [have, v]
        elif ref == "AchievementCompleted" and ts:
            ids = [t[1] for t in ts if isinstance(t, list)
                       and isinstance(t[1], str)]
            return [sum(1 for i in ids if i in done), len(ids)]
        elif ref == "Collect" and ts:
            t0 = ts[0]
            if isinstance(t0, list) and t0[0] in (4, 9):
                lst = COLLECT_LISTS.get(t0[1])
                if lst and isinstance(v, (int, float)):
                    return [len(owned_sets[lst]), v]
            else:
                ids = [t[1] for t in ts if isinstance(t, list)
                       and isinstance(t[1], str)]
                mine = set().union(*owned_sets.values())
                return [sum(1 for i in ids if i in mine), len(ids)]
        elif ref == "ElementCompleted" and ts:
            t0 = ts[0]
            if isinstance(t0, list) and t0[0] == 13:
                kind = t0[1][1] if isinstance(t0[1], list) else None
                region = t0[2][1] if len(t0) > 2 and isinstance(
                    t0[2], list) else None
                cat = ELEMENT_CATS.get(kind)
                pts = [p["id"] for p in world_map().get("points") or ()
                       if p.get("c") == cat and p.get("region") == region]
                if pts:
                    return [sum(1 for i in pts if _element_done(states, i)),
                            len(pts)]
            else:
                ids = [t[1] for t in ts if isinstance(t, list)
                       and isinstance(t[1], str)]
                return [sum(1 for i in ids if _element_done(states, i)),
                        len(ids)]
    return None


def _ach_target_label(t):
    """An objective target ([sheet ref, id]) in French: an activity (a
    dungeon), a job, a faction, an item, a unit."""
    kind, rid = t[0], t[1]
    sheets = {1: ("activity",), 7: ("job",), 5: ("faction", "unitType"),
              3: ("item",), 4: ("unitType",), 12: ("unit",)}.get(
        kind, ("activity", "job", "item", "unit", "unitType", "zone"))
    for sh in sheets:
        nm = _fr_names(sh).get(rid)
        if nm:
            return nm
    return _pretty_id(rid)


def _ach_text(a, by_id, tier_v):
    """An achievement's French name and description: a tier has neither of
    its own, they are its first ancestor's, with the tier's target value."""
    chain, cur = [], a
    while cur and cur["id"] not in [c["id"] for c in chain]:
        chain.append(cur)
        cur = by_id.get(cur.get("parent"))
    name = next((_fr_names("ach").get(c["id"]) for c in chain
                 if _fr_names("ach").get(c["id"])), None)
    desc = next((_fr_desc("ach").get(c["id"]) for c in chain
                 if _fr_desc("ach").get(c["id"])), "")
    if not desc and a.get("copyDesc"):
        desc = _fr_desc("ach").get(a["copyDesc"]) or ""
    v = tier_v
    tgt = next((t for o in a.get("obj") or () for t in o.get("t") or ()
                if isinstance(t, list) and len(t) > 1
                and isinstance(t[1], str)), None)
    tname = _ach_target_label(tgt) if tgt else "…"
    desc = desc.replace("::target::", tname)
    if name:
        name = name.replace("::target::", tname)
    num = (lambda x: f"{x:,.0f}".replace(",", "\u202f")
           if isinstance(x, (int, float)) else "…")
    desc = desc.replace("::targetValue::", num(v))
    for k, cv in (a.get("consts") or {}).items():
        desc = desc.replace(f"::{k}::", num(cv) if isinstance(
            cv, (int, float)) else str(cv))
    if name and "::targetValue::" in name:
        name = name.replace("::targetValue::", num(v))
    elif not _fr_names("ach").get(a["id"]) and name and isinstance(
            v, (int, float)) and len(chain) > 1:
        name = f"{name} ({num(v)})"
    return _fr_ref(name or _pretty_id(a["id"])), _fr_ref(desc)


def achievements_view(account, counters, owned, states):
    """The Succès tab: categories with points and counts, and every
    achievement chain (an achievement and its tiers) with its current tier,
    progress where the app can measure it, reward and completion date."""
    cat = achievements_catalogue()
    achs = cat.get("achievements") or []
    by_id = {a["id"]: a for a in achs}
    done = set(account)
    children = {}
    for a in achs:
        if a.get("parent") in by_id:
            children.setdefault(a["parent"], []).append(a)
    cats = {c["id"]: c for c in cat.get("categories") or ()}

    def top(cid):
        seen = set()
        while cid in cats and cats[cid].get("parent") and cid not in seen:
            seen.add(cid)
            cid = cats[cid]["parent"]
        return cid

    items, totals = [], {}
    for root in achs:
        if root.get("parent") in by_id:
            continue
        tiers, cur = [], root
        while cur and len(tiers) < 20:
            tiers.append(cur)
            nxt = children.get(cur["id"]) or []
            cur = nxt[0] if nxt else None
        cur = next((t for t in tiers if t["id"] not in done), None)
        show = cur or tiers[-1]
        v = next((o.get("v") for o in show.get("obj") or ()
                  if isinstance(o.get("v"), (int, float))), None)
        name, desc = _ach_text(show, by_id, v)
        prog = None if cur is None else _ach_progress(
            cur, done, counters, owned, states)
        last = max((account.get(t["id"]) or 0 for t in tiers), default=0)
        c = show.get("cat") or ""
        tc = top(c) or c
        rewards = [{"id": r, "name": item_label(r), "img": item_icon(r)}
                   for r in (show.get("reward") or ())]
        pts_done = sum(t.get("points") or 0 for t in tiers
                       if t["id"] in done)
        pts_all = sum(t.get("points") or 0 for t in tiers)
        tt = totals.setdefault(tc, {"n": 0, "got": 0, "pts": 0,
                                    "ptsAll": 0})
        tt["n"] += len(tiers)
        tt["got"] += sum(1 for t in tiers if t["id"] in done)
        tt["pts"] += pts_done
        tt["ptsAll"] += pts_all
        items.append({
            "id": root["id"], "c": tc, "sub": c if c != tc else "",
            "name": name, "desc": desc,
            "done": cur is None,
            "tiers": [{"ok": t["id"] in done, "p": t.get("points") or 0}
                      for t in tiers],
            "have": min(prog[0], prog[1]) if prog else None,
            "need": prog[1] if prog else None,
            "pct": (min(1.0, prog[0] / prog[1]) if prog and prog[1]
                    else None),
            "pts": show.get("points") or 0,
            "rewards": rewards,
            "when": date_fr(time.localtime(last / 1000)) if cur is None
            and last else ""})
    order = [c["id"] for c in cat.get("categories") or ()
             if not c.get("parent")]
    out_cats = [{"v": c, "t": _fr_ref(_fr_names("ach").get(c) or c),
                 "img": "achcat_" + c, **totals[c]}
                for c in order if c in totals]
    subs = {c["id"]: _fr_ref(_fr_names("ach").get(c["id"]) or c["id"])
            for c in cat.get("categories") or () if c.get("parent")}
    for it in items:
        it["subT"] = subs.get(it["sub"], "")
    return {"cats": out_cats, "items": items,
            "pts": sum(c["pts"] for c in out_cats),
            "ptsAll": sum(c["ptsAll"] for c in out_cats),
            "got": sum(c["got"] for c in out_cats),
            "n": sum(c["n"] for c in out_cats)}


def _luck_bonus(counter_id, counters):
    p = luck_data().get(counter_id) or {}
    n = counters.get(counter_id) or 0
    n = n if isinstance(n, (int, float)) else 0
    return min(p.get("max") or 0, (p.get("base") or 0)
               + n * (p.get("increment") or 0)), int(n)


def rift_rewards_view(counters, luck_until):
    """The rifts' loot, for the Failles tab: what each gate tier unlocks, and
    the chests' contents with their chances."""
    d = rift_rewards_data()
    if not d:
        return [{"k": "note", "t": tr("Données des failles absentes : relance "
                                      "Farever Book avec le jeu ouvert pour "
                                      "les générer.")}]
    now = time.time()
    on = {k for k, t in (luck_until or {}).items() if t > now}

    def names(ls):
        return ", ".join(item_label(ln["item"]) for ln in ls if ln.get("item"))

    # what each tier does, in the code's order (tier 3 adds Rift_Tier4,
    # tier 5 adds Rift_Tier6 — the data's own comment says Tier5)
    tier_txt = {0: tr("Ouvre le coffre du boss : sans ça, aucune de ses "
                      "récompenses (armes, montures…)"),
                3: tr("Ajoute au coffre du boss : {items} (une des deux, "
                      "garantie)", items=names(d.get("tier4") or [])),
                5: tr("Ajoute au coffre du boss : {items} (garanti)",
                      items=names(d.get("tier6") or []))}
    rows = [{"t": tr("{n} portails fermés", n=int(t["gates"])),
             "meta": tier_txt.get(i, tr("Un coffre bonus de plus"))}
            for i, t in enumerate(d.get("tiers") or ())]

    def luck_note(item):
        """A mount's / glider's own luck counter, when its offering is on."""
        t = item_type(item)
        cid = {"Mount": "Luck_Mount", "GearGlider": "Luck_Glider"}.get(t)
        if not cid:
            return 0.0
        status = (luck_data().get(cid) or {}).get("status")
        return _luck_bonus(cid, counters)[0] if status in on else 0.0

    def row(item, src, chance=None, qty="", note=""):
        rar = item_rarity(item) or ""
        return {"img": item_icon(item), "name": item_label(item),
                "rk": rar.lower(),
                "type": item_type_label(item_type(item))
                if item_type(item) else "",
                "apt": [], "src": src,
                "chance": (_pct(chance) if chance is not None and chance < 1
                           else tr("garanti")) + note,
                "qty": qty, "got": 0}

    def chest_rows(ls, src):
        out = []
        for ln in ls:
            qty = (f"{ln['itemMin']}" if ln.get("itemMin") else "")
            if ln.get("lootTable") == "Soulstone":
                out.append({"img": item_icon("Soulstone_Z1_1"),
                            "name": tr("Une pierre d'âme"), "rk": "rare",
                            "type": tr("Pierre d'âme"), "apt": [], "src": src,
                            "chance": tr("garanti"),
                            "qty": tr("1 parmi {n}",
                                      n=len(d.get("soulstone") or [])),
                            "got": 0})
            elif ln.get("item"):
                p = ln.get("proba") or 0
                bonus = luck_note(ln["item"]) if p < 1 else 0
                out.append(row(ln["item"], src, min(1.0, p + bonus), qty,
                               " " + tr("(offrande)") if bonus else ""))
        return out

    boss_rows = []
    for b in d.get("bosses") or ():
        who = _unit_label(b["id"])
        ws = [w for w in b.get("weapons") or () if w.get("item")]
        for w in ws:
            boss_rows.append(row(w["item"],
                                 tr("Coffre du boss · {who}", who=who),
                                 1 / len(ws) if ws else None))
        boss_rows += chest_rows(b.get("extra") or [],
                                tr("Coffre du boss · {who}", who=who))
    boss_rows += chest_rows(d.get("bossChest") or [], tr("Coffre du boss"))
    t4 = [ln for ln in d.get("tier4") or () if ln.get("item")]
    boss_rows += [row(ln["item"], tr("Coffre du boss · 10 portails"),
                      1 / len(t4)) for ln in t4]
    boss_rows += [row(ln["item"], tr("Coffre du boss · 15 portails"))
                  for ln in d.get("tier6") or () if ln.get("item")]
    return [
        {"k": "section", "t": tr("Butin")},
        {"k": "note", "t": tr("D'après le code et les données du jeu. Chaque "
                              "joueur reçoit sa propre part de chaque coffre. "
                              "Le coffre du boss s'ouvre une fois 3 portails "
                              "fermés, chaque palier suivant ajoute un "
                              "coffre bonus ou une récompense garantie.")},
        {"k": "list", "id": "rift_tiers", "rows": rows},
        # the two chests side by side
        {"k": "columns", "id": "rift_chests", "cols": [
            [{"k": "sub", "t": tr("Coffre du boss")},
             {"k": "droptable", "id": "rift_boss_chest", "rows": boss_rows,
              "lite": True}],
            [{"k": "sub", "t": tr("Coffre bonus (5, 9 et 14 portails)")},
             {"k": "droptable", "id": "rift_bonus_chest", "lite": True,
              "rows": chest_rows(d.get("bonusChest") or [],
                                 tr("Coffre bonus"))}]]},
    ]


# category -> (label, group). The groups are the map panel's sections.
MAP_CATS = {"chest": ("Coffre du monde", "Coffres"),
            "vault": ("Coffre de chambre forte", "Coffres"),
            "recipe": ("Coffre de recette", "Coffres"),
            "orb": ("Orbe rouge", "Orbes"),
            "obelisk": ("Obélisque", "Utilitaires"),
            "respawn": ("Point de réapparition", "Utilitaires")}


# Regions whose points are in the game's files but not yet playable (Bel-Etir
# is still in development, 2026-09-28): left off the map and its totals.
MAP_UNRELEASED_REGIONS = {"Bel_Etir_Region"}


def map_view(states=None):
    """The Map tab's data: the tile grid, every point with its French zone
    and region (and whether it is done, when the progress has been read),
    and the categories and regions with their counts."""
    wm = world_map()
    pts = []
    for p in wm.get("points") or ():
        if p.get("c") not in MAP_CATS \
                or p.get("region") in MAP_UNRELEASED_REGIONS:
            continue
        num = re.search(r"(\d+)$", str(p.get("id") or ""))
        pts.append({"c": p["c"], "x": p["x"], "y": p["y"],
                    "f": 1 if _element_done(states, p.get("id")) else 0,
                    "n": int(num.group(1)) if num else 0,
                    "z": _zone_label(p["zone"]) if p.get("zone") else "",
                    "r": p.get("region") or "other"})
    cats = [{"v": k, "t": tr(t), "g": tr(g),
             "n": sum(1 for p in pts if p["c"] == k)}
            for k, (t, g) in MAP_CATS.items()]
    regions = []
    seen = sorted({p["r"] for p in pts} - {"other"},
                  key=lambda r: (r not in HUNT_REGIONS, r))
    for r in seen + ["other"]:
        n = sum(1 for p in pts if p["r"] == r)
        if n:
            regions.append({"v": r, "n": n,
                            "t": _fr_names("zone").get(r) or _pretty_id(r)
                            if r != "other" else tr("Autres")})
    return {"meta": wm.get("meta") or {}, "points": pts, "cats": cats,
            "regions": regions, "known": states is not None}


def _pct(chance):
    if chance is None:
        return "?"
    v = chance * 100
    return f"{v:.0f}{pct_sp()}" if v >= 1 and abs(v - round(v)) < 0.05 \
        else f"{v:.2g}{pct_sp()}".replace(".", dec_sep())


def droptable_view(dg, got, diff=None):
    """A dungeon's possible loot as table rows, rarest first. `got`: item id
    -> how many the saved runs of this dungeon brought back. `diff`: only
    what that difficulty gives (the rare faction armour up to Vétéran, the
    epic one and the infusion pattern in Héroïque)."""
    src_label = {"coffre": "Coffre de fin", "boss": "Mort du boss",
                 "faction": "Armure (Normal, Vétéran)",
                 "heroic": "Armure (Héroïque)"}
    pools = dg.get("pools") or {}
    rows = []
    for e in dg.get("loot") or ():
        if diff is not None and (
                (e.get("src") == "heroic" and diff != 2)
                or (e.get("src") == "faction" and diff == 2)
                or (e.get("diff") is not None and e["diff"] != diff)):
            continue
        qty = ""
        if e.get("qty"):
            qty = " · ".join(
                f"{lo}–{hi}" + (" " + tr("(niv. {a}–{b})", a=a, b=b)
                                if a and b else "")
                for lo, hi, a, b in e["qty"])
        chance = e.get("chance")
        per_class = None
        if e.get("src") in pools:
            # one piece per player, evenly among his class's: 1 / pool
            ps = sorted({1 / pools[e["src"]][c]
                         for c in (e.get("apt") or pools[e["src"]])
                         if pools[e["src"]].get(c)})
            if ps:
                chance = ps[-1]
                per_class = ps
        rows.append({
            "img": item_icon(e["item"]), "name": item_label(e["item"]),
            "rk": (e.get("rarity") or "").lower(),
            "type": item_type_label(e.get("type")) if e.get("type") else "",
            "apt": e.get("apt") or [],
            "src": tr(src_label.get(e.get("src"), e.get("src") or "")),
            # per run; a range when the piece fits classes with different
            # pools ("8,3–9,1 %")
            "chance": ((_pct(per_class[0]).replace(pct_sp(), "") + "–"
                        + _pct(per_class[-1])
                        if per_class and len(per_class) > 1 else
                        _pct(chance) if chance is None or chance < 1
                        else tr("garanti"))
                       + (" " + tr("en {diff}", diff=tr(
                           DUNGEON_DIFFICULTIES.get(e["diff"], "?")))
                          if e.get("diff") and not per_class
                          and diff is None else "")),
            # the chance as a number, for sorting (guaranteed 1, unknown -1)
            "cv": (chance if chance is not None else -1),
            "qty": qty, "got": got.get(e["item"], 0),
            # rarest first; the faction armour (chance unknown) after the
            # chest's pick, the guaranteed shards last
            "_k": (chance if chance is not None else 0.75,
                   -RARITY_ORDER.get(e.get("rarity"), -1))})
    rows.sort(key=lambda r: r.pop("_k"))
    return rows

