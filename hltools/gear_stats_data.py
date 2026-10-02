"""Gear stats, for the Inspecter tab: what the game needs to compute a gear
piece's attributes — from data.cdb (res.light.pak). The computation itself
is in the meter (meter/gearstats.py, gear_stats); this only packs its inputs.

A gear piece holds no rolled stats: st.item.Gear keeps level, upgradeLevel,
slots, infusion and infusionBonusStat, nothing else. The game derives the
attributes every time, deterministically (hlboot.dat, read with
hltools/hlbc_code.py, 2026-10-01):

st.Item.getItemAffixes
    iLevel = the instance's item level (st.item.Gear.getILevel):
        the definition's iLevel, else its required level * 10 + the
        rarity's iLevelBonus ($HItem.getBaseILevel)
        + Item_FlawlessILevelBonus when the Flawless flag (bit 0) is set
        + round(upgradeLevel * Item_GearUpgradeILevelBonus)
        + the iLevel of each augment set in its slots
    equal to the definition's own iLevel -> the definition's affixes
    (fixed in data.cdb, else generated once the same way), otherwise
    $HItem.generateItemAffixes(def, iLevel).

$HItem.generateItemAffixes(def, iLevel)
    scalings = the aptitudes' atbScaling lines that apply
        ($HItem.getItemExpectedScalings: an Uncommon piece skips Vitality
        lines unless it has one aptitude, and Primary lines when it has
        exactly one; conds.minRarity and conds.factions must match)
    armorReduction = mean of the aptitudes' props.armorReduction
    grouped by endAtb; per group v = computeAtbScaling(def, iLevel, group,
    divide=true, armorReduction), added to the group's sourceAtb (or its
    endAtb when it has none); then a sourceAtb's total is divided by its
    endAtb's scaling `scale` for it (MaxHealth from Vitality: 3) and
    rounded: one flat affix per attribute.

$HItem.computeAtbScaling(def, iLevel, group, divide, armorReduction)
    L = iLevel * 0.1
    ratio = scale(0, L, bounds[0], bounds[1])   GearStatsRatio_Scaling_Bounds
    v = scale(index of sourceAtb or endAtb, L, mean start, mean end,
              armorReduction)
    unless gearOnly: v *= ratio;  if divide: v /= number of aptitudes
    round(v) <= 0 -> 0
    v * the item type's atbRatio[statGroup] (primary, vitality, armor,
        ratings — the type's own, not inherited), the rarity's override in
        itemType.props.rarities for primary / vitality when there is one

$HAttributes.getAtbLevelScaling(index, L, start, end, reduction)
    Armor (index 23), MagicArmor (24): getResistanceLevelScaling
        reduction >= 0: (-a*r - b*L*r) / (r - 1)
        (ResistanceScalableReductionFormula a, b)
    start < 1e-10 or end == 0 -> 0
    else start * ((end / start) ^ (1 / (LevelScalingFormula_EarlyMaxLevel
        - 1))) ^ (L - 1)
"""
import json
from pathlib import Path

import pak_extract

STAT_GROUPS = ("primary", "vitality", "armor", "ratings")


def build(game_dir):
    cdb = json.loads(pak_extract.read_entry(Path(game_dir) / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}

    def const(cid):
        for ln in sheets["constant"]["lines"]:
            if ln.get("id") == cid:
                return ln.get("v") or {}
        return {}

    def floats(cid):
        return [x.get("v") for x in const(cid).get("floats") or ()]

    def num(cid):
        v = const(cid)
        return v.get("float", v.get("int"))

    consts = {
        "bounds": floats("GearStatsRatio_Scaling_Bounds"),
        "resist": floats("ResistanceScalableReductionFormula"),
        "earlyMax": num("LevelScalingFormula_EarlyMaxLevel"),
        "upgradeIL": num("Item_GearUpgradeILevelBonus"),
        "flawlessIL": num("Item_FlawlessILevelBonus"),
    }
    rarities = {r["id"]: {"i": i, "il": (r.get("props") or {}).get(
                    "iLevelBonus") or 0}
                for i, r in enumerate(sheets["rarity"]["lines"])}
    attributes = {a["id"]: {"i": i, "flags": a.get("flags") or 0,
                            "scale": {s["attribute"]: s.get("scale")
                                      for s in a.get("scaling") or ()
                                      if s.get("attribute")},
                            # [source, scale, scalingOperator] for the
                            # hero's derived attributes (ent.Unit.
                            # getAtbScaling)
                            "ops": [[s["attribute"], s.get("scale") or 0,
                                     s.get("scalingOperator") or [0]]
                                    for s in a.get("scaling") or ()
                                    if s.get("attribute")]}
                  for i, a in enumerate(sheets["attribute"]["lines"])}
    # each class's base attributes (its unit line, over BaseHero's):
    # [level 1, level 50] on the level formula, or a flat value
    units = {u["id"]: u for u in sheets["unit"]["lines"]}

    def unit_stats(uid, seen=()):
        u = units.get(uid)
        if not u or uid in seen:
            return {}
        out = {}
        for ref in u.get("inherit") or ():
            out.update(unit_stats(ref.get("ref"), seen + (uid,)))
        for st in u.get("stats") or ():
            ls = [x.get("val") for x in st.get("levelScaling") or ()]
            out[st.get("attribute")] = (ls if len(ls) == 2
                                        else st.get("value") or 0)
        return out
    heroes = {k: unit_stats(k) for k in ("Warrior", "Mage", "Priest",
                                         "Rogue")}
    aptitudes = {}
    for a in sheets["aptitude"]["lines"]:
        lines = []
        for s in a.get("atbScaling") or ():
            conds = s.get("conds") or {}
            lines.append({
                "end": s.get("endAtb"), "src": s.get("sourceAtb"),
                "g": s.get("statGroup") or 0,
                "s": s.get("start") or 0, "e": s.get("end") or 0,
                "gearOnly": bool(s.get("gearOnly")),
                "minRarity": conds.get("minRarity"),
                "factions": [f.get("ref") for f in conds.get("factions") or ()]
                            or None})
        aptitudes[a["id"]] = {
            "armorReduction": (a.get("props") or {}).get("armorReduction")
                              or 0,
            "scalings": lines}
    # A type without its own ratio takes its parent's (data.cdb `inherit`,
    # resolved when the game loads its data): Mace -> OHWeapon ->
    # MainhandWeapon, which carries the weapons' ratio.
    tlines = {t["id"]: t for t in sheets["itemType"]["lines"]}

    def inherited(t, get):
        seen = set()
        while t is not None and t["id"] not in seen:
            seen.add(t["id"])
            v = get(t)
            if v:
                return v
            t = tlines.get(t.get("inherit"))
        return {}
    # Equipment slots that weaken what they hold (itemType Slot_* lines,
    # slot.affixFactor — st.Equipment.getAffixFactor): the arsenal's weapon
    # (Slot_Weapon2) at 0.4. The tooltip rounds the result up.
    slot_factors = {t["id"]: (t.get("slot") or {}).get("affixFactor")
                    for t in sheets["itemType"]["lines"]
                    if (t.get("slot") or {}).get("affixFactor") is not None}
    types = {}
    for t in sheets["itemType"]["lines"]:
        ratio = inherited(t, lambda x: x.get("atbRatio"))
        over = {r.get("rarity"): r.get("atbRatio") or {}
                for r in inherited(t, lambda x: (x.get("props") or {})
                                   .get("rarities")) or ()}
        if ratio or over:
            types[t["id"]] = {"ratio": {k: ratio.get(k) for k in STAT_GROUPS
                                        if ratio.get(k) is not None},
                              "rarities": over}
    items = {}
    for it in sheets["item"]["lines"]:
        apt = [a.get("ref") for a in it.get("aptitudes") or () if a.get("ref")]
        fixed = [{"atb": (x.get("target") or {}).get("attribute"),
                  "v": x.get("val")}
                 for x in it.get("affixes") or ()
                 if x.get("ref") == "TAttribute_Flat"]
        il = it.get("iLevel")
        if not (apt or fixed or il):
            continue
        items[it["id"]] = {"type": it.get("type"), "lvl": it.get("level"),
                           "il": il, "rar": it.get("rarity"),
                           "fac": it.get("faction"), "apt": apt,
                           "fixed": fixed or None}
    # Every skill's attribute effects (statuses, passives, talents,
    # infusion passives): [ref, attribute, value, conds] — Flat adds,
    # ARatio adds a share, MRatio / MRatioMin multiply. conds: mastery (a
    # rune the hero has), minRank (talent rank, infusion tier).
    skill_affixes = {}
    for sk in sheets["skill"]["lines"]:
        rows = [[a.get("ref"), (a.get("target") or {}).get("attribute"),
                 a.get("val"), a.get("conds") or {}]
                for a in sk.get("affixes") or ()
                if str(a.get("ref") or "").startswith("TAttribute_")
                and (a.get("target") or {}).get("attribute")]
        if rows:
            skill_affixes[sk["id"]] = rows
    consts["infusionBonus"] = num("Item_InfusionBonusRatio")
    return {"consts": consts, "rarities": rarities, "attributes": attributes,
            "skillAffixes": skill_affixes,
            "heroes": heroes, "slotFactors": slot_factors,
            "aptitudes": aptitudes, "types": types, "items": items}


if __name__ == "__main__":
    import sys
    from gamepath import find_hlboot
    out = build(Path(find_hlboot()).parent)
    print(len(out["items"]), "items;", json.dumps(out["consts"]))
