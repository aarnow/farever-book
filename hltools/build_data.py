"""What the Build tab needs to assemble a build the way the game allows it,
from data.cdb. Rules read in hlboot.dat (2026-10-02):

* a class wears what carries its aptitude (unit.props.aptitudes: Warrior =
  Fighter, Rogue = Assassin, Mage = Wizard, Priest = Cleric); hybrid
  aptitudes (FigCle...) count for both classes;
* weapons: a one-handed weapon (itemType flag AllowShield: sword, mace, axe)
  leaves the off hand to a shield; two-handed, dual, long weapons take both
  hands. The arsenal (Slot_Weapon2) takes a main-hand weapon;
* augments: one of each kind a piece's type accepts (itemType
  props.augmentTargets, matched through the type's inheritance);
* infusions: armour only, from Item_InfusionMinRarity, on a piece that has a
  faction ($HInfusion.canBeInfused);
* upgrades: up to the rarity's gearUpgrades;
* talents: level - UnlockLevel_Talents + 1 points
  (HeroSpecialization.getTotalTalentPoints); one tree; a talent of tier t
  needs Talents_TierThresholds[t] points in lower tiers of its own branch or
  the root (implSetTalentRank);
* skills: class skills unlock at their level (unit.skills[].level); weapon
  skill slots at UnlockLevel_WeaponSkillSlots, the arsenal's at
  UnlockLevel_Arsenal.
"""
import json
import re
from pathlib import Path

import pak_extract

CLASSES = ("Warrior", "Mage", "Priest", "Rogue")
APT_PARTS = {"Fig": "Fighter", "Ass": "Assassin", "Wiz": "Wizard",
             "Cle": "Cleric"}
ARMOUR = {"Head", "Shoulders", "Chest", "Back", "Hands", "Waist", "Legs",
          "Feet"}
JEWEL = {"GearNeck": "Neck", "GearFinger": "Finger", "GearTrinket": "Trinket"}
CLASS_SKILL_TYPES = ("ClassSkill",)


def _at_rank(conds, rank):
    """A scaling applies at this rank: only rank conditions, met."""
    conds = conds or {}
    if set(conds) - {"minRank", "maxRank"}:
        return False                    # heroic-only and the like
    return (conds.get("minRank") or 0) <= rank <= (conds.get("maxRank")
                                                   or rank)


def build(game_dir):
    cdb = json.loads(pak_extract.read_entry(Path(game_dir) / "res.light.pak",
                                            "data.cdb"))
    sh = {s["name"]: s for s in cdb["sheets"]}
    consts = {c["id"]: c.get("v") or {} for c in sh["constant"]["lines"]
              if isinstance(c.get("id"), str)}

    def num(cid):
        v = consts.get(cid) or {}
        return v.get("float", v.get("int"))

    def floats(cid):
        return [x.get("v") for x in (consts.get(cid) or {}).get("floats") or ()]

    types = {t["id"]: t for t in sh["itemType"]["lines"]}

    def chain(tid):
        out, seen = [], set()
        t = types.get(tid)
        while t is not None and t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
            t = types.get(t.get("inherit"))
        return out

    tflags = next(c for c in sh["itemType"]["columns"]
                  if c["name"] == "flags")["typeStr"].split(":")[1].split(",")
    allow_shield_bit = 1 << tflags.index("AllowShield")

    unit = {u["id"]: u for u in sh["unit"]["lines"]}
    apt_class = {}
    for c in CLASSES:
        for a in (unit.get(c, {}).get("props") or {}).get("aptitudes") or ():
            apt_class[a.get("ref")] = c

    def classes_of(apts):
        out = set()
        for a in apts:
            if a in apt_class:
                out.add(apt_class[a])
            else:                       # hybrid: FigCle -> Fighter + Cleric
                for part, full in APT_PARTS.items():
                    if part in a and full in apt_class:
                        out.add(apt_class[full])
        # stat aptitudes only (jewellery: Crit, ArPen, Vita...): any class
        return sorted(out) or list(CLASSES)

    skill_type = next(c for c in sh["skill"]["columns"]
                      if c["name"] == "type")["typeStr"].split(":")[1].split(",")
    skills = {s["id"]: s for s in sh["skill"]["lines"]}

    def stype(sid):
        t = (skills.get(sid) or {}).get("type")
        return skill_type[t] if isinstance(t, int) else None

    items = {}
    for it in sh["item"]["lines"]:
        tid = it.get("type")
        ch = [t["id"] for t in chain(tid)]
        if tid in ARMOUR:
            slot, hands = tid, None
        elif tid in JEWEL:
            slot, hands = JEWEL[tid], None
        elif "MainhandWeapon" in ch:
            slot = "Weapon"
            hands = ("1h" if "OHWeapon" in ch else "dual" if "DualWeapon" in ch
                     else "long" if "LongWeapon" in ch else "2h")
        elif "OffhandWeapon" in ch:
            slot, hands = "Offhand", "off"
        else:
            continue
        apts = [a.get("ref") for a in it.get("aptitudes") or () if a.get("ref")]
        if not apts and tid not in JEWEL:
            continue                    # no aptitude: no stats, not gear
        entry = {"type": tid, "slot": slot, "cls": classes_of(apts),
                 "fac": it.get("faction"), "lvl": it.get("level"),
                 "rar": it.get("rarity")}
        if hands:
            entry["hands"] = hands
            entry["shield"] = any((t.get("flags") or 0) & allow_shield_bit
                                  for t in chain(tid))
            entry["skills"] = [
                {"id": s.get("skill"), "type": stype(s.get("skill"))}
                for s in it.get("skills") or ()
                if stype(s.get("skill")) in ("WeaponSkill", "AttackCombo",
                                             "WeaponPassive", "Attack",
                                             "Attack2", "Attack3", "Attack4")]
        items[it["id"]] = entry

    # augments: kind -> the types they go on, and the augments themselves
    augments = {}
    for t in sh["itemType"]["lines"]:
        targets = [x.get("itemType") for x in
                   (t.get("props") or {}).get("augmentTargets") or ()]
        if targets:
            augments[t["id"]] = {"targets": targets, "items": []}
    for it in sh["item"]["lines"]:
        if it.get("type") in augments:
            augments[it["type"]]["items"].append(it["id"])
    # which augment kinds each gear type accepts (through its inheritance)
    accepts = {}
    for tid in {e["type"] for e in items.values()}:
        ch = {t["id"] for t in chain(tid)}
        accepts[tid] = sorted(k for k, a in augments.items()
                              if ch & set(a["targets"]))

    # infusions: the infusion patterns' skills
    infusions = sorted({(it.get("props") or {}).get("ref", {}).get("skill")
                        for it in sh["item"]["lines"]
                        if it.get("type") == "InfusionPattern"} - {None})

    classes = {}
    for c in CLASSES:
        u = unit.get(c) or {}
        apt = [a.get("ref") for a in (u.get("props") or {}).get("aptitudes") or ()]
        classes[c] = {
            "apt": apt[0] if apt else None,
            "skills": [{"id": s.get("skill"), "lvl": s.get("level") or 1}
                       for s in u.get("skills") or ()
                       if stype(s.get("skill")) in CLASS_SKILL_TYPES],
            "passives": [{"id": s.get("skill"), "lvl": s.get("level") or 1}
                         for s in u.get("skills") or ()
                         if stype(s.get("skill")) == "ClassPassive"]}

    # what each skill does, for the simulator
    eff_kinds = next(c for c in sh["skill@steps@effects"]["columns"]
                     if c["name"] == "effect")["typeStr"].split(":")[1]         .split(",")
    wanted = {s["id"] for e in items.values() for s in e.get("skills") or ()}
    for c in CLASSES:
        wanted |= {s.get("skill") for s in (unit.get(c) or {}).get("skills")
                   or ()}
    # Steps and scalings can need a rank (minRank / maxRank): skills are shown
    # at their highest (WeaponSkill_MaxRank).
    max_rank = int(num("WeaponSkill_MaxRank") or 3)
    # the runes' French descriptions (lang/export_fr.xml mastery/<rune>/text.desc)
    rune_fr = {}
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(pak_extract.read_entry(Path(game_dir) / "res.pak",
                                                    "lang/export_fr.xml"))
        for sheet in root.findall("sheet"):
            if sheet.get("name") != "skill":
                continue
            for row in sheet:
                for m in row.findall("mastery/*"):
                    d = m.find("text.desc")
                    if d is not None:
                        rune_fr[m.tag] = "".join(d.itertext()).strip()
    except Exception:
        pass
    effects, skill_info = {}, {}
    def collect(sid, rune, no_rune, depth, seen, out, reach):
        """The damage / heal / shield effects of a skill and of the statuses
        and sub-skills it puts down, two levels deep; a step's rune condition
        carries over to them."""
        if sid in seen or depth > 2:
            return reach
        seen.add(sid)
        sk = skills.get(sid) or {}
        subs = [x.get("skill") if isinstance(x, dict) else x
                for x in (sk.get("props") or {}).get("subskills") or ()]
        # a skill with no steps of its own may put its status down from its
        # script: setStatus(owner, Skill.<status>, ...)
        subs += [m for m in re.findall(r"setStatus\([^,]+,\s*Skill\.(\w+)",
                                       sk.get("script") or "")
                 if m in skills]
        for st in sk.get("steps") or ():
            cond = st.get("cond") or {}
            if not ((cond.get("minRank") or 0) <= max_rank
                    <= (cond.get("maxRank") or max_rank)):
                continue                # a step for lower ranks only
            r = cond.get("mastery") or rune
            nr = cond.get("masteryExclude") or no_rune
            for e in st.get("effects") or ():
                k = e.get("effect")
                kind = eff_kinds[k] if isinstance(k, int) else None
                if kind == "Status" and e.get("status"):
                    reach = collect(e["status"], r, nr, depth + 1, seen, out,
                                    reach)
                    continue
                if kind not in ("Damage", "Heal", "Shield"):
                    continue
                if reach is None and isinstance(st.get("range"),
                                                (int, float)):
                    reach = st["range"]
                line = {"k": kind, "aff": e.get("affinity"),
                        "base": e.get("baseVal") or 0,
                        "rune": r, "noRune": nr,
                        "sc": [[x.get("atb"), x.get("ratio") or 0]
                               for x in e.get("scaling") or ()
                               if x.get("atb")
                               and _at_rank(x.get("conds"), max_rank)]}
                if line not in out:     # the same hit again (a last charge)
                    out.append(line)
            ref = ((st.get("props") or {}).get("status") or {}).get("ref")
            if ref:
                reach = collect(ref, r, nr, depth + 1, seen, out, reach)
        for sub in subs:
            if sub:
                reach = collect(sub, rune, no_rune, depth + 1, seen, out,
                                reach)
        return reach

    for sid in wanted:
        sk = skills.get(sid) or {}
        out = []
        reach = collect(sid, None, None, 0, set(), out, None)
        if out:
            effects[sid] = out
        runes = [{"id": m.get("id"),
                  "cd": (m.get("props") or {}).get("cooldown"),
                  "desc": rune_fr.get(m.get("id"))
                  or (m.get("text") or {}).get("desc") or ""}
                 for m in sk.get("mastery") or () if m.get("id")]
        if reach is None:
            reach = max((st["range"] for st in sk.get("steps") or ()
                         if isinstance(st.get("range"), (int, float))),
                        default=None)
        cd = sk.get("cooldown")
        for ov in (sk.get("props") or {}).get("rankOverride") or ():
            if (ov.get("minRank") or 0) <= max_rank and                     (ov.get("props") or {}).get("cooldown") is not None:
                cd = ov["props"]["cooldown"]
        info = {"cd": cd, "range": reach}
        if runes:
            info["runes"] = runes
        if info["cd"] or runes or reach:
            skill_info[sid] = info
    affinities = {a["id"]: a.get("parent") for a in sh["affinity"]["lines"]}
    apt_flags = {a["id"]: (a.get("props") or {}).get("flags") or 0
                 for a in sh["aptitude"]["lines"]}
    wp_ratio = {g.get("id"): (g.get("v") or {}).get("float")
                for g in (consts.get("WeaponPowerRatio") or {}).get("group")
                or ()}
    rarities = [r["id"] for r in sh["rarity"]["lines"]]
    min_rar = ((consts.get("Item_InfusionMinRarity") or {}).get("other")
               or {}).get("rarity")
    return {
        "maxLevel": int(num("MaxLevel") or 25),
        "talentsFrom": int(num("UnlockLevel_Talents") or 10),
        "tiers": [int(x) for x in floats("Talents_TierThresholds")],
        "weaponSkillLevels": [int(x) for x in floats("UnlockLevel_WeaponSkillSlots")],
        "arsenalLevels": [int(x) for x in floats("UnlockLevel_Arsenal")
                          if x and x <= (num("MaxLevel") or 25)],
        "infusionMinRarity": min_rar if isinstance(min_rar, str) else None,
        "rarities": rarities,
        "upgrades": {r["id"]: int((r.get("props") or {}).get("gearUpgrades")
                                  or 0) for r in sh["rarity"]["lines"]},
        "classes": classes, "items": items, "augments": augments,
        "accepts": accepts, "infusions": infusions,
        "effects": effects, "skillInfo": skill_info,
        "affinities": affinities, "aptFlags": apt_flags,
        "weaponPowerRatio": wp_ratio,
    }


if __name__ == "__main__":
    from gamepath import find_hlboot
    out = build(Path(find_hlboot()).parent)
    print({k: (len(v) if isinstance(v, (dict, list)) else v)
           for k, v in out.items()})
