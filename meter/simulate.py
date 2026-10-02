"""Damage, heal and mitigation simulation for a build, with the game's own
formulas (hlboot.dat, read with hltools/hlbc_code.py, 2026-10-02):

amount ($HSkill.getStepEffectScaling)
    base value + sum(ratio * attribute). A skill of the main weapon draws
    WeaponPowerRatio.MainhandWeaponSkill (0.4) of each item-scaling
    attribute from the weapon's level instead of the hero
    ($HSkill.getStepEffectItemScaling: the attribute expected at the
    weapon's level for its class aptitudes), the rest from the hero.
    WeaponPower is the weapon's primary attributes, split evenly
    ($HSkill.convertWeaponPowerScaling).
damage (ent.Unit.computeDamage)
    amount * (1 + mastery of the affinity + Fervor) * DamageModifier
    (ent.GameObject.getDamageRatio), * CritDamage on a critical hit,
    * (1 - mitigation) (getAffinityDamageReduction), * the target's
    DamageTakenModifier.
mitigation (ent.GameObject.getAffinityDamageReduction)
    resistance (Armor; MagicArmor for magic) * (1 - penetration%) ->
    r / (r + a + b * attacker level) (ResistanceScalableReductionFormula),
    + MagicReduction for magic.
heal (ent.Unit.computeHeal)
    amount * CritDamage on a critical heal * HealGivenMultiplier
    (100 % + Fervor).

Not simulated: what a skill's own script adds (conditional bonuses,
stacks, special effects) — those are code, not data.
"""
from __future__ import annotations

from gamedata import _skill_label, build_data, gear_stats_data
from gearstats import _atb_level_scaling

PERCENT = 4                         # attribute flag: stored in %
CLASS_APTITUDE = 1                  # aptitude props flag: a class's


def _resist_consts():
    c = gear_stats_data().get("consts") or {}
    r = c.get("resist") or [385, 100]
    return r[0], r[1]


def mitigation(resist, pen_pct, attacker_level):
    """getAffinityDamageReduction: the share of a hit a resistance stops."""
    a, b = _resist_consts()
    r = max(0.0, resist) * (1 - max(0.0, min(100.0, pen_pct)) / 100)
    return r / (r + a + b * attacker_level) if r > 0 else 0.0


def resist_for(mit, attacker_level):
    """The resistance that gives a mitigation share at an attacker level
    (the target's armour is entered as a percentage)."""
    a, b = _resist_consts()
    mit = max(0.0, min(0.95, mit))
    return mit * (a + b * attacker_level) / (1 - mit)


def is_magic(aff):
    parents = build_data().get("affinities") or {}
    seen = set()
    while aff and aff not in seen:
        if aff == "Magic":
            return True
        if aff == "Physical":
            return False
        seen.add(aff)
        aff = parents.get(aff)
    return False


def _class_apts(kind):
    """The weapon's class aptitudes (Fighter, Cleric...), as the game
    picks them for the item part (aptitude props flag 1)."""
    it = (gear_stats_data().get("items") or {}).get(kind) or {}
    flags = build_data().get("aptFlags") or {}
    return [a for a in it.get("apt") or () if flags.get(a, 0) & CLASS_APTITUDE]


def _expected(apts, atb, level):
    """$HAttributes.getExpectedAttributeAtAptitudesLevel."""
    gs = gear_stats_data()
    lines = [x for a in apts
             for x in (gs.get("aptitudes") or {}).get(a, {}).get("scalings")
             or () if x["end"] == atb]
    if not lines:
        return 0.0
    start = sum(x["s"] for x in lines) / len(lines)
    end = sum(x["e"] for x in lines) / len(lines)
    idx = ((gs.get("attributes") or {}).get(atb) or {}).get("i", -1)
    return _atb_level_scaling(gs, idx, level, start, end)


def _primaries(kind):
    """The weapon's primary attributes (its aptitudes' Primary line)."""
    gs = gear_stats_data()
    it = (gs.get("items") or {}).get(kind) or {}
    out = []
    for a in it.get("apt") or ():
        for x in (gs.get("aptitudes") or {}).get(a, {}).get("scalings") or ():
            if x["g"] == 0:
                out.append(x["end"])
                break
    return out


def effect_amount(effect, hero, weapon=None):
    """An effect's amount before modifiers. `weapon` = (kind, level) when
    the skill is the main weapon's."""
    gs = gear_stats_data()
    atbs = gs.get("attributes") or {}
    item_share = 0.0
    if weapon:
        item_share = float((build_data().get("weaponPowerRatio") or {})
                           .get("MainhandWeaponSkill") or 0.4)
    total = float(effect.get("base") or 0)
    for atb, ratio in effect.get("sc") or ():
        parts = [(atb, ratio)]
        if atb == "WeaponPower":
            prim = _primaries(weapon[0]) if weapon else []
            parts = [(p, ratio / len(prim)) for p in prim]
        for a, r in parts:
            scaling = bool(((atbs.get(a) or {}).get("flags") or 0) & 512)
            if weapon and scaling and item_share:
                total += r * (1 - item_share) * (hero.get(a) or 0)
                total += r * item_share * _expected(
                    _class_apts(weapon[0]), a, weapon[1])
            else:
                total += r * (hero.get(a) or 0)
    return total


def simulate(hero, level, bar, target_armor_pct, enemy_level, incoming):
    """Every damage / heal / shield the bar's skills do, against a target
    whose armour stops `target_armor_pct` % at this level; and what an
    `incoming` hit of an enemy of `enemy_level` leaves.

    hero: the sheet's attribute values (percent attributes in %).
    bar:  [(skill id, label, (weapon kind, level) when it is a weapon's
           skill — the main weapon's or the arsenal's — else None)]."""
    pct = lambda k: (hero.get(k) or 0) / 100.0
    fervor = pct("Fervor")
    crit_c = max(0.0, min(1.0, pct("CritChance")))
    crit_m = max(1.0, pct("CritDamage"))
    dmg_mod = 1.0                               # DamageModifier: 100 %
    heal_mod = 1.0 + fervor                     # HealGivenMultiplier
    target_resist = resist_for(target_armor_pct / 100.0, level)
    effects = build_data().get("effects") or {}
    rows = []
    for sid, label, weapon in bar:
        for e in effects.get(sid) or ():
            base = effect_amount(e, hero, weapon)
            if base <= 0:
                continue
            row = {"id": sid, "name": label or _skill_label(sid),
                   "kind": e["k"],
                   "aff": e.get("aff") or ""}
            if e["k"] == "Damage":
                magic = is_magic(e.get("aff"))
                mastery = pct("MagicMastery" if magic else "PhysicalMastery")
                pen = hero.get("SpellPenetration" if magic
                               else "ArmorPenetration") or 0
                mit = mitigation(target_resist, pen, level)
                hit = base * (1 + mastery + fervor) * dmg_mod * (1 - mit)
                row.update(normal=hit, crit=hit * crit_m,
                           avg=hit * (1 + crit_c * (crit_m - 1)),
                           mit=mit)
            else:                                   # Heal, Shield
                v = base * heal_mod
                row.update(normal=v, crit=v * crit_m,
                           avg=v * (1 + crit_c * (crit_m - 1)))
            rows.append(row)
    taken = max(0.0, (100 - 0.5 * (hero.get("Fervor") or 0)) / 100)
    phys = mitigation(hero.get("Armor") or 0, 0, enemy_level)
    magic = min(1.0, mitigation(hero.get("MagicArmor") or 0, 0, enemy_level)
                + pct("MagicReduction"))
    defense = {"hit": incoming, "taken": taken,
               "phys": {"mit": phys, "after": incoming * (1 - phys) * taken},
               "magic": {"mit": magic, "after": incoming * (1 - magic) * taken},
               "hp": hero.get("MaxHealth") or 0}
    return {"rows": rows, "defense": defense, "critChance": crit_c,
            "critMult": crit_m, "targetResist": target_resist}
