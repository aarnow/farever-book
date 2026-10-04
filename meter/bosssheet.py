"""A boss's sheet, as the dungeon page shows it: its health by group size,
its defences, its power, its critical hits, each skill with what it deals or
heals, the phases, what it summons and the statuses it applies — computed at
a level and a difficulty from analysis_out/boss_sheets.json
(hltools/boss_sheets.py), with the game's own formulas:

  level scaling   start * (end / start) ^ ((level - 1) / (earlyMax - 1))
  multipliers     compound down the inheritance (Vitality: elite x9, then
                  the boss's own, x3 in heroic)
  health          Vitality x 3 (MaxHealth's scaling), x the player-count
                  factor where the unit has one
  armour          set as a reduction r: r / (1 - r) * (385 + 100 * level)
  damage / heal   ratio x the unit's FoePower
  critical        chance and bonus + levelDiffScaling per level above the
                  target"""
from __future__ import annotations

import hashlib
import json

from common import ANALYSIS, _n, _pretty_id, element_label
from gamedata import (_fr_desc, _fr_ref, _skill_label, _unit_label,
                      build_data, gear_stats_data)
from simulate import is_magic

# the bosses whose sheet is shown (the others once checked against the game)
BOSS_SHEETS_READY = {"Nepsilon"}

_DATA = None


def boss_sheets():
    global _DATA
    if _DATA is None:
        try:
            _DATA = json.loads((ANALYSIS / "boss_sheets.json")
                               .read_text(encoding="utf-8"))
        except Exception:
            _DATA = {}
    return _DATA


def _num(v, digits=0):
    if digits:
        return f"{v:.{digits}f}".rstrip("0").rstrip(",.").replace(".", ",")
    return _n(round(v))


def _consts():
    c = gear_stats_data().get("consts") or {}
    return (c.get("earlyMax") or 50, (c.get("resist") or [385, 100]))


def _applies(heroic_flag, heroic):
    return heroic_flag is None or bool(heroic_flag) == heroic


def stat(sheet, atb, level, heroic, players=1):
    """One attribute of a unit, or None when it has none."""
    e = (sheet.get("stats") or {}).get(atb)
    if not e:
        return None
    early, (ra, rb) = _consts()
    v = None
    base = e.get("base")
    if base and _applies(base.get("heroic"), heroic):
        if "scale" in base:
            s, end = base["scale"]
            v = s * ((end / s) ** (1 / (early - 1))) ** (level - 1) if s else 0
        else:
            v = base.get("value")
    spec = [sp["s"] for sp in e.get("spec") or ()
            if _applies(sp.get("heroic"), heroic)]
    for sp in spec:                      # the nearest one wins
        if "armorReduction" in sp:
            r = sp["armorReduction"]
            v = r / (1 - r) * (ra + rb * level)
        if "magicReduction" in sp:
            v = sp["magicReduction"] * 100      # a percentage
    if v is None:
        return None
    for m in e.get("mults") or ():
        if _applies(m.get("heroic"), heroic):
            v *= m["m"]
    for sp in spec:
        pc = sp.get("playerCount")
        if pc:
            v *= pc[min(players, len(pc)) - 1]
    return v


_PLACEHOLDER = None


def _icon_ok(sid):
    """The skill's own picture exists and is not the game's blank one (the
    same image several unfinished skills share)."""
    global _PLACEHOLDER
    folder = ANALYSIS / "skill_img"
    if _PLACEHOLDER is None:
        counts = {}
        for p in folder.glob("*.webp"):
            try:
                h = hashlib.md5(p.read_bytes()).hexdigest()
            except OSError:
                continue
            counts[h] = counts.get(h, 0) + 1
        common = max(counts.items(), key=lambda kv: kv[1], default=("", 0))
        _PLACEHOLDER = common[0] if common[1] >= 3 else ""
    p = folder / f"{sid}.webp"
    try:
        return p.is_file() and hashlib.md5(p.read_bytes()).hexdigest() != \
            _PLACEHOLDER
    except OSError:
        return False


def _status_text(sid, data):
    st = (data.get("statuses") or {}).get(sid) or {}
    name = _skill_label(sid)
    if name == _pretty_id(sid):
        return None
    desc = _fr_desc("skill").get(sid) or ""
    for k, v in (st.get("vars") or {}).items():
        desc = desc.replace(f"::{k}%#::", f"{_num(v * 100, 1)} %")
    bits = [_fr_ref(desc).strip().rstrip(".")] if desc else []
    if st.get("stacks") and st["stacks"] < 1000:
        bits.append(f"jusqu'à {st['stacks']} cumuls")
    if st.get("duration"):
        bits.append(f"{_num(st['duration'], 1)} s")
    return {"id": sid, "name": name, "t": " · ".join(b for b in bits if b)}


def _when(sid, phases):
    """The phase thresholds a skill opens ("À 70 % et 40 %")."""
    at = [p["at"] for p in phases
          if p.get("skill") and (p["skill"] == sid
                                 or sid.endswith("_" + p["skill"]))]
    if not at:
        return ""
    pcts = [f"{round(a * 100)} %" for a in at]
    return "À " + (" et ".join([", ".join(pcts[:-1]), pcts[-1]])
                   if len(pcts) > 1 else pcts[0])


def _skills(sheet, level, heroic, data, phases, owner=None):
    power = stat(sheet, "FoePower", level, heroic) or 0
    out = []
    for s in sheet.get("skills") or ():
        if s.get("heroicOnly") and not heroic:
            continue
        effs = [e for e in s.get("effects") or ()
                if _applies(e.get("heroic"), heroic)]
        hits = [e for e in effs if e.get("ratio")]
        main = hits[0] if hits else None
        name = "Attaque de base" if s.get("auto") else _skill_label(s["id"])
        if name == _pretty_id(s["id"]):
            name = ("Soin" if main and main.get("kind") == "heal"
                    else "Compétence")
        aff = (main or (effs[0] if effs else {})).get("aff") or ""
        tags = []
        if s.get("auto"):
            tags.append(f"Toutes les {_num(s['cooldown'], 1)} s"
                        if s.get("cooldown") else "Attaque de base")
        elif s.get("cooldown"):
            tags.append(f"Recharge {_num(s['cooldown'], 1)} s")
        when = _when(s["id"], phases)
        if when:
            tags.append(when)
        if s.get("range"):
            tags.append(("Mêlée " if s.get("auto") else "Portée ")
                        + f"{_num(s['range'], 1)} m")
        fx = []
        if main and main.get("kind") == "heal" and owner:
            fx.append(f"soigne {owner}")
        if any(e.get("knock") for e in effs):
            fx.append("projette les joueurs")
        for st in sorted({e["status"] for e in effs if e.get("status")}):
            t = _status_text(st, data)
            fx.append(f"applique {t['name']}" if t else "ralentit")
        for sm in s.get("summons") or ():
            if _applies(sm.get("heroic"), heroic):
                fx.append(f"invoque {_unit_label(sm['unit'])}")
        val = per = ""
        if main:
            val = _num(power * main["ratio"])
            if main.get("tick"):
                n = 1 / main["tick"]
                per = (f"par impact, {_num(n, 1)} par seconde" if n > 1
                       else "par seconde" if n == 1
                       else f"toutes les {_num(main['tick'], 1)} s")
        # its own picture, else the one of a status of its own (named after
        # it: the Course de bulles' slow), never another skill's
        own = sorted((x for x in s.get("statuses") or ()
                      if x.startswith(s["id"] + "_")), reverse=True)
        icon = next((i for i in [s["id"]] + own if _icon_ok(i)), "")
        out.append({
            "id": s["id"], "name": name, "icon": icon,
            "aff": element_label(aff) if aff else "",
            "magic": bool(aff) and is_magic(aff),
            "heal": bool(main) and main.get("kind") == "heal",
            "v": val, "per": per,
            "coef": (f"×{_num(main['ratio'], 2)} puissance" if main else ""),
            "tags": tags,
            "fx": (fx[0][0].upper() + ", ".join(fx)[1:] + ".") if fx else ""})
    return out


def _unit(sheet, level, heroic, data, players=1):
    hp = stat(sheet, "Vitality", level, heroic, players)
    armor = stat(sheet, "Armor", level, heroic)
    _e, (ra, rb) = _consts()
    return {
        "hp": hp * 3 if hp else None,
        "armor": armor, "armorPct": (armor / (armor + ra + rb * level) * 100
                                     if armor else 0),
        "magicPct": stat(sheet, "MagicArmor", level, heroic) or 0,
        "power": stat(sheet, "FoePower", level, heroic) or 0}


def boss_sheet_view(boss, heroic=True, level=None):
    """The sheet's node, or None when the boss has none (or isn't ready)."""
    data = boss_sheets()
    sheet = (data.get("bosses") or {}).get(boss)
    if not sheet or boss not in BOSS_SHEETS_READY:
        return None
    level = level or (build_data().get("maxLevel") or 25)
    phases = sheet.get("heroicPhases" if heroic else "phases") or []
    me = _unit(sheet, level, heroic, data)
    cc = (sheet.get("stats") or {}).get("CritChance") or {}
    cd = (sheet.get("stats") or {}).get("CritDamage") or {}
    crit = None
    if cc.get("base"):
        crit = {"chance": _num(cc["base"].get("value") or 0, 1),
                "mult": _num((cd.get("base") or {}).get("value", 150) / 100, 2),
                "step": _num(cc.get("diff") or 0, 1)}
    statuses = []
    for s in sheet.get("skills") or ():
        for st in s.get("statuses") or ():
            t = _status_text(st, data)
            if t and t["id"] not in {x["id"] for x in statuses} and t["t"]:
                statuses.append(t)
    summons = []
    for s in sheet.get("skills") or ():
        for sm in s.get("summons") or ():
            u = (data.get("units") or {}).get(sm["unit"])
            if not u or not _applies(sm.get("heroic"), heroic):
                continue
            st = _unit(u, level, heroic, data)
            summons.append({
                "name": _unit_label(sm["unit"]),
                "by": ("Invoqué par " + _skill_label(s["id"])),
                "hp": _num(st["hp"]) if st["hp"] else "—",
                "armor": _num(st["armor"]) if st["armor"] else "—",
                "armorPct": _num(st["armorPct"]),
                "magic": _num(st["magicPct"]), "power": _num(st["power"]),
                "skills": _skills(u, level, heroic, data, [],
                                  owner=_unit_label(boss))})
    group = range(1, int(data.get("maxPlayers") or 4) + 1)
    return {
        "k": "bosssheet", "id": "boss_sheet", "boss": boss,
        "name": _unit_label(boss), "heroic": heroic, "level": level,
        "hp": [{"n": n, "v": _num(_unit(sheet, level, heroic, data, n)["hp"])}
               for n in group],
        "armor": _num(me["armor"]) if me["armor"] else "—",
        "armorPct": _num(me["armorPct"]), "magicPct": _num(me["magicPct"]),
        "power": _num(me["power"]), "crit": crit,
        "skills": _skills(sheet, level, heroic, data, phases),
        "phases": [{"at": round(p["at"] * 100),
                    "t": next((x["name"] for x in _skills(
                        sheet, level, heroic, data, phases)
                        if x["id"] == p["skill"]
                        or x["id"].endswith("_" + (p["skill"] or ""))), "")}
                   for p in phases],
        "summons": summons, "statuses": statuses}
