"""The gear infusions: what each does, for the Character tab, from data.cdb
and the game's French text (res.pak lang/export_fr.xml).

An infusion is a passive skill (type InfusionPassive) set on a gear piece by
an infusion pattern (item type InfusionPattern, which names the faction).
Worn on several pieces it reaches tiers, as the game's tooltip shows them:

* 2 pieces: the skill's description;
* 4 pieces: its affixes conditioned on minRank 2 (an attribute bonus);
* 6 pieces: its rank description (texts.rankDescs, the non-empty line).

The texts carry ::placeholders::: a variable of the skill (::var1::), of a
skill it refers to (::ref_stacks:: — ref, ref2, ref3; ref1 is ref), the
referred skill's name (::ref_name::); a trailing % shows a ratio as a
percentage; ::ref_stacks:: falls back on the status's maxStacks, and
::heal:: / ::dmg:: / ::shield:: on the effect's stat scaling ("35 % de
[MaxHealth]"). What the game's scripts compute (some durations) is not in
the data: it reads "X".

Each gear piece also has a faction (item.faction): the infusion's bonus stat
only applies when the two match, so the gear factions are emitted too."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pak_extract

ROLES = {"Tank": "Tank", "Support": "Soutien", "DPS": "Dégâts"}


# ::heal:: / ::dmg:: / ::shield:: -> the effect kind (skill@steps@effects
# .effect) whose stat scaling gives the amount
EFFECT_KEYS = {"heal": 1, "dmg": 0, "damage": 0, "shield": 2}


def _scaling(skill, effect):
    """'35 % de [MaxHealth]': the first step effect of that kind, as the
    share of a stat it scales on (the game shows the computed number)."""
    for st in skill.get("steps") or ():
        for e in st.get("effects") or ():
            if e.get("effect") == effect and e.get("scaling"):
                parts = [f"{_num(sc['ratio'] * 100)} % de "
                         + ("votre attribut principal"
                            if sc["atb"] == "AllAttributes"
                            else f"[{sc['atb']}]")
                         for sc in e["scaling"]
                         if sc.get("ratio") and sc.get("atb")]
                if parts:
                    return " + ".join(parts)
    return None


def _num(v):
    return f"{v:g}".replace(".", ",")


def build(game_dir):
    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}
    skills = {ln["id"]: ln for ln in sheets["skill"]["lines"]
              if isinstance(ln.get("id"), str)}
    root = ET.fromstring(pak_extract.read_entry(game_dir / "res.pak",
                                                "lang/export_fr.xml"))
    fr = {}
    for sheet in root.findall("sheet"):
        if sheet.get("name") != "skill":
            continue
        for row in sheet:
            name = row.find("texts.name")
            desc = row.find("texts.desc")
            ranks = [("".join(d.itertext()).strip())
                     for d in row.findall("texts.rankDescs/*/desc")]
            fr[row.tag] = {
                "name": "".join(name.itertext()).strip()
                if name is not None else "",
                "desc": "".join(desc.itertext()).strip()
                if desc is not None else "",
                "ranks": ranks}

    def fill(text, sid):
        sk = skills.get(sid) or {}
        refs = ((sk.get("texts") or {}).get("refs")) or {}

        def one(m):
            key, pct = m.group(1), m.group(2) == "%"
            src = sk
            mref = re.match(r"ref(\d?)_(.+)", key)
            if mref:
                rid = refs.get("ref" if mref.group(1) in ("", "1")
                               else "ref" + mref.group(1))
                src, key = skills.get(rid) or {}, mref.group(2)
                if key == "name":
                    return (fr.get(rid) or {}).get("name") or rid or "X"
            v = (src.get("vars") or {}).get(key)
            if not isinstance(v, (int, float)) and key == "stacks":
                v = (((src.get("props") or {}).get("status") or {})
                     .get("maxStacks"))
            if not isinstance(v, (int, float)):
                eff = None if pct else EFFECT_KEYS.get(
                    re.sub(r"\d+$", "", key))
                sc = _scaling(src, eff) if eff is not None else None
                return sc or "X"
            return f"{_num(v * 100)} %" if pct else _num(v)
        return re.sub(r"::([A-Za-z0-9_]+?)(%?)::", one, text or "")

    out = {}
    for ln in sheets["item"]["lines"]:
        if ln.get("type") != "InfusionPattern":
            continue
        sid = ((ln.get("props") or {}).get("ref") or {}).get("skill")
        if sid not in skills:
            continue
        sk, t = skills[sid], fr.get(sid) or {}
        role = sid.rsplit("_", 1)[-1]
        bonus = [[(a.get("target") or {}).get("attribute"), a.get("val"),
                  a.get("ref")]
                 for a in sk.get("affixes") or ()
                 if ((a.get("conds") or {}).get("minRank") or 0) == 2]
        six = next((r for r in t.get("ranks") or () if r), "")
        out[sid] = {"f": ln.get("faction"), "role": ROLES.get(role, role),
                    "name": t.get("name") or sid,
                    "pattern": ln["id"],
                    "t2": fill(t.get("desc"), sid),
                    "t4": bonus,
                    "t6": fill(six, sid)}
    factions = {ln["id"]: ln["faction"] for ln in sheets["item"]["lines"]
                if isinstance(ln.get("id"), str) and ln.get("faction")
                and ln.get("type") != "InfusionPattern"}
    return {"infusions": out, "item_faction": factions}
