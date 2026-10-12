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
only applies when the two match, so the gear factions are emitted too.

An infusion is learnt from its pattern, which a boss drops: the loot table
holding it is that boss's (unit props.lootTable or bossLootTable), and the
game's crucible says to unlock it "Vaincre : <boss> héroïque" (text
game_unlock). That boss and the crucible's own words (its name, the tiers'
"(2:) Définir", "Équipé(e)") are emitted, in French and in English."""
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
    # the boss dropping each pattern: its loot table's owner
    holder = {}
    for t in sheets.get("lootTable", {}).get("lines") or ():
        for e in t.get("loot") or ():
            holder.setdefault(e.get("item"), t.get("id"))
    owner = {}
    for u in sheets.get("unit", {}).get("lines") or ():
        props = u.get("props") or {}
        for key in ("lootTable", "bossLootTable"):
            if isinstance(props.get(key), str):
                owner.setdefault(props[key], u["id"])
    units = {u["id"]: u for u in sheets.get("unit", {}).get("lines") or ()
             if isinstance(u.get("id"), str)}
    fr = st.fr_root
    fr_units = {}
    for sheet in fr.findall("sheet"):
        if sheet.get("name") == "unit":
            for row in sheet:
                nm = row.find("texts.name")
                if nm is not None:
                    fr_units[row.tag] = "".join(nm.itertext()).strip()
    bosses = {}
    for sid, e in out.items():
        boss = owner.get(holder.get(e["pattern"]))
        if boss:
            e["boss"] = boss
            bosses[boss] = {"fr": fr_units.get(boss) or "",
                            "en": ((units.get(boss) or {}).get("texts") or {}).get("name") or ""}
    return {"infusions": out, "item_faction": factions, "bosses": bosses,
            "ui": _ui_words(sheets, fr)}


def _ui_words(sheets, fr):
    """The crucible's words, as the game writes them: its name, how to
    unlock (from a boss), a tier's label, "equipped"."""
    def fr_text(sheet_name, path):
        for sheet in fr.findall("sheet"):
            if sheet.get("name") == sheet_name:
                node = sheet.find(path)
                return "".join(node.itertext()).strip() if node is not None else ""
        return ""
    text = {ln.get("id"): ln for ln in sheets.get("text", {}).get("lines") or ()}
    icon = {ln.get("id"): ln for ln in sheets.get("icon", {}).get("lines") or ()}

    def en_group(tid, gid):
        for g in ((text.get(tid) or {}).get("value") or {}).get("group") or ():
            if g.get("id") == gid:
                return (g.get("value") or {}).get("text") or ""
        return ""

    def en_text(tid):
        return ((text.get(tid) or {}).get("value") or {}).get("text") or ""
    return {
        "fr": {"crucible": fr_text("icon", "InfusionUI/name"),
               "unlock": fr_text("text", "game_unlock/value.group/title/value.text"),
               "boss": fr_text("text", "game_unlock/value.group/Boss/value.text"),
               "rank": fr_text("text", "infusion_rank/value.text"),
               "equipped": fr_text("text", "inventory_equipped_desc/value.text")},
        "en": {"crucible": (icon.get("InfusionUI") or {}).get("name") or "",
               "unlock": en_group("game_unlock", "title"),
               "boss": en_group("game_unlock", "Boss"),
               "rank": en_text("infusion_rank"),
               "equipped": en_text("inventory_equipped_desc")}}
