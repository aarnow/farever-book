"""Page builders: game data and saved state turned into the node specs the
window renders (collection, bestiary, achievements, rift rewards, character,
map, loot tables)."""
from __future__ import annotations

import math
import re
import time

from common import ANALYSIS, _n, _pretty_id, class_key, date_fr
from gamedata import (
    RARITY_ORDER, _augments_data, _codex_thresholds, _element_done, build_data,
    _family_label, _fr_desc, _fr_names, _fr_ref, _item_flag, _skill_label,
    _spark_units, _unit_label, _zone_label, achievements_catalogue,
    bestiary_catalogue, codex_items_catalogue, collection_catalogue,
    dungeon_catalogue, dungeon_name, faction_label, item_icon, item_label,
    item_rarity, item_type, item_type_label, luck_data, rarity_label,
    rift_rewards_data, talent_data, world_map)
from gearstats import (
    EQUIP_SLOTS, SHEET_LEFT, SHEET_RIGHT, SLOT_ICON, _equip_by_slot,
    _gear_infusion, _hero_sheet, _infusion_sets, _scaled, gear_stats,
    infusion_bonus, slot_factor)
from combat import DUNGEON_DIFFICULTIES


COLLECTION_CATS = (("mounts", "Montures", "monture"),
                   ("gliders", "Planeurs", "planeur"),
                   ("pets", "Compagnons", "compagnon"),
                   ("gears", "Équipements", "équipement"),
                   ("items", "Objets", "objet"))


# the armour appearances: slots in the game's order, and the aptitudes
GEAR_SLOTS = (("Head", "Tête"), ("Shoulders", "Épaules"), ("Chest", "Torse"),
              ("Hands", "Mains"), ("Waist", "Taille"), ("Legs", "Jambes"),
              ("Feet", "Pieds"), ("Back", "Dos"))


# a piece's aptitudes -> the classes that wear it (unit.props.aptitudes:
# Warrior Fighter, Rogue Assassin, Mage Wizard, Priest Cleric); none = all
APTITUDE_CLASSES = {"Fighter": "warrior", "Assassin": "rogue",
                    "Wizard": "mage", "Cleric": "priest"}


GEAR_CLASSES = ("warrior", "mage", "rogue", "priest")


FACTION_ACTS = (("WorldElite", "élite", "élites"),
                ("FightStone", "pierre de combat", "pierres de combat"),
                ("ChestOrb", "orbe à coffre", "orbes à coffre"),
                ("WorldCamp", "camp", "camps"),
                ("TimerCollectRun", "course", "courses"),
                ("Ascension", "ascension", "ascensions"))


# the item codex's rank steps (codex_units.json "item": 1, 5, 25, 50)
ITEM_RANKS = 4


CLASS_LABELS = {"warrior": "Guerrier", "mage": "Mage", "rogue": "Voleur",
                "priest": "Prêtre"}


CHEST_LABELS = {"VaultChest": "Coffre-fort", "WorldChest": "Coffre",
                "BossChest": "Coffre du boss"}


def _source_text(s, bosses):
    """One way to obtain a collectible, in French."""
    k, ch = s.get("k"), s.get("chance")
    pct = ("" if ch is None else " — garanti" if ch >= 1
           else f" — {_pct(ch)}")
    if k == "family":
        name = _fr_names("unitType").get(s.get("id")) or _pretty_id(s["id"])
        return f"Butin des ennemis : {name}{pct}"
    if k == "unit":
        dg = bosses.get(s.get("id"))
        where = f" ({dungeon_name(dg)})" if dg else ""
        return f"Butin de {_unit_label(s.get('id'))}{where}{pct}"
    if k == "chest":
        kind = CHEST_LABELS.get(s.get("id"), "Coffre")
        zone = f" — {_zone_label(s['zone'])}" if s.get("zone") else ""
        return f"{kind}{zone}{pct}"
    if k == "shop":
        npc = s.get("npc")
        who = (_fr_names("unit").get(npc) if npc else None) or "un marchand"
        zone = f" — {_zone_label(s['zone'])}" if s.get("zone") else ""
        cost = ", ".join(f"{c['n']} × {item_label(c['item'])}"
                         if c.get("n") else item_label(c["item"])
                         for c in s.get("cost") or () if c.get("item"))
        return f"Vendu par {who}{zone}" + (f" (prix : {cost})" if cost else "")
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
        return f"Succès « {name} »" + (f" : {desc}" if desc and desc != name
                                        else "")
    if k == "starter":
        what = "Équipement" if s.get("gear") else "Planeur"
        return f"{what} de départ du {CLASS_LABELS.get(s.get('cls'), '?')}"
    if k == "world":
        lvl = s.get("lvl") or 1
        return (f"Butin aléatoire du monde (ennemis, coffres, activités) "
                f"de niveau {max(1, lvl - 1)} à {lvl + 2}")
    if k == "faction":
        f = s.get("f")
        info = (collection_catalogue().get("factions") or {}).get(f) or {}
        name = faction_label(f)
        dgs = [_fr_names("activity").get(a) or _pretty_id(a)
               for a in info.get("dungeons") or ()]
        acts = [f"{n} {one if n == 1 else many}"
                for key, one, many in FACTION_ACTS
                for n in [(info.get("acts") or {}).get(key)] if n]
        n = info.get("chests") or 0
        lines = [f"Butin de la faction {name} : 20 % par activité"
                 + (", 5 % par coffre" if n else "")]
        if dgs:
            lines.append("Donjons : " + ", ".join(dgs))
        if acts:
            lines.append("Activités du monde : " + ", ".join(acts))
        if n:
            lines.append(f"Coffres de la faction : {n}")
        return "\n".join(lines)
    if k == "gather":
        name = _fr_names("gatherable").get(s.get("id")) or _pretty_id(
            s.get("id"))
        return f"Récolte : {name}{pct}"
    if k == "craft":
        job = _fr_names("job").get(s.get("job")) or _pretty_id(s.get("job"))
        inputs = " + ".join(f"{n} × {item_label(i)}"
                            for i, n in s.get("input") or ())
        made = f" (×{s['n']})" if (s.get("n") or 1) > 1 else ""
        return (f"Fabrication{made} : {job} niv. {s.get('lvl') or 1}"
                + (f" — {inputs}" if inputs else ""))
    if k == "scrap":
        what = "objet rare" if s.get("id") == "Scrap_Rare" else "objet"
        return f"Recyclage d'un {what} à la station d'Étincelle{pct}"
    if k == "combine":
        return "Combinaison : " + " + ".join(
            f"{n} × {item_label(i)}" for i, n in s.get("from") or ())
    if k == "salvage":
        lo, hi = (s.get("lvl") or [1, 25])[:2]
        rar = s.get("rarity")
        what = (f"d'un équipement {rarity_label(rar).lower()}" if rar
                else "d'un équipement")
        return f"Démontage {what} de niveau {lo} à {hi}"
    if k == "spawn":
        zones = ", ".join(_zone_label(z) for z in s.get("zones") or ())
        where = "en faille" if s.get("rift") else (zones or "dans le monde")
        rate = ("" if ch is None or ch >= 1
                else f" · {_pct(ch)} des apparitions")
        return f"Se capture : {where}{rate}"
    return k or "?"


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
        cats.append({"v": key, "t": label, "one": one, "n": len(rows),
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
                "rar": "Étincelle" if spark else
                       (rarity_label(rar) if rar else ""),
                "desc": _fr_ref(_fr_desc("item").get(iid)) if not pet else "",
                "sl": e.get("slot"),
                "slot": dict(GEAR_SLOTS).get(e.get("slot")),
                "cls": [APTITUDE_CLASSES[a] for a in e.get("apt") or ()
                        if a in APTITUDE_CLASSES],
                "apt": (", ".join(CLASS_LABELS[APTITUDE_CLASSES[a]]
                                  for a in e.get("apt") or ()
                                  if a in APTITUDE_CLASSES)
                        or "toutes") if key == "gears" else "",
                "lvl": e.get("lvl"),
                "src": [line for s in srcs
                        for line in _source_text(s, bosses).split("\n")]})
    return {"cats": cats, "items": items,
            "slots": [{"v": v, "t": t} for v, t in GEAR_SLOTS],
            "classes": [{"v": c, "t": CLASS_LABELS[c]} for c in GEAR_CLASSES]}


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
                     "regions": [meta["region"]] if meta.get("region")
                     else [], "zones": []})
    regions = []
    for r in HUNT_REGIONS + ("",):
        n = sum(1 for e in rows if (e.get("regions") or [""])[0] == r)
        if n:
            regions.append({"v": r or "other",
                            "t": "Failles et invasions" if r == "rift"
                            else _fr_names("zone").get(r) or "Autres"
                            if r else "Autres", "n": n})
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
            "tier": HUNT_TIERS.get(e.get("tier"), ""),
            "kills": int(kills), "rank": int(rank),
            "max": len(steps) or 3,
            "next": nxt})
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


def _farm_view(items, fams, owned):
    """The mounts and gliders a monster can drop, each with every monster
    (a whole family, or one unit — a dungeon boss, an elite demon) that can
    drop it, the kills behind each, and the chances: per kill, and of having
    seen it drop by now (1 - (1-p)^kills, each kill an independent roll)."""
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
                        "sub": f"toute la famille · {fam.get('species', 0)} "
                               "espèces",
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
                        "sub": (f"boss de {dungeon_name(dg)}" if dg else
                                it.get("fam") or "monstre"),
                        "kills": kills, "p": p})
                miss *= (1 - p) ** kills
            if not sources:
                continue
            # One set of numbers per item: the kills of every source summed,
            # the chance per kill (a range when the sources differ), and the
            # chance of having seen it drop by now over all of them.
            total = sum(s["kills"] for s in sources)
            ps = sorted({s["p"] for s in sources})
            pct = (_pct(ps[0]) if len(ps) == 1
                   else f"{_pct(ps[0])} à {_pct(ps[-1])}")
            odds = (f"1 chance sur {_n(round(1 / ps[0]))}" if len(ps) == 1
                    else "selon le monstre")
            # every monster that can drop it: a family's species, or the
            # unit itself — portraits only, the name on hover
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
    """One augment set into a gear: its name and what it does — the attribute
    bonuses and maluses, or the skill it grants (a formula's enchantment, a
    sigil's talent). Corrupted gifts all share one name, so the effect is
    what tells them apart."""
    a = _augments_data().get(aid) or {}
    t = a.get("t") or ""
    fx = []
    for atb, val in a.get("a") or ():
        name = _fr_names("attribute").get(atb) or _pretty_id(atb)
        val = _scaled(val, factor)
        sign = "+" if val > 0 else "\u2212"
        fx.append(f"{name} {sign}{abs(val):g}")
    name = item_label(aid)
    # a formula is already named after its enchantment ("Formule magique :
    # Dévot" gives "Dévot"): no need to say it twice
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
                + (" (offert par l'équipement)" if gift else ""),
                "pts": t["max"] if gift else pts, "max": t["max"],
                "gift": gift}
    root = next((t for t in tree["talents"] if t["tier"] == 0), None)
    tiers = []
    for tier in (1, 2, 3, 4):
        tiers.append([[cell(t) for t in tree["talents"]
                       if t["tier"] == tier and t["branch"] == b]
                      for b in ("Left", "Center", "Right")])
    spent = sum(int(v or 0) for v in ranks.values())
    # the tree's own talents only; the root's point counts like any other
    # the points a tier needs in the lower tiers of its branch
    # (Talents_TierThresholds, read in the game's implSetTalentRank)
    thresholds = build_data().get("tiers") or [0, 1, 2, 4, 8]
    # shown as the game does: the points in the branch, the root's apart
    # (tier 1 needs only the root)
    cost = [""] + [str(thresholds[k] - thresholds[1])
                   for k in range(2, min(5, len(thresholds)))]
    return {"root": cell(root) if root else None, "tiers": tiers,
            "cost": cost, "spent": spent}


def _spell_bar(prof):
    """The action bar as the game shows it: the four weapon skill slots
    (1-4), the next prayer, then the four class skills (A E R G)."""
    if not prof.get("weaponSkills") and not prof.get("slots"):
        return []
    def sk(sid, key):
        return {"id": sid, "name": _skill_label(sid) if sid else "",
                "key": key, "empty": not sid}
    weap = list(prof.get("weaponSkills") or [])[:4]
    weap += [None] * (4 - len(weap))
    out = [sk(s, str(i + 1)) for i, s in enumerate(weap)]
    prayers = prof.get("prayers") or []
    if prayers:
        out.append(dict(sk(prayers[0], "Prière"), sep=True,
                        seq=[_skill_label(p) for p in prayers]))
    for s, key in zip(list(prof.get("slots") or [])[:4],
                      ("A", "E", "R", "G")):
        out.append(dict(sk(s, key), sep=key == "A" and not prayers))
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
            "weapons": [weapon("Weapon1", "Main principale"),
                        weapon("OffhandWeapon", "Main secondaire")],
            "arsenal": weapon("Weapon2", "Arme de rechange")}


def character_view(roster, profiles, sel, waiting, live):
    """The Character tab: the players around (to analyse), the profiles
    already built, and the open one."""
    near = []
    for r in sorted(roster, key=lambda r: (not r.get("me"),
                                           -(r.get("lvl") or 0),
                                           r.get("n") or "")):
        near.append({"n": r.get("n"), "lvl": r.get("lvl"),
                     "cls": CLASS_FR.get(r.get("k"), r.get("k") or ""),
                     "ck": class_key(r.get("k")), "me": bool(r.get("me")),
                     "saved": r.get("n") in profiles,
                     "busy": r.get("n") == waiting})
    saved = [{"n": n, "lvl": p.get("lvl"),
              "cls": CLASS_FR.get(p.get("k"), p.get("k") or ""),
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
                    extras.append({"k": "enchant", "name": "Enchantement",
                                   "fx": _skill_label(e)})
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
                     "t": t}
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
            "cls": CLASS_FR.get(prof.get("k"), prof.get("k") or ""),
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
            "passives": [{"id": t, "name": _skill_label(t)}
                         for t in dict.fromkeys(prof.get("skills") or ())
                         if t and (t.endswith("_Passive")
                                   or t.endswith("_P"))],
            "raw": {k: prof.get(k) for k in ("arsenals", "prayers",
                                              "secondary")},
            "infusions": _infusion_sets(gear)}
    return view


# The loot luck counters (analysis_out/luck.json), in display order.
LUCK_LABELS = (("Luck_Mount", "Monture"), ("Luck_Glider", "Planeur"),
               ("Luck_LegendaryWeapon", "Arme légendaire"),
               ("Luck_RareMaterial", "Matériau rare"),
               ("Luck_PrismaticGear", "Équipement prismatique"))


# Progress.counters shown as statistics (the rest are internal flags).
# The rift counters head the Failles tab; the rest is on the live page.
RIFT_STAT_LABELS = (("Rift_NbCompleted", "Failles terminées"),
                    ("Rift_NbGatesClosed", "Portails de faille fermés"),
                    ("Rift_NbGatesClosed_InOneRift",
                     "Record de portails fermés en une faille"))


STAT_LABELS = (("Gold_TotalEarned", "Or gagné"),
               ("Gold_TotalEarned_FromActivity", "Or gagné en activités"),
               ("Scrap_NbItemsScrapped", "Objets recyclés"),
               ("CraftPoint_TotalEarned", "Points d'artisanat gagnés"),
               ("CraftPoint_TotalSpent", "Points d'artisanat dépensés"),
               ("Jobs_NbLearnt", "Métiers appris"))


def _pct2(v):
    return f"{v * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " %"


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
        out.append({"t": label, "n": int(n), "bonus": _pct2(bonus),
                    "cap": _pct2(cap), "full": bonus >= cap,
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
    return [{"t": label, "v": counters[k]} for k, label in STAT_LABELS
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


RARITY_ORDER_IDS = ("Common", "Uncommon", "Rare", "Epic", "Legendary")


def weapon_rarity_odds(level, luck_bonus=0.0):
    """A dropped weapon's rarity, at least Rare, as ent.Hero.makeLootItem
    draws it: the rarity sheet's generationChance at the player's level;
    the Legendary share first (plus the luck bonus, when the offering is
    on), then Epic / Rare by weight. {rarity: probability}."""
    rr = rift_rewards_data().get("rarities") or {}
    w = {}
    for rid in RARITY_ORDER_IDS[2:]:
        for g in rr.get(rid) or ():
            if g.get("minLevel", 0) <= level <= g.get("maxLevel", 10 ** 6):
                w[rid] = g.get("chance") or 0
    total = sum(w.values())
    if not total:
        return {}
    leg = min(1.0, w.get("Legendary", 0) / total
              + (luck_bonus if w.get("Legendary") else 0))
    rest = total - w.get("Legendary", 0)
    out = {"Legendary": leg}
    for rid in ("Epic", "Rare"):
        out[rid] = (1 - leg) * (w.get(rid, 0) / rest if rest else 0)
    return out


def _luck_bonus(counter_id, counters):
    p = luck_data().get(counter_id) or {}
    n = counters.get(counter_id) or 0
    n = n if isinstance(n, (int, float)) else 0
    return min(p.get("max") or 0, (p.get("base") or 0)
               + n * (p.get("increment") or 0)), int(n)


def rift_rewards_view(counters, luck_until):
    """The rift rewards page: what each gate tier unlocks, the chests'
    contents with their chances, and the weapon's rarity odds at the
    player's level, with and without the Soulwell offering."""
    d = rift_rewards_data()
    if not d:
        return [{"k": "note", "t": "Données des failles absentes : relance "
                                   "Farever France avec le jeu ouvert pour les "
                                   "générer."}]
    level = counters.get("HeroLevel") if isinstance(
        counters.get("HeroLevel"), (int, float)) else 25
    now = time.time()
    on = {k for k, t in (luck_until or {}).items() if t > now}
    left = {k: max(0, round((t - now) / 60)) for k, t in
            (luck_until or {}).items() if t > now}

    def names(ls):
        return ", ".join(item_label(ln["item"]) for ln in ls if ln.get("item"))

    # what each tier does, in the code's order (tier 3 adds Rift_Tier4,
    # tier 5 adds Rift_Tier6 — the data's own comment says Tier5)
    tier_txt = {0: "Ouvre le coffre du boss : sans ça, aucune de ses "
                   "récompenses (armes, montures…)",
                3: f"Ajoute au coffre du boss : {names(d.get('tier4') or [])} "
                   "(une des deux, garantie)",
                5: f"Ajoute au coffre du boss : {names(d.get('tier6') or [])} "
                   "(garanti)"}
    rows = [{"t": f"{int(t['gates'])} portails fermés",
             "meta": tier_txt.get(i, "Un coffre bonus de plus")}
            for i, t in enumerate(d.get("tiers") or ())]

    leg_bonus, leg_n = _luck_bonus("Luck_LegendaryWeapon", counters)
    base = weapon_rarity_odds(level)
    lucky = weapon_rarity_odds(level, leg_bonus)
    leg_on = "Luck_LegendaryWeapon_Status" in on
    cards = [
        {"title": "Arme légendaire", "value": _pct(base.get("Legendary", 0)),
         "sub": f"par arme, sans offrande (niv. {int(level)})"},
        {"title": "Avec l'offrande", "value": _pct(lucky.get("Legendary", 0)),
         "sub": (f"active · {left.get('Luck_LegendaryWeapon_Status', 0)} min"
                 if leg_on else "si tu en fais une")
                + f" · compteur {leg_n}",
         "tone": "rift" if leg_on else ""},
        {"title": "Arme épique",
         "value": _pct((lucky if leg_on else base).get("Epic", 0)),
         "sub": "sinon rare"}]

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
                           else "garanti") + note,
                "qty": qty, "got": 0}

    def chest_rows(ls, src):
        out = []
        for ln in ls:
            qty = (f"{ln['itemMin']}" if ln.get("itemMin") else "")
            if ln.get("lootTable") == "Soulstone":
                out.append({"img": item_icon("Soulstone_Z1_1"),
                            "name": "Une pierre d'âme", "rk": "rare",
                            "type": "Pierre d'âme", "apt": [], "src": src,
                            "chance": "garanti",
                            "qty": f"1 parmi {len(d.get('soulstone') or [])}",
                            "got": 0})
            elif ln.get("item"):
                p = ln.get("proba") or 0
                bonus = luck_note(ln["item"]) if p < 1 else 0
                out.append(row(ln["item"], src, min(1.0, p + bonus), qty,
                               " (offrande)" if bonus else ""))
        return out

    boss_rows = []
    for b in d.get("bosses") or ():
        who = _unit_label(b["id"])
        ws = [w for w in b.get("weapons") or () if w.get("item")]
        for w in ws:
            boss_rows.append(row(w["item"], f"Coffre du boss · {who}",
                                 1 / len(ws) if ws else None))
        boss_rows += chest_rows(b.get("extra") or [],
                                f"Coffre du boss · {who}")
    boss_rows += chest_rows(d.get("bossChest") or [], "Coffre du boss")
    t4 = [ln for ln in d.get("tier4") or () if ln.get("item")]
    boss_rows += [row(ln["item"], "Coffre du boss · 10 portails",
                      1 / len(t4)) for ln in t4]
    boss_rows += [row(ln["item"], "Coffre du boss · 15 portails")
                  for ln in d.get("tier6") or () if ln.get("item")]
    return [
        {"k": "section", "t": "Récompenses des failles"},
        {"k": "note", "t": "D'après le code et les données du jeu. Chaque "
                           "joueur reçoit sa propre part de chaque coffre. Le "
                           "coffre du boss s'ouvre une fois 3 portails "
                           "fermés ; chaque palier suivant ajoute un coffre "
                           "bonus ou une récompense garantie."},
        {"k": "list", "id": "rift_tiers", "rows": rows},
        {"k": "section", "t": "Rareté de l'arme du boss"},
        {"k": "note", "t": "Chaque joueur reçoit une des deux armes du boss "
                           "de la faille. Sa rareté est tirée à ton niveau, "
                           "rare au minimum : la légendaire d'abord, puis "
                           "épique ou rare. Pendant l'offrande d'arme "
                           "légendaire du Puits des âmes, ton compteur "
                           "s'ajoute à la chance : +1 %, puis +0,5 % par "
                           "arme non légendaire (jusqu'à +25 %), et il "
                           "revient à 0 quand une légendaire tombe."},
        {"k": "cards", "id": "rift_weapon_odds", "items": cards},
        {"k": "section", "t": "Coffre du boss"},
        {"k": "droptable", "id": "rift_boss_chest", "rows": boss_rows},
        {"k": "section", "t": "Coffre bonus (5, 9 et 14 portails)"},
        {"k": "droptable", "id": "rift_bonus_chest",
         "rows": chest_rows(d.get("bonusChest") or [], "Coffre bonus")},
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
    cats = [{"v": k, "t": t, "g": g, "n": sum(1 for p in pts if p["c"] == k)}
            for k, (t, g) in MAP_CATS.items()]
    regions = []
    seen = sorted({p["r"] for p in pts} - {"other"},
                  key=lambda r: (r not in HUNT_REGIONS, r))
    for r in seen + ["other"]:
        n = sum(1 for p in pts if p["r"] == r)
        if n:
            regions.append({"v": r, "n": n,
                            "t": _fr_names("zone").get(r) or _pretty_id(r)
                            if r != "other" else "Autres"})
    return {"meta": wm.get("meta") or {}, "points": pts, "cats": cats,
            "regions": regions, "known": states is not None}


def _pct(chance):
    if chance is None:
        return "?"
    v = chance * 100
    return f"{v:.0f} %" if v >= 1 and abs(v - round(v)) < 0.05 \
        else f"{v:.2g} %".replace(".", ",")


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
                f"{lo}–{hi}" + (f" (niv. {a}–{b})" if a and b else "")
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
            "src": src_label.get(e.get("src"), e.get("src") or ""),
            # per run; a range when the piece fits classes with different
            # pools ("8,3–9,1 %")
            "chance": ((_pct(per_class[0]).replace(" %", "") + "–"
                        + _pct(per_class[-1])
                        if per_class and len(per_class) > 1 else
                        _pct(chance) if chance is None or chance < 1
                        else "garanti")
                       + (f" en {DUNGEON_DIFFICULTIES.get(e['diff'], '?')}"
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

