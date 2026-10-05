"""The gear infusions: what each does, for the Character tab, from data.cdb
(its English texts) and the game's French text (res.pak lang/export_fr.xml):
each entry in French, its "en" in English.

An infusion is a passive skill (type InfusionPassive) set on a gear piece by
an infusion pattern (item type InfusionPattern, which names the faction).
Worn on several pieces it reaches tiers, as the game's tooltip shows them:

* 2 pieces: the skill's description;
* 4 pieces: its affixes conditioned on minRank 2 (an attribute bonus);
* 6 pieces: its rank description (texts.rankDescs, the non-empty line).

The texts' ::placeholders:: are filled by skilltext.py.

Each gear piece also has a faction (item.faction): the infusion's bonus stat
only applies when the two match, so the gear factions are emitted too."""
from pathlib import Path

from skilltext import SkillText

ROLES = {"fr": {"Tank": "Tank", "Support": "Soutien", "DPS": "Dégâts"},
         "en": {"Tank": "Tank", "Support": "Support", "DPS": "Damage"}}


def build(game_dir):
    game_dir = Path(game_dir)
    st = SkillText(game_dir)
    sheets, skills = st.sheets, st.skills
    out = {}
    for ln in sheets["item"]["lines"]:
        if ln.get("type") != "InfusionPattern":
            continue
        sid = ((ln.get("props") or {}).get("ref") or {}).get("skill")
        if sid not in skills:
            continue
        sk = skills[sid]
        role = sid.rsplit("_", 1)[-1]
        bonus = [[(a.get("target") or {}).get("attribute"), a.get("val"),
                  a.get("ref")]
                 for a in sk.get("affixes") or ()
                 if ((a.get("conds") or {}).get("minRank") or 0) == 2]

        def words(lang):
            t = st.tip(sid, lang)
            return {"role": ROLES[lang].get(role, role),
                    "name": t["name"],
                    "t2": t["desc"],
                    "t6": next((r for r in t["ranks"] if r), "")}
        out[sid] = dict(words("fr"), f=ln.get("faction"), pattern=ln["id"],
                        t4=bonus, en=words("en"))
    factions = {ln["id"]: ln["faction"] for ln in sheets["item"]["lines"]
                if isinstance(ln.get("id"), str) and ln.get("faction")
                and ln.get("type") != "InfusionPattern"}
    return {"infusions": out, "item_faction": factions}
