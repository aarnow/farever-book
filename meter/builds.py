"""Builds: a character put together by hand (class, level, gear, talents,
skills) under the game's rules (hltools/build_data.py), saved in builds/.

to_profile turns a build into the hook's live-player profile, so the sheet
and stats reuse the Inspecter's code."""
from __future__ import annotations

import base64
import json
import re
import sys
import time
import zlib

from common import BUILDS_DIR
from gamedata import _offsets, build_data, talent_data
from gearstats import EQUIP_SLOTS

CLASSES = ("Warrior", "Mage", "Priest", "Rogue")
# the build's slots, and the catalogue's slot kind for each
SLOT_KIND = {"Weapon1": "Weapon", "OffhandWeapon": "Offhand",
             "Weapon2": "Weapon", "Head": "Head", "Neck": "Neck",
             "Shoulders": "Shoulders", "Chest": "Chest", "Back": "Back",
             "Hands": "Hands", "Waist": "Waist", "Legs": "Legs",
             "Feet": "Feet", "FingerLeft": "Finger", "Trinket": "Trinket",
             "FingerRight": "Finger"}
ARMOUR = {"Head", "Shoulders", "Chest", "Back", "Hands", "Waist", "Legs",
          "Feet"}
# the infusion bonus can be any of the InfusionBonus aptitude's ratings
INFUSION_STATS = ("CritChanceRating", "FervorRating",
                  "ArmorPenetrationRating", "SpellPenetrationRating")
CLASS_SKILL_SLOTS = 4
WEAPON_SKILL_TYPES = ("WeaponSkill",)
PASSIVE_TYPES = ("WeaponPassive",)


# ---- share codes ------------------------------------------------------------
# "FFB1:" + the SHARE_KEYS as compact JSON, zlib, base64url (no padding).
# The digit is the format version.
SHARE_PREFIX = "FFB1:"
SHARE_KEYS = ("name", "cls", "lvl", "gear", "skills", "talents", "runes")


def share_code(b):
    raw = json.dumps({k: b.get(k) for k in SHARE_KEYS if b.get(k)},
                     separators=(",", ":"), ensure_ascii=False)
    z = zlib.compress(raw.encode("utf-8"), 9)
    return SHARE_PREFIX + base64.urlsafe_b64encode(z).decode().rstrip("=")


def from_code(text):
    """The build a share code holds, brought within the rules (what this
    game no longer has is dropped). ValueError with a message to show."""
    m = re.search(r"FFB(\d+):([A-Za-z0-9_-]+)", str(text or ""))
    if not m:
        raise ValueError("Ce n'est pas un code de build Farever Book.")
    if m.group(1) != "1":
        raise ValueError("Ce code vient d'une version plus récente de "
                         "Farever Book.")
    body = m.group(2)
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(
            body + "=" * (-len(body) % 4)))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("Code incomplet ou abîmé : recopie-le en entier.")
    if not isinstance(data, dict) or data.get("cls") not in CLASSES:
        raise ValueError("Code illisible : classe inconnue.")
    b = new_build(str(data.get("name") or "Build importé")[:60],
                  data["cls"], data.get("lvl"))
    for k in ("gear", "skills", "talents", "runes"):
        if isinstance(data.get(k), dict):
            b[k] = data[k]
    return normalize(b)


def new_build(name, cls="Priest", lvl=None):
    d = build_data()
    return {"name": name, "cls": cls if cls in CLASSES else "Priest",
            "lvl": int(lvl or d.get("maxLevel") or 25), "gear": {},
            "sim": {"armor": 30, "enemy": None, "hit": 300},
            "talents": {}, "skills": {"class": [], "weapon": [],
                                      "arsenal": []},
            "at": time.time()}


def item(kind):
    return (build_data().get("items") or {}).get(kind)


def options(b, slot):
    """The items a slot can take in this build: the class's, of the slot's
    kind; a shield only behind a one-handed main weapon."""
    kind = SLOT_KIND.get(slot)
    if kind is None:
        return []
    if slot == "OffhandWeapon" and not offhand_allowed(b):
        return []
    # one weapon cannot be both the main one and the arsenal's
    other = {"Weapon1": "Weapon2", "Weapon2": "Weapon1"}.get(slot)
    taken = (b["gear"].get(other) or {}).get("id") if other else None
    return sorted(k for k, e in (build_data().get("items") or {}).items()
                  if e["slot"] == kind and b["cls"] in e["cls"]
                  and k != taken)


def offhand_allowed(b):
    main = (b["gear"].get("Weapon1") or {}).get("id")
    e = item(main) if main else None
    return bool(e and e.get("shield"))


def is_weapon(kind):
    e = item(kind)
    return bool(e and e["slot"] in ("Weapon", "Offhand"))


def max_upgrades(rarity, kind=None):
    """Only weapons are upgraded, up to their rarity's gearUpgrades."""
    if kind is not None and not is_weapon(kind):
        return 0
    return int((build_data().get("upgrades") or {}).get(rarity) or 0)


def prismatic_flag():
    """The item flags value of a prismatic copy (st.ItemFlag bit)."""
    return 1 << int((_offsets().get("ItemFlag") or {}).get("Prismatic", 1))


def aug_kinds(kind):
    e = item(kind)
    return list((build_data().get("accepts") or {}).get(e["type"]) or ()) \
        if e else []


def infusable(piece):
    """$HInfusion.canBeInfused: armour, from the minimum rarity, with a
    faction."""
    e = item(piece.get("id"))
    if not e or e["slot"] not in ARMOUR or not e.get("fac"):
        return False
    rar = build_data().get("rarities") or []
    need = build_data().get("infusionMinRarity")
    r = piece.get("rar")
    return (r in rar and need in rar and rar.index(r) >= rar.index(need))


# a demonic sigil's class, from its id (DemonSigil_War_..., _Priest_...)
SIGIL_CLASS = {"War": "Warrior", "Priest": "Priest", "Mage": "Mage",
               "Rogue": "Rogue"}


def sigil_ok(b, aug):
    """A demonic sigil fits the build's class (its talent is the class's)."""
    parts = str(aug).split("_")
    return len(parts) > 1 and SIGIL_CLASS.get(parts[1]) == b["cls"]


def granted_talents(b):
    """The talents the gear grants: a demonic sigil on the head."""
    from gamedata import _augments_data
    out = []
    for p in (b.get("gear") or {}).values():
        for aug in (p.get("augs") or {}).values():
            a = _augments_data().get(aug) or {}
            if a.get("t") == "AugmentDemonSigil":
                out += list(a.get("s") or ())
    return out


def talent_points(b):
    d = build_data()
    return max(0, int(b["lvl"]) - int(d.get("talentsFrom") or 10) + 1)


def tree(b):
    return (talent_data().get("trees") or {}).get(b["cls"]) or {}


def _tier_ok(ranks, t, talents, tiers):
    """implSetTalentRank: the points in lower tiers of the talent's own
    branch (and the root) reach the tier's threshold."""
    need = tiers[t["tier"]] if t["tier"] < len(tiers) else 0
    have = sum(ranks.get(x["s"], 0) for x in talents
               if x["tier"] < t["tier"]
               and (x["branch"] == "Root" or t["branch"] == "Root"
                    or x["branch"] == t["branch"]))
    return have >= need


def _talents_valid(b, ranks):
    tr = tree(b)
    talents = tr.get("talents") or []
    tiers = build_data().get("tiers") or [0, 1, 2, 4, 8]
    if sum(ranks.values()) > talent_points(b):
        return False
    by = {t["s"]: t for t in talents}
    for sid, r in ranks.items():
        t = by.get(sid)
        if t is None or r < 0 or r > t["max"]:
            return False
        if r and not _tier_ok(ranks, t, talents, tiers):
            return False
    return True


def talent_view(b):
    """The tree with each talent's rank and whether a point can be added or
    taken back."""
    tr = tree(b)
    ranks = {k: int(v) for k, v in (b.get("talents") or {}).items() if v}
    gifts = set(granted_talents(b))
    out = {}
    for t in tr.get("talents") or ():
        r = ranks.get(t["s"], 0)
        if t["s"] in gifts and not r:
            out[t["s"]] = {"rank": t["max"], "max": t["max"], "add": False,
                           "remove": False, "gift": True}
            continue
        up = dict(ranks, **{t["s"]: r + 1})
        down = dict(ranks, **{t["s"]: r - 1})
        out[t["s"]] = {"rank": r, "max": t["max"],
                       "add": r < t["max"] and _talents_valid(b, up),
                       "remove": r > 0 and _talents_valid(b, down)}
    return out


def set_talent(b, sid, delta):
    ranks = {k: int(v) for k, v in (b.get("talents") or {}).items() if v}
    r = ranks.get(sid, 0) + (1 if delta > 0 else -1)
    trial = dict(ranks, **{sid: r})
    trial = {k: v for k, v in trial.items() if v}
    if _talents_valid(b, trial):
        b["talents"] = trial
        return True
    return False


def _skills_of(b, slot, types):
    e = item((b["gear"].get(slot) or {}).get("id"))
    return [s["id"] for s in (e or {}).get("skills") or ()
            if s.get("type") in types]


def skill_options(b):
    """-> (options, slot counts) per skill group. Weapon skills are not a
    choice: main weapon's then off hand's, as many as UnlockLevel_
    WeaponSkillSlots opens. Arsenal: its skills and passive (UnlockLevel_
    Arsenal slots). Class: those unlocked at this level, 4 slots."""
    d = build_data()
    lvl = int(b["lvl"])
    cls = (d.get("classes") or {}).get(b["cls"]) or {}
    opts = {"class": [s["id"] for s in cls.get("skills") or ()
                      if (s.get("lvl") or 1) <= lvl],
            "weapon": (_skills_of(b, "Weapon1", WEAPON_SKILL_TYPES)
                       + _skills_of(b, "OffhandWeapon", WEAPON_SKILL_TYPES)),
            "arsenal": _skills_of(b, "Weapon2",
                                  WEAPON_SKILL_TYPES + PASSIVE_TYPES)}
    slots = {"class": CLASS_SKILL_SLOTS,
             "weapon": sum(1 for x in d.get("weaponSkillLevels") or (1, 2)
                           if x <= lvl),
             "arsenal": sum(1 for x in d.get("arsenalLevels") or (7, 20)
                            if x <= lvl)}
    return opts, slots


def normalize(b):
    """Bring a build back within the rules after any change (class, level,
    a weapon swapped...): what no longer fits is dropped."""
    d = build_data()
    b["cls"] = b.get("cls") if b.get("cls") in CLASSES else "Priest"
    b["lvl"] = max(1, min(int(b.get("lvl") or 1), int(d.get("maxLevel")
                                                         or 25)))
    rar = d.get("rarities") or []
    gear = {}
    for slot in SLOT_KIND:
        p = (b.get("gear") or {}).get(slot)
        if not p or p.get("id") not in options(dict(b, gear=gear), slot):
            continue
        p = dict(p)
        p["rar"] = p.get("rar") if p.get("rar") in rar else \
            (item(p["id"]).get("rar") or "Rare")
        p["lvl"] = max(1, min(int(p.get("lvl") or b["lvl"]),
                              int(d.get("maxLevel") or 25)))
        p["up"] = max(0, min(int(p.get("up") or 0),
                             max_upgrades(p["rar"], p["id"])))
        p["prism"] = bool(p.get("prism"))
        kinds = aug_kinds(p["id"])
        augs = d.get("augments") or {}
        p["augs"] = {k: v for k, v in (p.get("augs") or {}).items()
                     if k in kinds and v in (augs.get(k) or {}).get("items", ())
                     and (k != "AugmentDemonSigil" or sigil_ok(b, v))}
        if not infusable(p):
            # ent.Hero.makeLootItem rolls prismatic only on a piece that
            # can be infused (and gets an infusion bonus stat)
            p.pop("inf", None)
            p.pop("istat", None)
            p["prism"] = False
        elif p.get("inf") not in (d.get("infusions") or ()):
            p.pop("inf", None)
        if p.get("istat") not in istat_options(p):
            p["istat"] = None           # set on its own: a planned bonus
        gear[slot] = p
    b["gear"] = gear
    ranks = {k: int(v) for k, v in (b.get("talents") or {}).items() if v}
    while ranks and not _talents_valid(b, ranks):
        ranks.pop(max(ranks, key=lambda k: next(
            (t["tier"] for t in tree(b).get("talents") or () if t["s"] == k),
            99)))                                       # highest tier first
    b["talents"] = ranks
    opts, slots = skill_options(b)
    sk = b.get("skills") or {}
    # each slot keeps its place: an empty slot stays empty (None)
    b["skills"] = {}
    for g in ("class", "arsenal"):
        cur = [s if s in opts[g] else None for s in (sk.get(g) or [])]
        cur = cur[:slots[g]]
        while cur and cur[-1] is None:
            cur.pop()
        b["skills"][g] = cur
    b["skills"]["weapon"] = opts["weapon"][:slots["weapon"]]
    # runes: the character's own, one per class skill whether it is on
    # the bar or not, among that skill's runes
    info = d.get("skillInfo") or {}
    mine = set(rune_skills(b))
    b["runes"] = {s: r for s, r in (b.get("runes") or {}).items()
                  if s in mine and r in {x["id"] for x in
                                           (info.get(s) or {}).get("runes")
                                           or ()}}
    return b


def rune_skills(b):
    """The class's skills that have runes, all of them (runes are set for
    the character, not for the bar)."""
    d = build_data()
    info = d.get("skillInfo") or {}
    cls = (d.get("classes") or {}).get(b.get("cls")) or {}
    return [s["id"] for s in cls.get("skills") or ()
            if (info.get(s["id"]) or {}).get("runes")]


def bar_skills(b):
    """The skills on the bar: the weapon's, the arsenal's, the class's."""
    sk = b.get("skills") or {}
    return [s for g in ("weapon", "arsenal", "class")
            for s in sk.get(g) or () if s]


def passives(b):
    """The passives at work: the class's (by level), the main weapon's and
    the off hand's, and the arsenal's when it is one of the two picked."""
    d = build_data()
    cls = (d.get("classes") or {}).get(b["cls"]) or {}
    out = [s["id"] for s in cls.get("passives") or ()
           if (s.get("lvl") or 1) <= int(b["lvl"])]
    out += _skills_of(b, "Weapon1", PASSIVE_TYPES)
    out += _skills_of(b, "OffhandWeapon", PASSIVE_TYPES)
    picked = set((b.get("skills") or {}).get("arsenal") or ())
    out += [s for s in _skills_of(b, "Weapon2", PASSIVE_TYPES) if s in picked]
    return out


# ---- the build as a profile --------------------------------------------------
def to_profile(b):
    """The build in the hook's profile format, for character_view."""
    equip = [None] * len(EQUIP_SLOTS)
    for slot, p in (b.get("gear") or {}).items():
        if slot not in EQUIP_SLOTS or not p.get("id"):
            continue
        equip[EQUIP_SLOTS.index(slot)] = [
            p["id"], p.get("rar"), p.get("lvl"), p.get("up") or 0,
            list((p.get("augs") or {}).values()), [], p.get("inf"),
            p.get("istat"),
            prismatic_flag() if p.get("prism") else 0]
    sk = b.get("skills") or {}
    arsenals = {}
    for group, slot in (("weapon", "Weapon1"), ("arsenal", "Weapon2")):
        wid = (b["gear"].get(slot) or {}).get("id")
        if wid:
            arsenals[wid] = [s for s in sk.get(group) or () if s]
    tr = tree(b)
    skills = ([s for s in sk.get("class") or () if s] + passives(b)
              + granted_talents(b))
    return {"n": b.get("name"), "k": b["cls"], "lvl": b["lvl"], "me": False,
            "at": b.get("at") or time.time(), "equip": equip,
            "talents": dict(b.get("talents") or {}),
            "slots": list(sk.get("class") or []),
            "weaponSkills": [s for s in list(sk.get("weapon") or [])
                             + list(sk.get("arsenal") or []) if s],
            "arsenals": arsenals, "skills": skills, "statuses": [],
            "masteries": list((b.get("runes") or {}).values()),
            "prayers": [],
            "root": tr.get("root")}


def from_profile(prof, name):
    """A build from a player analysed in Inspecter."""
    b = new_build(name, prof.get("k"), prof.get("lvl"))
    for i, row in enumerate(prof.get("equip") or ()):
        if not row or i >= len(EQUIP_SLOTS):
            continue
        kind, rar, lvl, upg, gslots, _eff, infu, istat, flags = (
            list(row) + [None] * 9)[:9]
        e = item(kind)
        if not e:
            continue
        augs = {}
        for g in gslots or ():
            for k, a in (build_data().get("augments") or {}).items():
                if g in a.get("items", ()):
                    augs[k] = g
        b["gear"][EQUIP_SLOTS[i]] = {
            "id": kind, "rar": rar or e.get("rar"),
            "lvl": lvl if isinstance(lvl, int) and lvl > 0 else b["lvl"],
            "up": upg if isinstance(upg, int) else 0, "augs": augs,
            "inf": infu or None, "istat": istat or None,
            "prism": isinstance(flags, int)
            and bool(flags & prismatic_flag())}
    tal = prof.get("talents")
    b["talents"] = ({k: int(v) for k, v in tal.items()}
                    if isinstance(tal, dict) else {})
    ars = prof.get("arsenals") or {}
    w1 = (b["gear"].get("Weapon1") or {}).get("id")
    w2 = (b["gear"].get("Weapon2") or {}).get("id")
    b["skills"] = {"class": list(prof.get("slots") or []),
                   "weapon": list(ars.get(w1) or []),
                   "arsenal": list(ars.get(w2) or [])}
    return normalize(b)


# ---- saving --------------------------------------------------------------------
def _file_name(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "build"
    return f"build-{slug}-{int(time.time())}.json"


def list_builds():
    """[(file name, build)], the most recently changed first."""
    out = []
    try:
        files = sorted(BUILDS_DIR.glob("build-*.json"))
    except OSError:
        return out
    for f in files:
        try:
            b = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(b, dict) and b.get("cls"):
            out.append((f.name, b))
    out.sort(key=lambda fb: -(fb[1].get("at") or 0))
    return out


def save_build(b, file=None):
    """Write the build; a new one gets a file name. -> the file name."""
    file = file or _file_name(b.get("name") or "build")
    b["at"] = time.time()
    try:
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
        (BUILDS_DIR / file).write_text(json.dumps(b, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")
    except OSError as e:
        print(f"[meter] couldn't save the build: {e}", file=sys.stderr)
    return file


def delete_build(file):
    name = str(file or "")
    if not (name.startswith("build-") and name.endswith(".json")
            and "/" not in name and "\\" not in name):
        return False
    try:
        (BUILDS_DIR / name).unlink()
        return True
    except OSError:
        return False


# ---- the guided build ----------------------------------------------------------
# The player's wishes (class, level, weapons, two attributes, three stats)
# -> best-fitting pieces, augments and infusion. Talents and runes are left
# to the player.
GUIDE_ATTRIBUTES = ("Vitality", "Strength", "Dexterity", "Faith", "Intellect")
GUIDE_STATS = ("CritChanceRating", "FervorRating", "ArmorPenetrationRating",
               "SpellPenetrationRating")
# how much each wish counts: main attribute, second; first stat, 2nd, 3rd;
# and a little vitality, which everyone needs
GUIDE_WEIGHTS = {"atb": (1.0, 0.55), "stat": (0.9, 0.6, 0.35),
                 "vitality": 0.15}
ROLE_OF = {"Vitality": "Tank", "Faith": "Support"}      # else DPS
GUIDE_ROLES = ("DPS", "Support", "Tank")


def guide_role(p):
    """The role the player picked, else the one the main attribute says."""
    return p.get("role") if p.get("role") in GUIDE_ROLES else \
        ROLE_OF.get((p.get("atbs") or [None])[0], "DPS")
# the guide's goal -> infusions worn as (role, pieces): "main" the main
# attribute's role, "other" Tank (DPS for a tank). Pieces count whatever
# their faction (ent.Hero.refreshInfusions); tiers at 2, 4 and 6.
GUIDE_GOALS = {"max": (("main", 6), ("main", 2)),
               "mix": (("main", 6), ("other", 2)),
               "surv": (("main", 4), ("other", 4))}
# a 2-piece tier is an effect the sheet can't weigh: those that work with
# no condition first (in combat: a shield every 5 s, a mastery bonus...)
GUIDE_INFUSION_PREF = ("Infusion_Bee_Tank", "Infusion_Manfish_Tank",
                       "Infusion_Crimson_DPS", "Infusion_Kobold_DPS",
                       "Infusion_Crimson_Support", "Infusion_Kobold_Support")


def istat_options(piece):
    """The infusion bonus stats a piece can roll: one of the four, minus
    those it already has ($HInfusion.pickInfusionBonusStat)."""
    from gearstats import gear_stats
    e = item(piece.get("id")) or {}
    got = gear_stats(piece.get("id"), piece.get("rar") or e.get("rar"),
                     piece.get("lvl"), 0, [], 0)
    own = {a for a, _n, _v in (got[1] if got else ())}
    return [s for s in INFUSION_STATS if s not in own]


def guide_istat(piece, stats):
    """The bonus a guided build aims for: the player's first stat the piece
    can roll, else the first it can."""
    can = istat_options(piece)
    return next((s for s in stats if s in can), can[0] if can else "")


def guide_weights(atbs, stats):
    w = {"Vitality": GUIDE_WEIGHTS["vitality"]}
    for k, v in zip(atbs, GUIDE_WEIGHTS["atb"]):
        if k:
            w[k] = w.get(k, 0) + v
    for k, v in zip(stats, GUIDE_WEIGHTS["stat"]):
        if k:
            w[k] = w.get(k, 0) + v
    return w


def _best(cands, weights):
    """The candidate whose values fit the weights best: each attribute
    counted against the best value any candidate has of it (so a rating
    of 60 weighs like a primary of 20 when those are each slot's tops)."""
    tops = {}
    for _k, vals in cands:
        for a, v in vals.items():
            tops[a] = max(tops.get(a, 0), v)

    def score(vals):
        return sum(w * vals.get(a, 0) / tops[a]
                   for a, w in weights.items() if tops.get(a))
    ranked = sorted(cands, key=lambda kv: -score(kv[1]))
    return [k for k, vals in ranked if score(vals) > 0] or \
        [k for k, _v in ranked]


def guided_build(p):
    """A build from the guide's answers ({cls, lvl, main, off, ars, atbs:
    [main, second], stats: [1st, 2nd, 3rd]}), within the rules."""
    from gamedata import _augments_data, infusion_data
    from gearstats import gear_stats
    cls = p.get("cls") if p.get("cls") in CLASSES else "Priest"
    b = new_build("Build guidé", cls, p.get("lvl"))
    lvl = b["lvl"]
    weights = guide_weights(list(p.get("atbs") or ())[:2],
                            list(p.get("stats") or ())[:3])

    def piece(kind):
        e = item(kind) or {}
        rar = e.get("rar") or "Rare"
        return {"id": kind, "rar": rar, "lvl": lvl,
                "up": max_upgrades(rar, kind), "prism": False, "augs": {},
                "inf": "", "istat": ""}

    def top(kind):
        """A weapon at its best: legendary, fully upgraded."""
        pc = piece(kind)
        pc["rar"] = "Legendary"
        pc["up"] = max_upgrades("Legendary", kind)
        return pc

    for slot, key in (("Weapon1", "main"), ("Weapon2", "ars")):
        if p.get(key) in options(b, slot):
            b["gear"][slot] = top(p[key])
    if p.get("off") and p["off"] in options(b, "OffhandWeapon"):
        b["gear"]["OffhandWeapon"] = top(p["off"])

    def stats_of(kind):
        got = gear_stats(kind, (item(kind) or {}).get("rar"), lvl, 0, [], 0)
        return {a: v for a, _n, v in (got[1] if got else ())}

    used = set()
    for slot in SLOT_KIND:
        if slot.startswith(("Weapon", "Offhand")):
            continue
        cands = [(k, stats_of(k)) for k in options(b, slot) if k not in used]
        cands = [c for c in cands if c[1]]
        if not cands:
            continue
        kind = _best(cands, weights)[0]
        used.add(kind)                      # two rings: two different ones
        b["gear"][slot] = piece(kind)

    # each augment slot: the augment whose effects fit (those that only
    # grant a skill or a talent are the player's call)
    augs = (build_data().get("augments") or {})
    adata = _augments_data()
    for slot, pc in b["gear"].items():
        for kind in aug_kinds(pc["id"]):
            cands = []
            for aid in (augs.get(kind) or {}).get("items") or ():
                if kind == "AugmentDemonSigil" and not sigil_ok(b, aid):
                    continue
                vals = {}
                for a, v in (adata.get(aid) or {}).get("a") or ():
                    vals[a] = vals.get(a, 0) + v
                if vals and all(v > 0 for v in vals.values()):
                    cands.append((aid, vals))
            if cands:
                pc["augs"][kind] = _best(cands, weights)[0]

    # the faction armour: the infusion of the faction most of it is from,
    # for the role the main attribute says, its bonus on the first stat
    infs = infusion_data().get("infusions") or {}
    role = ROLE_OF.get((p.get("atbs") or [None])[0], "DPS")
    pieces = [pc for pc in b["gear"].values() if infusable(pc)]
    facs = {}
    for pc in pieces:
        f = (item(pc["id"]) or {}).get("fac")
        facs[f] = facs.get(f, 0) + 1
    if facs:
        fac = max(facs, key=facs.get)
        inf = next((sid for sid, e in infs.items()
                    if e.get("f") == fac and sid.endswith("_" + role)),
                   next((sid for sid, e in infs.items() if e.get("f") == fac),
                        ""))
        for pc in pieces:
            pc["inf"], pc["prism"] = inf, True
            pc["istat"] = guide_istat(pc, p.get("stats") or ())

    b = normalize(b)
    # the class's skills, in their slots (the weapons' come with them)
    opts, slots = skill_options(b)
    if not any(b["skills"].get("class") or ()):
        b["skills"]["class"] = list(opts.get("class") or ())[:4]
    if not any(b["skills"].get("arsenal") or ()):
        b["skills"]["arsenal"] = list(opts.get("arsenal") or ())[
            :slots.get("arsenal", 0)]
    return normalize(b)
