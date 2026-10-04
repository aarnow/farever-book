"""Gear stats and the character sheet's attributes, computed the way the game
does (see hltools/gear_stats_data.py)."""
from __future__ import annotations

import math
import sys

from common import _n, _pretty_id
from gamedata import (
    _fr_names, _fr_ref, _infusion_id, _item_flag, faction_label,
    gear_stats_data, infusion_data)


def _scaled(val, factor):
    """A stat on a piece worn in a weakening slot, as the game's tooltip
    shows it ($HText.makeAfxDescTextsComparisons): ceil(val * factor)."""
    return val if factor == 1 else math.ceil(val * factor)


def slot_factor(slot):
    """The share of its stats a piece keeps in an equipment slot (EQUIP_SLOTS
    name): 0.4 for the arsenal's weapon, 1 elsewhere."""
    return ((gear_stats_data().get("slotFactors") or {})
            .get(f"Slot_{slot}") or 1)


# The equipment container's cells, in order: data.cdb's Slot_* lines
# (itemType, after the item types). Weapon2 is the arsenal's weapon.
EQUIP_SLOTS = ("Weapon1", "Weapon2", "OffhandWeapon", "Head", "Neck",
               "Shoulders", "Chest", "Back", "Hands", "Waist", "Legs", "Feet",
               "FingerLeft", "Trinket", "FingerRight")


# what can sit in each, by item type (weapons: anything else that is gear)
SLOT_TYPES = {"Head": {"Head"}, "Neck": {"GearNeck"},
              "Shoulders": {"Shoulders"}, "Chest": {"Chest"},
              "Back": {"Back"}, "Hands": {"Hands"}, "Waist": {"Waist"},
              "Legs": {"Legs"}, "Feet": {"Feet"},
              "FingerLeft": {"GearFinger"}, "FingerRight": {"GearFinger"},
              "Trinket": {"GearTrinket"}}


# the character sheet as the game lays it out: two columns around the hero
SHEET_LEFT = (("Head", "Tête"), ("Neck", "Cou"), ("Shoulders", "Épaules"),
              ("Chest", "Torse"), ("Back", "Dos"), ("FingerLeft", "Anneau"))


SHEET_RIGHT = (("Hands", "Mains"), ("Waist", "Taille"), ("Legs", "Jambes"),
               ("Feet", "Pieds"), ("Trinket", "Babiole"),
               ("FingerRight", "Anneau"))


SLOT_ICON = {"FingerLeft": "Finger", "FingerRight": "Finger"}


_SLOT_WARNED = set()


def _equip_by_slot(entries):
    """[(cell index, gear entry)] -> {slot: entry}. The cell says the slot;
    an item that can't sit there (the order changed in a patch) is placed by
    its type instead, and said once in the log."""
    out, loose = {}, []
    for i, g in entries:
        slot = EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else None
        ok = slot is not None and (
            g["t"] in SLOT_TYPES[slot] if slot in SLOT_TYPES
            else not any(g["t"] in v for v in SLOT_TYPES.values()))
        if ok and slot not in out:
            out[slot] = g
        else:
            loose.append(g)
    for g in loose:
        key = (g["t"], g["id"])
        if key not in _SLOT_WARNED:
            _SLOT_WARNED.add(key)
            print(f"[meter] equipment: {g['id']} ({g['t']}) not in its "
                  "expected cell — placed by type", file=sys.stderr)
        for slot in EQUIP_SLOTS:
            fits = (g["t"] in SLOT_TYPES[slot] if slot in SLOT_TYPES
                    else not any(g["t"] in v for v in SLOT_TYPES.values()))
            if fits and slot not in out:
                out[slot] = g
                break
    return out


STAT_GROUP_KEYS = ("primary", "vitality", "armor", "ratings")


# shown first, in the character sheet's order; the ratings after
GEAR_STAT_ORDER = ("Armor", "Vitality", "Strength", "Dexterity", "Faith",
                   "Intellect")


def _hx_round(v):
    """Haxe's Math.round: half up, also for negatives."""
    return math.floor(v + 0.5)


def _atb_level_scaling(d, index, level, start, end, reduction=-1.0):
    """$HAttributes.getAtbLevelScaling."""
    c = d["consts"]
    if index in (23, 24):                       # Armor, MagicArmor
        if index == 24 or reduction < 0:
            return 0.0
        a, b = c["resist"][0], c["resist"][1]
        return (-a * reduction - b * level * reduction) / (reduction - 1)
    if start < 1e-10 or end == 0:
        return 0.0
    step = (end / start) ** (1.0 / (c["earlyMax"] - 1))
    return start * step ** (level - 1)


def _item_ilevel(d, it, rarity, glevel):
    """$HItem.getILevel: the definition's iLevel, else its required level
    * 10 + the rarity's bonus. The copy's level (Gear.level) and rarity
    (Weapon.rarity) count, not the definition's (checked against tooltips,
    2026-10-01)."""
    # a fixed iLevel holds only at the definition's level: scaled loot is
    # defined at level 1 and takes the level it dropped at
    if it.get("il") is not None and (not glevel or glevel == it.get("lvl")):
        return int(it["il"])
    lvl = glevel or it.get("lvl") or 1
    return int(lvl) * 10 + int((d["rarities"].get(rarity) or {}).get("il")
                               or 0)


def _compute_atb_scaling(d, it, rarity, ilevel, group, armor_red,
                         divide=True):
    """$HItem.computeAtbScaling(def, iLevel, group, divide, armorRed)."""
    c = d["consts"]
    level = ilevel * 0.1
    first = group[0]
    ratio = _atb_level_scaling(d, 0, level, c["bounds"][0], c["bounds"][1])
    key = first["src"] or first["end"]
    start = sum(x["s"] for x in group) / len(group)
    end = sum(x["e"] for x in group) / len(group)
    index = (d["attributes"].get(key) or {}).get("i", -1)
    v = _atb_level_scaling(d, index, level, start, end, armor_red)
    if not first["gearOnly"]:
        v *= ratio
    if divide:
        v /= max(1, len(it["apt"]))
    if _hx_round(v) <= 0:
        return 0.0
    gname = STAT_GROUP_KEYS[first["g"]] if first["g"] < 4 else None
    t = d["types"].get(it.get("type")) or {}
    mul = (t.get("ratio") or {}).get(gname) or 0.0
    over = (t.get("rarities") or {}).get(rarity)
    if over is not None and gname in ("primary", "vitality") \
            and over.get(gname) is not None:
        mul = over[gname]
    return v * mul


def _generate_affixes(d, it, rarity, ilevel):
    """$HItem.generateItemAffixes -> [(attribute, value)]."""
    apts = [d["aptitudes"][a] for a in it["apt"] if a in d["aptitudes"]]
    if not apts:
        return []
    rar_i = (d["rarities"].get(rarity) or {}).get("i", 0)
    n = len(it["apt"])
    lines = []
    for a in apts:                      # $HItem.getItemExpectedScalings
        for x in a["scalings"]:
            if rarity == "Uncommon":
                if x["g"] == 1 and n > 1:
                    continue
                if x["g"] == 0 and n == 1:
                    continue
            mr = x.get("minRarity")
            if mr and rar_i < (d["rarities"].get(mr) or {}).get("i", 0):
                continue
            if x.get("factions") and it.get("fac") not in x["factions"]:
                continue
            lines.append(x)
    armor_red = sum(a["armorReduction"] for a in apts) / n
    groups = {}
    for x in lines:
        groups.setdefault(x["end"], []).append(x)
    out = []
    for end_atb, group in groups.items():
        v = _compute_atb_scaling(d, it, rarity, ilevel, group, armor_red)
        acc = {}
        for x in group:
            k = x["src"] or x["end"]
            acc[k] = acc.get(k, 0.0) + v
        for k, val in acc.items():
            if val == 0:
                continue
            if k != end_atb:
                sc = (d["attributes"].get(end_atb) or {}).get("scale", {})
                if not sc.get(k):
                    continue            # the game logs an error and skips
                val /= sc[k]
            out.append((k, _hx_round(val)))
    return out


def infusion_bonus(kind, rarity, ilevel, stat):
    """st.item.Gear.getInfusionBonusAffix: the infusion's bonus stat on a
    piece — the InfusionBonus aptitude's line for that stat, scaled like
    the piece's own (not divided by its aptitudes), times
    Item_InfusionBonusRatio, rounded. 0 when unknown."""
    d = gear_stats_data()
    it = (d.get("items") or {}).get(kind)
    apt = (d.get("aptitudes") or {}).get("InfusionBonus") or {}
    group = [x for x in apt.get("scalings") or () if x["end"] == stat]
    if not it or not group or not ilevel:
        return 0
    v = _compute_atb_scaling(d, it, rarity, ilevel, group, -1.0,
                             divide=False)
    return _hx_round((d["consts"].get("infusionBonus") or 0) * v)


def gear_stats(kind, rarity, glevel, upgrade, slots, flags):
    """A gear copy's attributes as the game computes them (st.Item.
    getItemAffixes): (iLevel, [(attribute id, French name, value)]), or None
    when the piece has no stats in the game's data."""
    d = gear_stats_data()
    it = (d.get("items") or {}).get(kind)
    if not it:
        return None
    rarity = rarity or it.get("rar")
    glevel = glevel if isinstance(glevel, int) and glevel > 0 else None
    base = _item_ilevel(d, it, rarity, glevel)
    il = base
    if _item_flag(flags, "Flawless"):
        il += _hx_round(d["consts"]["flawlessIL"])
    if isinstance(upgrade, int) and upgrade > 0:
        il += _hx_round(upgrade * d["consts"]["upgradeIL"])
    for aug in slots or ():
        a = (d["items"].get(aug) or {}) if isinstance(aug, str) else {}
        il += int(a.get("il") or 0)
    if il == base and it.get("fixed"):
        affixes = [(x["atb"], x["v"]) for x in it["fixed"] if x["atb"]]
    else:
        affixes = _generate_affixes(d, it, rarity, il)
    total = {}
    for k, v in affixes:
        total[k] = total.get(k, 0) + v
    names = _fr_names("attribute")
    order = {k: i for i, k in enumerate(GEAR_STAT_ORDER)}
    rows = sorted(((k, names.get(k) or _pretty_id(k), v)
                   for k, v in total.items() if v),
                  key=lambda r: (order.get(r[0], 99), r[1]))
    return il, rows


# Character sheet (ent.Unit.getAtbScaling): class base at the hero's level,
# plus gear, then each derived attribute from its sources.
ATB_PERCENT, ATB_MOVESPEED = 4, 256           # attribute flags


HERO_PRIMARY = ("Vitality", "Strength", "Dexterity", "Faith", "Intellect")


# the game's "Plus de stats" list, in its order (BlockMitigation left out:
# it comes from the shield, which the data here doesn't say)
HERO_SECONDARY = ("CritChance", "CritDamage", "ArmorPenetration",
                  "SpellPenetration", "Fervor", "DodgeChance",
                  "MagicMastery", "PhysicalMastery", "Armor", "MaxHealth",
                  "HealthRegen")


# what the damage simulator reads besides the sheet (meter/simulate.py)
SIM_EXTRA = ("MagicArmor", "MagicReduction")


def hero_attributes(cls, level, gear_totals, effects=None, extra=()):
    """{attribute: value} for a hero of class `cls` at `level` wearing gear
    worth `gear_totals` ({attribute: flat value}), under `effects`
    ({attribute: [flat, share, factor]}: statuses, passives, talents —
    (value + flat) * (1 + share) * factor). None without the data."""
    effects = effects or {}
    d = gear_stats_data()
    base = (d.get("heroes") or {}).get(cls)
    atbs = d.get("attributes") or {}
    if not base or not level:
        return None
    memo = {}

    def own(k):
        b = base.get(k)
        if isinstance(b, list):
            v = _atb_level_scaling(d, (atbs.get(k) or {}).get("i", -1),
                                   level, b[0], b[1])
        else:
            v = b or 0
        return v + (gear_totals.get(k) or 0) + (effects.get(k) or (0,))[0]

    def kfac(k):
        f = (atbs.get(k) or {}).get("flags") or 0
        m = 0.01 if f & ATB_PERCENT else 1.0
        return m                        # MoveSpeed scaling: not on the sheet

    def total(k, depth=0):
        if k in memo:
            return memo[k]
        v = own(k)
        if depth < 6:
            for src, scale, op in (atbs.get(k) or {}).get("ops") or ():
                sv = total(src, depth + 1)
                kind = op[0] if op else 0
                if kind == 0:
                    v += scale * sv
                elif kind == 1:
                    v += (1 - 1 / (1 + sv * kfac(src) * scale)) / kfac(k)
                elif kind == 2 and len(op) >= 4:
                    ref = _atb_level_scaling(d, (atbs.get(src) or {}).get(
                        "i", -1), level, op[1], op[2])
                    if ref:
                        v += sv / ref * op[3]
        e = effects.get(k)
        if e:
            v = v * (1 + e[1]) * e[2]
        memo[k] = v
        return v

    return {k: total(k) for k in HERO_PRIMARY + HERO_SECONDARY + tuple(extra)}


def _hero_sheet(prof, gear):
    """The sheet's attributes: the primaries, then the derived ones, each
    {t, v} ready to show; armour with the damage it stops at this level."""
    totals = {}
    for g in gear:
        for x in g.get("stats") or ():
            totals[x["k"]] = totals.get(x["k"], 0) + x["v"]
        for a in g.get("augs") or ():
            for atb, val in a:
                totals[atb] = totals.get(atb, 0) + val
    for g in gear:
        inf = g.get("inf")
        if inf and inf.get("on") and inf.get("val") and inf.get("statId"):
            k = inf["statId"]
            totals[k] = totals.get(k, 0) + inf["val"]
    lvl = prof.get("lvl") if isinstance(prof.get("lvl"), int) else None
    effects = _hero_effects(prof, gear)
    vals = hero_attributes(prof.get("k"), lvl, totals, effects, SIM_EXTRA)
    if vals is None:
        return None
    d = gear_stats_data()
    names = _fr_names("attribute")
    atbs = d.get("attributes") or {}

    def row(k):
        v = vals[k]
        pct = bool((atbs.get(k) or {}).get("flags", 0) & ATB_PERCENT)
        if pct:
            txt = f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " %"
        elif k == "HealthRegen":
            txt = f"{v:.1f}".replace(".", ",")
        else:
            txt = _n(round(v))
        out = {"k": k, "t": names.get(k) or _pretty_id(k), "v": txt}
        if k == "Armor" and lvl:
            a, b = d["consts"]["resist"][0], d["consts"]["resist"][1]
            red = v / (v + a + b * lvl) if v > 0 else 0
            out["sub"] = f"\u2212{red * 100:.2f}".replace(".", ",") + " %"
        return out
    return {"primary": [row(k) for k in HERO_PRIMARY],
            "secondary": [row(k) for k in HERO_SECONDARY],
            "raw": vals}


def _hero_effects(prof, gear):
    """The attribute effects on the hero: its active statuses, its passives
    and talents, its infusion passives (rank: one per two pieces).
    -> {attribute: [flat, share, factor]}."""
    aff = gear_stats_data().get("skillAffixes") or {}
    masteries = set(prof.get("masteries") or ())
    talents = prof.get("talents") if isinstance(prof.get("talents"),
                                                dict) else {}
    ranks = {}
    for sid in prof.get("statuses") or ():
        ranks.setdefault(sid, 1)
    for sid in prof.get("skills") or ():
        if sid in aff:
            ranks.setdefault(sid, int(talents.get(sid) or 1))
    for sid, r in talents.items():
        if sid in aff:
            ranks[sid] = max(ranks.get(sid, 0), int(r or 1))
    pieces = {}
    for g in gear:
        inf = g.get("inf")
        if inf:
            pieces[inf["id"]] = pieces.get(inf["id"], 0) + 1
    for sid, n in pieces.items():
        if sid in aff and n >= 2:
            ranks[sid] = max(ranks.get(sid, 0), n // 2)
    out = {}
    for sid, rank in ranks.items():
        for ref, atb, val, conds in aff.get(sid) or ():
            if not isinstance(val, (int, float)):
                continue
            if set(conds) - {"mastery", "minRank"}:
                continue                # a condition the sheet can't judge
            if conds.get("mastery") and conds["mastery"] not in masteries:
                continue
            if conds.get("minRank") and rank < conds["minRank"]:
                continue
            e = out.setdefault(atb, [0.0, 0.0, 1.0])
            if ref == "TAttribute_Flat":
                e[0] += val
            elif ref == "TAttribute_ARatio":
                e[1] += val
            elif ref in ("TAttribute_MRatio", "TAttribute_MRatioMin"):
                e[2] *= val
    return out


def _gear_infusion(kind, raw, stat, prism=False):
    """One gear piece's infusion: name, bonus stat, and whether the bonus
    applies (piece of the infusion's faction, or prismatic: active
    regardless of faction)."""
    sid = _infusion_id(raw)
    if not sid:
        return None
    e = (infusion_data().get("infusions") or {}).get(sid) or {}
    fac = e.get("f")
    mine = (infusion_data().get("item_faction") or {}).get(kind)
    return {"id": sid, "name": e.get("name") or _pretty_id(sid),
            "fac": faction_label(fac),
            "bonus": (_fr_names("attribute").get(stat) or _pretty_id(stat))
            if stat else "",
            "statId": stat or None,
            "on": prism or (bool(fac) and mine == fac)}


def _infusion_sets(gear):
    """The infusions worn, by number of pieces, with their 2 / 4 / 6
    tiers and which are reached."""
    infs = infusion_data().get("infusions") or {}
    counts = {}
    for g in gear:
        inf = g.get("inf")
        if inf:
            counts[inf["id"]] = counts.get(inf["id"], 0) + 1
    out = []
    for sid, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        e = infs.get(sid) or {}
        out.append({
            "name": e.get("name") or _pretty_id(sid),
            "fac": faction_label(e.get("f")),
            "role": e.get("role") or "", "n": n,
            "tiers": infusion_tiers(sid, n)})
    return out


def infusion_tiers(sid, n=0):
    """An infusion's 2 / 4 / 6 pieces bonuses, in French, those reached
    with `n` pieces marked on."""
    e = (infusion_data().get("infusions") or {}).get(sid) or {}
    four = []
    for atb, val, ref in e.get("t4") or ():
        name = _fr_names("attribute").get(atb) or _pretty_id(atb)
        v = val * 100 if ref == "TAttribute_ARatio" else val
        four.append(f"{name} {'+' if v >= 0 else '−'}"
                    f"{abs(v):g} %".replace(".", ","))
    return [{"n": k, "on": n >= k, "txt": _fr_ref(t)}
            for k, t in ((2, e.get("t2")), (4, " · ".join(four)),
                         (6, e.get("t6"))) if t]

