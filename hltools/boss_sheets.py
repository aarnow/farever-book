"""The bosses' sheets, from data.cdb: what a boss is made of, for the app to
compute at a level and a difficulty (meter/views.boss_sheet_view).

For each boss (and each unit it summons):
  stats    per attribute, merged down the unit's inheritance chain (BaseMob
           -> W_Base / D_Base -> ... -> the boss): the level scaling (or a
           plain value) the nearest ancestor sets, every `multiplier` (they
           compound with inheritance, as the game documents), the spec
           scaling (armour set as a reduction, health by player count) and
           `levelDiffScaling` (gained per level above the target), each with
           the difficulty it is for (`heroic`: True, False, or None = both).
  skills   in the unit's order: cooldown, range, and each effect (damage or
           heal, affinity, ratio of an attribute, status applied, knock-back,
           tick when it repeats, difficulty); the units a skill summons.
  phases   the health thresholds, normal and heroic, with the skill (or the
           sequence, named after its skill) they open with.
  statuses the statuses the skills apply: duration, stacks, variables.

Usage: python boss_sheets.py  (writes analysis_out/boss_sheets.json)."""
from __future__ import annotations

import json
from pathlib import Path

STATS = ("Vitality", "FoePower", "Armor", "MagicArmor", "CritChance",
         "CritDamage")


def _rows(sheets, name):
    return {ln["id"]: ln for ln in sheets[name].get("lines") or ()
            if isinstance(ln.get("id"), str)}


def _chain(units, uid):
    """The unit then its ancestors (first inherit only), root last."""
    out, seen = [], set()
    while uid in units and uid not in seen:
        seen.add(uid)
        out.append(uid)
        inh = units[uid].get("inherit") or ()
        uid = inh[0].get("ref") if inh else None
    return out


def unit_stats(units, uid):
    """{attribute: {base, mults, spec, diff}} merged root -> unit."""
    out = {}
    for u in reversed(_chain(units, uid)):
        for st in units[u].get("stats") or ():
            atb = st.get("attribute")
            if atb not in STATS:
                continue
            e = out.setdefault(atb, {"base": None, "mults": [], "spec": [],
                                     "diff": None})
            hero = st.get("heroic")
            ls = st.get("levelScaling") or []
            if len(ls) >= 2:
                e["base"] = {"scale": [ls[0].get("val"), ls[-1].get("val")],
                             "heroic": hero}
            elif st.get("value") is not None:
                e["base"] = {"value": st["value"], "heroic": hero}
            if st.get("multiplier") is not None:
                e["mults"].append({"m": st["multiplier"], "heroic": hero,
                                   "from": u})
            if st.get("specScaling"):
                sp = dict(st["specScaling"])
                if "playerCount" in sp:
                    sp["playerCount"] = [x.get("multiply", 1)
                                         for x in sp["playerCount"]]
                e["spec"].append({"s": sp, "heroic": hero, "from": u})
            if st.get("levelDiffScaling") is not None:
                e["diff"] = st["levelDiffScaling"]
    return out


def _effects(node, out, tick=None):
    """Every effect under a skill's steps: damage (0), heal (1), and the
    others (a status applied, a resource given) without a value. One entry
    per scaling line: the normal and heroic values are two lines of the
    same effect, told apart by their own `conds.heroic`."""
    if isinstance(node, dict):
        t = ((node.get("props") or {}).get("loop") or {}).get("tick", tick)
        for e in node.get("effects") or ():
            kind = {0: "dmg", 1: "heal"}.get(e.get("effect"), "other")
            base = {"kind": kind, "aff": e.get("affinity") or "",
                    "status": e.get("status"),
                    "knock": bool((e.get("sideEffects") or {}).get(
                        "knockBack")),
                    "tick": t}
            lines = [sc for sc in e.get("scaling") or () if sc.get("ratio")]
            if kind == "other" or not lines:
                out.append(dict(base, ratio=None, atb=None,
                                value=e.get("baseVal") or None,
                                heroic=e.get("heroic")))
                continue
            for sc in lines:
                hero = (sc.get("conds") or {}).get("heroic", e.get("heroic"))
                out.append(dict(base, ratio=sc["ratio"], atb=sc.get("atb"),
                                value=e.get("baseVal") or None,
                                heroic=hero))
        for k, v in node.items():
            if k != "effects":
                _effects(v, out, t)
    elif isinstance(node, list):
        for v in node:
            _effects(v, out, tick)


def _summons(node, out):
    if isinstance(node, dict):
        unit = ((node.get("props") or {}).get("summon") or {}).get("unit")
        if unit:
            out.append({"unit": unit,
                        "heroic": (node.get("cond") or {}).get("heroic")})
        for v in node.values():
            _summons(v, out)
    elif isinstance(node, list):
        for v in node:
            _summons(v, out)


def skill_sheet(skills, sid):
    r = skills.get(sid) or {}
    effects, summons = [], []
    _effects(r.get("steps"), effects)
    _summons(r.get("steps"), summons)
    ranges = [s.get("range") for s in r.get("steps") or ()
              if isinstance(s.get("range"), (int, float))]
    return {"id": sid, "cooldown": r.get("cooldown"),
            "range": max(ranges) if ranges else None,
            "heroicOnly": bool((r.get("props") or {}).get("heroic")),
            "auto": sid.endswith("_Auto") or r.get("type") == 0,
            "effects": effects, "summons": summons,
            "statuses": sorted({e["status"] for e in effects if e["status"]})}


def unit_sheet(units, skills, uid):
    u = units.get(uid) or {}
    phases = (u.get("props") or {}).get("phases") or {}

    def ph(lst):
        return [{"at": p.get("healthThreshold"),
                 "skill": p.get("entrySkill") or p.get("entrySequence")}
                for p in lst or () if p.get("healthThreshold")]
    sk = [skill_sheet(skills, s["skill"]) for s in u.get("skills") or ()]
    return {"id": uid, "lvl": u.get("lvl"), "type": u.get("type"),
            "stats": unit_stats(units, uid),
            "skills": [s for s in sk if s["effects"] or s["summons"]],
            "phases": ph(phases.get("phases")),
            "heroicPhases": ph(phases.get("heroicPhases"))}


def build(cdb, bosses):
    sheets = {s["name"]: s for s in cdb["sheets"]}
    units, skills = _rows(sheets, "unit"), _rows(sheets, "skill")
    out = {"bosses": {}, "units": {}, "statuses": {}, "maxPlayers": 4}
    for c in sheets["constant"].get("lines") or ():
        if c.get("id") == "Group_MaxPlayers":
            out["maxPlayers"] = (c.get("v") or {}).get("int") or 4
    todo = []
    for b in bosses:
        if b in units:
            out["bosses"][b] = unit_sheet(units, skills, b)
            todo.append(out["bosses"][b])
    while todo:
        sh = todo.pop()
        for s in sh["skills"]:
            for st in s["statuses"]:
                r = skills.get(st) or {}
                stp = (r.get("props") or {}).get("status") or {}
                # a poison or a burn deals its damage itself
                se = []
                _effects(r.get("steps"), se)
                _effects(r.get("props"), se)
                out["statuses"][st] = {"duration": r.get("duration"),
                                       "stacks": stp.get("maxStacks"),
                                       "vars": r.get("vars") or {},
                                       "effects": [e for e in se
                                                   if e["ratio"]]}
            for sm in s["summons"]:
                if sm["unit"] in units and sm["unit"] not in out["units"]:
                    out["units"][sm["unit"]] = unit_sheet(units, skills,
                                                          sm["unit"])
                    todo.append(out["units"][sm["unit"]])
    return out


def main():
    import pak_extract
    from gamepath import find_hlboot
    game = Path(find_hlboot(99)).parent
    cdb = json.loads(pak_extract.read_entry(game / "res.light.pak",
                                            "data.cdb"))
    root = Path(__file__).resolve().parent.parent / "analysis_out"
    bosses = [d.get("boss") for d in json.loads(
        (root / "dungeons.json").read_text(encoding="utf-8")) if d.get("boss")]
    data = build(cdb, bosses)
    (root / "boss_sheets.json").write_text(json.dumps(data, indent=0),
                                           encoding="utf-8")
    print(f"[written] boss_sheets.json ({len(data['bosses'])} bosses, "
          f"{len(data['units'])} summons)")


if __name__ == "__main__":
    main()
