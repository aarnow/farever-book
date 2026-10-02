"""Builds: a character put together by hand — class, level, gear (rarity,
level, upgrades, augments, infusion), talents and skills — under the rules
the game enforces (hltools/build_data.py), saved in builds/.

A build is turned into the same profile the hook reads from a live player
(to_profile), so the character sheet, the gear stats and the attributes are
the Inspecter's own, already checked against the game."""
from __future__ import annotations

import json
import re
import sys
import time

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


# ---- the build ---------------------------------------------------------------
def new_build(name, cls="Priest", lvl=None):
    d = build_data()
    return {"name": name, "cls": cls if cls in CLASSES else "Priest",
            "lvl": int(lvl or d.get("maxLevel") or 25), "gear": {},
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
    return sorted(k for k, e in (build_data().get("items") or {}).items()
                  if e["slot"] == kind and b["cls"] in e["cls"])


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
    out = {}
    for t in tr.get("talents") or ():
        r = ranks.get(t["s"], 0)
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
    """What each skill group holds or can hold now. The weapon's are not a
    choice: the main weapon's skills then the off hand's, as many as the
    slots open at this level (UnlockLevel_WeaponSkillSlots: "Weapon 1
    second skill or off-hand skill"). The arsenal's are picked among its
    skills and passive (UnlockLevel_Arsenal slots); the class's among those
    unlocked at this level, 4 slots."""
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
                     if k in kinds and v in (augs.get(k) or {}).get("items", ())}
        if not infusable(p):
            # ent.Hero.makeLootItem rolls prismatic only on a piece that
            # can be infused (and gets an infusion bonus stat)
            p.pop("inf", None)
            p.pop("istat", None)
            p["prism"] = False
        elif p.get("inf") not in (d.get("infusions") or ()):
            p.pop("inf", None)
        if p.get("istat") not in INFUSION_STATS:
            p["istat"] = INFUSION_STATS[0] if p.get("inf") else None
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
    return b


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
            p.get("istat") if p.get("inf") else None,
            prismatic_flag() if p.get("prism") else 0]
    sk = b.get("skills") or {}
    arsenals = {}
    for group, slot in (("weapon", "Weapon1"), ("arsenal", "Weapon2")):
        wid = (b["gear"].get(slot) or {}).get("id")
        if wid:
            arsenals[wid] = [s for s in sk.get(group) or () if s]
    tr = tree(b)
    skills = [s for s in sk.get("class") or () if s] + passives(b)
    return {"n": b.get("name"), "k": b["cls"], "lvl": b["lvl"], "me": False,
            "at": b.get("at") or time.time(), "equip": equip,
            "talents": dict(b.get("talents") or {}),
            "slots": list(sk.get("class") or []),
            "weaponSkills": [s for s in list(sk.get("weapon") or [])
                             + list(sk.get("arsenal") or []) if s],
            "arsenals": arsenals, "skills": skills, "statuses": [],
            "masteries": [], "prayers": [],
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
