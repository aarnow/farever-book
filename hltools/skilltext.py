"""The game's skill texts, filled: a skill's description and its rank
descriptions with their ::placeholders:: computed from data.cdb, in French
(res.pak lang/export_fr.xml) and in English (data.cdb's own texts). The
[Id] references stay: the app names them in the interface's language.

A placeholder names a variable of the skill (::var1::) or of a skill it
refers to (::ref_var1:: — ref, ref2, ref3; ref1 is ref), the referred
skill's name (::ref_name::); a trailing % shows a ratio as a percentage.
Where the game computes it, it is taken from: the skill's vars, its own
fields (duration, cooldown, range), its affixes (::val1::, ::val2:: — their
distinct values in order), its status's maxStacks (::stacks::), and the
stat scaling of its effects (::dmg:: / ::heal:: / ::shield::: "35 % de
[MaxHealth]"). What only the game's scripts compute reads "X".

skill_tips.json: for every skill the game describes, its cooldown and
both languages' texts, and the kills a weapon skill's rank needs
(WeaponKills_PerSkillRankPoint, main hand and off hand)."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pak_extract

# ::heal:: / ::dmg:: / ::shield:: -> the effect kind (skill@steps@effects
# .effect) whose stat scaling gives the amount
EFFECT_KEYS = {"heal": 1, "dmg": 0, "damage": 0, "shield": 2}
# placeholders that are a time, shown in seconds
TIME_KEY = re.compile(r"^(time|dur|duration|cooldown|delay|tick)\d*$")
_OF = {"fr": "{pct} % de {atb}", "en": "{pct}% of {atb}"}
_MAIN_ATB = {"fr": "votre attribut principal", "en": "your main attribute"}


def num(v, lang):
    return f"{v:g}".replace(".", ",") if lang == "fr" else f"{v:g}"


def pct(v, lang):
    return f"{num(v, lang)} %" if lang == "fr" else f"{num(v, lang)}%"


class SkillText:
    def __init__(self, game_dir):
        game_dir = Path(game_dir)
        cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                                "data.cdb"))
        self.sheets = {s["name"]: s for s in cdb["sheets"]}
        self.skills = {ln["id"]: ln for ln in self.sheets["skill"]["lines"]
                       if isinstance(ln.get("id"), str)}
        self.consts = {ln["id"]: ln for ln in self.sheets["constant"]["lines"]
                       if isinstance(ln.get("id"), str)}
        self.texts = {"fr": {}, "en": {}}
        root = ET.fromstring(pak_extract.read_entry(game_dir / "res.pak",
                                                    "lang/export_fr.xml"))
        self.fr_root = root                 # the game's French texts, whole
        # the game's terms ([BasicAttack] in a text: sheet gameTerm), each
        # word and its plural
        self.terms = {"fr": {}, "en": {}}
        for sheet in root.findall("sheet"):
            if sheet.get("name") == "gameTerm":
                for row in sheet:
                    v, pl = row.find("texts.name.v"), row.find("texts.name.plural")
                    if v is not None:
                        one = "".join(v.itertext()).strip()
                        self.terms["fr"][row.tag] = [
                            one, "".join(pl.itertext()).strip()
                            if pl is not None else one]
        for ln in self.sheets.get("gameTerm", {}).get("lines") or ():
            nm = (ln.get("texts") or {}).get("name") or {}
            if isinstance(ln.get("id"), str) and nm.get("v"):
                self.terms["en"][ln["id"]] = [nm["v"], nm.get("plural") or nm["v"]]
        for sheet in root.findall("sheet"):
            if sheet.get("name") != "skill":
                continue
            for row in sheet:
                name = row.find("texts.name")
                desc = row.find("texts.desc")
                self.texts["fr"][row.tag] = {
                    "name": "".join(name.itertext()).strip()
                    if name is not None else "",
                    "desc": "".join(desc.itertext()).strip()
                    if desc is not None else "",
                    "ranks": ["".join(d.itertext()).strip()
                              for d in row.findall("texts.rankDescs/*/desc")]}
        for sid, sk in self.skills.items():
            tx = sk.get("texts") or {}
            self.texts["en"][sid] = {
                "name": tx.get("name") or "", "desc": tx.get("desc") or "",
                "ranks": [(r.get("desc") or "").strip()
                          for r in tx.get("rankDescs") or ()
                          if isinstance(r, dict)]}

    def const(self, cid, default=None):
        v = (self.consts.get(cid) or {}).get("v") or {}
        return next(iter(v.values()), default) if isinstance(v, dict) else default

    def scaling(self, skill, effect, lang):
        """'35 % de [MaxHealth]': the first step effect of that kind, as the
        share of a stat it scales on (the game shows the computed number)."""
        for st in skill.get("steps") or ():
            for e in st.get("effects") or ():
                if e.get("effect") == effect and e.get("scaling"):
                    parts = [_OF[lang].format(
                                pct=num(sc["ratio"] * 100, lang),
                                atb=_MAIN_ATB[lang] if sc["atb"] == "AllAttributes"
                                else f"[{sc['atb']}]")
                             for sc in e["scaling"]
                             if sc.get("ratio") and sc.get("atb")]
                    if parts:
                        return " + ".join(parts)
        return None

    def value(self, src, key, rank=1):
        """A placeholder's number, from the skill `src` at `rank` (its
        props.rankOverride vars from their minRank on), or None."""
        vars_ = dict(src.get("vars") or {})
        for ov in (src.get("props") or {}).get("rankOverride") or ():
            if (ov.get("minRank") or 0) <= rank:
                vars_.update(ov.get("vars") or {})
        v = vars_.get(key)
        if isinstance(v, (int, float)):
            return v
        v = src.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
        if key == "stacks":
            return ((src.get("props") or {}).get("status") or {}).get("maxStacks")
        if re.fullmatch(r"dur\d*", key):         # a status's own duration
            v = src.get("duration")
            return v if isinstance(v, (int, float)) and v > 0 else None
        if key == "charges":
            v = (src.get("props") or {}).get("charges")
            return v if isinstance(v, (int, float)) else None
        m = re.fullmatch(r"val(\d*)", key)
        if m:
            vals = []
            affixes = src.get("affixes") or ()
            # affixes for some ranks only: those of this rank
            if any((a.get("conds") or {}).get("minRank") is not None
                   or (a.get("conds") or {}).get("maxRank") is not None
                   for a in affixes):
                affixes = [a for a in affixes
                           if ((a.get("conds") or {}).get("minRank") or -99) <= rank
                           <= ((a.get("conds") or {}).get("maxRank") or 99)]
            for a in affixes:
                if isinstance(a.get("val"), (int, float)) and a["val"] not in vals:
                    vals.append(a["val"])
            i = int(m.group(1) or 1) - 1
            return vals[i] if 0 <= i < len(vals) else None
        return None

    def fill(self, text, sid, lang, rank=1):
        sk = self.skills.get(sid) or {}
        refs = ((sk.get("texts") or {}).get("refs")) or {}

        def one(m):
            key, is_pct = m.group(1), m.group(2) == "%"
            # a chance is a percentage, % or not (the game's text formatter:
            # $HText.percentValue for "chance")
            is_pct = is_pct or bool(re.fullmatch(r"chance\d*", key))
            src = sk
            mref = re.match(r"ref(\d?)_(.+)", key)
            if mref:
                rid = refs.get("ref" if mref.group(1) in ("", "1")
                               else "ref" + mref.group(1))
                src, key = self.skills.get(rid) or {}, mref.group(2)
                if key == "name":
                    return (self.texts[lang].get(rid) or {}).get("name") or rid or "X"
            elif key == "name":
                return (self.texts[lang].get(sid) or {}).get("name") or sid
            v = self.value(src, key, rank)
            if not isinstance(v, (int, float)):
                eff = None if is_pct else EFFECT_KEYS.get(re.sub(r"\d+$", "", key))
                sc = self.scaling(src, eff, lang) if eff is not None else None
                return sc or "X"
            if is_pct:
                return pct(v * 100 if abs(v) <= 1 else v, lang)
            if TIME_KEY.match(key):
                return f"{num(v, lang)} s" if lang == "fr" else f"{num(v, lang)}s"
            return num(v, lang)
        return re.sub(r"::([A-Za-z0-9_]+?)(%?)::", one, text or "")

    def tip(self, sid, lang):
        t = self.texts[lang].get(sid) or {}
        out = {"name": t.get("name") or sid,
               "desc": self.fill(t.get("desc"), sid, lang),
               # the rank descriptions are ranks 2, 3...
               "ranks": [self.fill(r, sid, lang, i + 2)
                         for i, r in enumerate(t.get("ranks") or ())]}
        # its description at ranks 1 to 5, when its numbers change with the
        # rank (a talent's points: affixes from / up to a rank, overrides)
        by = [self.fill(t.get("desc"), sid, lang, r) for r in range(1, 6)]
        if len(set(by)) > 1:
            out["byRank"] = by
        return out


def build(game_dir):
    """skill_tips.json: every skill the game describes (the bar's, the
    passives, the infusions', the talents')."""
    st = SkillText(game_dir)
    out = {}
    for sid in sorted(st.skills):
        if not ((st.texts["en"].get(sid) or {}).get("desc")
                or (st.texts["fr"].get(sid) or {}).get("desc")):
            continue
        out[sid] = {"cd": st.skills[sid].get("cooldown"),
                    "fr": st.tip(sid, "fr"), "en": st.tip(sid, "en")}
    # a weapon's upgrade effect (st.item.Weapon.getWeaponUpgradeSkill): the
    # skill "<item type>_Upgrade", from the upgrade level SkillUnlockLevel
    # on, at the rank of the weapon's rarity (its row's index, from 0:
    # cdb.Index.initLines); its text at each rarity
    rars = [ln["id"] for ln in st.sheets["rarity"]["lines"]]
    upgrades, taken = {}, {}
    for sid in sorted(st.skills):
        if not sid.endswith("_Upgrade") or sid.startswith("Weapon_"):
            continue
        texts = {lang: [st.fill((st.texts[lang].get(sid) or {}).get("desc"),
                                sid, lang, rank) for rank in range(len(rars))]
                 for lang in ("fr", "en")}
        if texts["en"][0]:
            upgrades[sid[:-len("_Upgrade")]] = texts
        # one that cuts the damage its wearer takes (the shields':
        # "onReceiveDamageEval(hit) { hit.dmgMult -= vars.damage; }"): the
        # share taken off a hit's multiplier, at each rarity
        m = re.search(r"onReceiveDamageEval\s*\(\s*(\w+)\s*\)\s*\{\s*\1\.dmgMult\s*-=\s*vars\.(\w+)\s*;\s*\}",
                      st.skills[sid].get("script") or "")
        if m:
            taken[sid[:-len("_Upgrade")]] = [
                float(st.value(st.skills[sid], m.group(2), rank) or 0)
                for rank in range(len(rars))]
    unlock = next((g.get("v", {}).get("float")
                   for g in ((st.consts.get("GearUpgrades") or {}).get("v") or {})
                   .get("group") or () if g.get("id") == "SkillUnlockLevel"), 3)
    return {"kills": [st.const("WeaponKills_PerSkillRankPoint", 20),
                      st.const("WeaponKills_PerSkillRankPoint_OffHand", 26)],
            "skills": out,
            "terms": st.terms,
            "upgrades": {"at": int(unlock or 3), "rarities": rars,
                         "types": upgrades, "taken": taken},
            "defense": _defense(st),
            "block": _blocks(st)}


def _blocks(st):
    """How much a block stops: the block skills (PhysicalBlock, MagicBlock,
    ShieldBlock: their BlockMitigation, a flat percent), the one each item
    type has, and the items that have another. {skills: {id: %}, types:
    {item type: skill}, items: {item: skill}}."""
    skills = {}
    for sid, sk in st.skills.items():
        for a in sk.get("affixes") or ():
            if ((a.get("target") or {}).get("attribute") == "BlockMitigation"
                    and a.get("ref") == "TAttribute_Flat"
                    and isinstance(a.get("val"), (int, float))):
                skills[sid] = a["val"]

    def block_of(row):
        return next((s.get("skill") for s in row.get("skills") or ()
                     if s.get("skill") in skills), None)
    types = {ln["id"]: block_of(ln) for ln in st.sheets.get("itemType", {}).get("lines") or ()
             if isinstance(ln.get("id"), str) and block_of(ln)}
    items = {ln["id"]: block_of(ln) for ln in st.sheets["item"]["lines"]
             if isinstance(ln.get("id"), str) and block_of(ln)}
    return {"skills": skills, "types": types, "items": items}


# A condition a damage cut is put under in a script (onReceiveDamageEval),
# that the simulator can offer: its rank, magic damage only, while the
# skill recharges. Any other condition leaves the cut out.
_COND = {"rank": re.compile(r"^rank\s*>=\s*(\d+)$"),
         "magic": re.compile(r"^\w+\.isMagic$"),
         "cooldown": re.compile(r"^isInCooldown\(\)$")}


def _dmg_cuts(script):
    """What a script takes off the hits its owner receives: [(var, conds)]
    for each "<hit>.dmgMult -= vars.<var>" of its onReceiveDamageEval,
    alone or under an if of conditions the simulator knows."""
    m = re.search(r"function\s+onReceiveDamageEval\s*\(\s*(\w+)\s*\)\s*\{", script or "")
    if not m:
        return []
    hit, i, depth = m.group(1), m.end(), 1
    j = i
    while j < len(script) and depth:
        depth += {"{": 1, "}": -1}.get(script[j], 0)
        j += 1
    body = script[i:j - 1]
    out = []
    cut = rf"{hit}\.dmgMult\s*-=\s*vars\.(\w+)\s*;"
    for c in re.finditer(rf"if\s*\((.*?)\)\s*\{{\s*{cut}\s*\}}", body, re.S):
        conds = {}
        for part in (p.strip() for p in c.group(1).split("&&")):
            for k, rx in _COND.items():
                mm = rx.match(part)
                if mm:
                    conds[k] = int(mm.group(1)) if k == "rank" else True
                    break
            else:
                conds = None
                break
        if conds is not None:
            out.append((c.group(2), conds))
    # outside any if: always
    bare = re.sub(rf"if\s*\(.*?\)\s*\{{.*?\}}", "", body, flags=re.S)
    out += [(v, {}) for v in re.findall(cut, bare)]
    return out


def _defense(st):
    """The temporary defences a skill gives its user: the statuses its steps
    apply that raise Armor or MagicArmor (a share, TAttribute_ARatio), and
    the damage cuts of its script and of those statuses'. {skill: [{k:
    "armor"|"cut", atb, val, via, rank, magic, cooldown, dur}]}. A passive's
    own affixes are left out: they are on the sheet already. The weapons'
    upgrade effects are emitted apart ("upgrades")."""
    out = {}
    for sid, sk in st.skills.items():
        if sid.endswith("_Upgrade"):
            continue
        got = []
        # its statuses: those its steps apply, and those its script names
        # (Skill.<id>: one it sets on a parry...)
        sources = [(sid, sk)]
        named = [e.get("status") for step in sk.get("steps") or ()
                 for e in step.get("effects") or ()]
        named += re.findall(r"Skill\.(\w+)", sk.get("script") or "")
        for sts in named:
            if (isinstance(sts, str) and sts != sid and sts in st.skills
                    and sts not in [v for v, _s in sources]):
                sources.append((sts, st.skills[sts]))
        for via, src in sources:
            # a status's raised resistance: a buff's (its user's, its
            # allies'), not a debuff put on an enemy
            buff = any(t.get("type") == "Buff" for t in
                       ((src.get("props") or {}).get("status") or {}).get("types") or ())
            if via != sid and buff:
                for a in src.get("affixes") or ():
                    atb = (a.get("target") or {}).get("attribute")
                    conds = a.get("conds") or {}
                    if (atb in ("Armor", "MagicArmor") and a.get("ref") == "TAttribute_ARatio"
                            and isinstance(a.get("val"), (int, float)) and a["val"] > 0
                            and not set(conds) - {"minRank"}):
                        got.append({"k": "armor", "atb": atb, "val": a["val"], "via": via,
                                    "rank": int(conds.get("minRank") or 1),
                                    "dur": src.get("duration")})
            for var, conds in _dmg_cuts(src.get("script")):
                rank = int(conds.get("rank") or 1)
                v = st.value(src, var, rank)
                if isinstance(v, (int, float)) and v:
                    got.append({"k": "cut", "val": v, "via": via, "rank": rank,
                                "magic": bool(conds.get("magic")),
                                "cooldown": bool(conds.get("cooldown")),
                                "dur": src.get("duration") if via != sid else None})
        if got:
            out[sid] = got
    return out
