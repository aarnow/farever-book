"""The game's data as the app reads it: the tables generated into analysis_out/
(names, items, catalogues), their regeneration after a game patch, and the
hook's source."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import base64
import re
import subprocess
import sys
import threading
from pathlib import Path

from common import (
    ANALYSIS, CREATE_NO_WINDOW, FRIDA_DIR, FROZEN, GAME_PATH_FILE, ROOT,
    TOOL_FLAG, _pretty_id)
import i18n
from i18n import tr


def dungeon_name(kind):
    """The dungeon's French name, as the game shows it
    ("R1_POI_CleodorasNest" -> "Tronc-ruche d'Élizabeille"); the prettified
    id when the game's translation doesn't have it."""
    fr = _fr_names("activity").get(str(kind or ""))
    if fr:
        return fr
    s = re.sub(r"^R\d+_POI_(Dungeon_)?", "", str(kind or ""))
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s.replace("_", " "))
    return " ".join(s.split()) or tr("Donjon")



# The tables of analysis_out/, each read once, when first needed.
_TABLES = {}


def _table(name, empty=dict, shape=None, warn=None):
    """analysis_out/<name>, parsed (then shaped), read once; `empty()` when
    absent or unreadable (logged with `warn` when given)."""
    got = _TABLES.get(name)
    if got is None:
        try:
            got = json.loads((ANALYSIS / name).read_text(encoding="utf-8"))
        except Exception as e:
            got = empty()
            if warn:
                print(f"[meter] {name} unavailable ({e}): {warn}",
                      file=sys.stderr)
        if shape:
            got = shape(got)
        _TABLES[name] = got
    return got


def collection_catalogue():
    """{"mounts": [...], "gliders": [...], "pets": [...]}: every collectible
    with how it is obtained, from analysis_out/collection.json (built from the
    game's data and levels by hltools/collection_data.py). {} when absent."""
    return _table("collection.json")

def codex_items_catalogue():
    """[{id, rarity, type, src, uses}]: the items the game's item codex
    counts (crafting components, ores, cloth, leather), from
    analysis_out/codex_items.json (hltools/codex_items.py)."""
    return _table("codex_items.json", list)
# The game's generic [terms] (skill kinds), named in no sheet.
# (singular, plural): the texts write "[WeaponSkill]s".
FR_TERMS = {"Skill": ("compétence", "compétences"),
            "WeaponSkill": ("compétence d'arme", "compétences d'arme"),
            "ClassSkill": ("compétence de classe", "compétences de classe"),
            "ComboAttack": ("attaque combo", "attaques combo")}
EN_TERMS = {"Skill": ("skill", "skills"),
            "WeaponSkill": ("weapon skill", "weapon skills"),
            "ClassSkill": ("class skill", "class skills"),
            "ComboAttack": ("combo attack", "combo attacks")}


def _fr_ref(text):
    """The game's [Id] references in a French text, replaced by names."""
    def one(m):
        rid, plural = m.group(1), m.group(2)
        # the game's own words (skill_tips.json "terms"), in a sentence
        # without their capital
        game = ((_table("skill_tips.json").get("terms") or {})
                .get("fr" if i18n.lang() == "fr" else "en") or {})
        if rid in game:
            word = game[rid][1 if plural else 0]
            head = not text[:m.start()].strip() or text[:m.start()].rstrip()[-1] in ".!?"
            return word if head else word.lower()
        terms = FR_TERMS if i18n.lang() == "fr" else EN_TERMS
        if rid in terms:
            return terms[rid][1 if plural else 0]
        for sheet in ("zone", "unit", "activity", "item", "itemType",
                      "unitType", "skill", "attribute", "faction"):
            name = _fr_names(sheet).get(rid)
            if name:
                return name + plural
        return _pretty_id(rid) + plural
    return re.sub(r"\[([A-Za-z0-9_]+)\](s?)", one, text or "")


def _zone_label(z):
    return _fr_names("zone").get(z) or _pretty_id(z or "")


def _unit_label(u):
    names = _fr_names("unit")
    # a boss the translation skips, whose fight clones carry its name
    # (DemonSuperElite_Fairy: "Nocte-reine Shaarlize Te'reur")
    clone = next((names[u + s] for s in ("_TrueClone", "_FalseClone")
                  if u and names.get(u + s)), None)
    return (names.get(u) or clone or _unit_names().get(u)
            or _pretty_id(u or ""))



def _spark_units():
    return _table("unit_traits.json",
                  shape=lambda d: set(d["spark"]) if d else set())

def bestiary_catalogue():
    """{"placed": [{id, family, tier, zones, regions, lvl}] — every monster
    the levels place —, "units": {id: {family, tier, region}} — every monster
    of the game —, "families": [...]}, from analysis_out/bestiary.json
    (hltools/bestiary_data.py)."""
    # a list before the family view
    return _table("bestiary.json", shape=lambda d: {"placed": d}
                  if isinstance(d, list) else d)

def _codex_thresholds():
    """tier -> the kill counts of the codex's three ranks (the game's own
    numbers, analysis_out/codex_units.json)."""
    return _table("codex_units.json").get("thresholds") or {}
def _family_label(fam):
    if fam == "Demon_Rift":             # "Démons" too in the game's text
        return tr("Démons des failles")
    if fam == "Human":                  # the game has no name for it
        return tr("Humains")
    return (_fr_names("unitType").get(fam) or _pretty_id(fam)) if fam else ""



def item_type(kind):
    return _table("item_types.json").get(kind) or ""

def _augments_data():
    return _table("augments.json")
def _skill_label(sid):
    return _fr_names("skill").get(sid) or _pretty_id(sid)



def talent_data():
    """{"trees": {class: {root, talents: [{s, tier, branch, max}]}},
    "runes": {rune: skill}} from analysis_out/talents.json
    (hltools/skills_data.py)."""
    return _table("talents.json")

def luck_data():
    return _table("luck.json")

def achievements_catalogue():
    """{"categories": [{id, parent}], "achievements": [{id, cat, parent,
    points, obj, reward, copyDesc, consts}]} from analysis_out/
    achievements.json (hltools/achievements_data.py)."""
    return _table("achievements.json")

def rift_rewards_data():
    """analysis_out/rift_rewards.json (emit_offsets.extract_rift_rewards)."""
    return _table("rift_rewards.json")

def _localized(name, swap):
    """analysis_out/<name> in the interface's language: `swap(copy)` puts
    the language's texts in place of the French ones. Cached per language."""
    lang = i18n.lang()
    if lang == "fr":
        return _table(name)
    key = (name, lang)
    if key not in _LOCALIZED:
        data = json.loads(json.dumps(_table(name)))
        swap(data)
        _LOCALIZED[key] = data
    return _LOCALIZED[key]


_LOCALIZED = {}


def infusion_data():
    """{"infusions": {skill: {f, role, name, pattern, t2, t4, t6, en}},
    "item_faction": {item: faction}} from analysis_out/infusions.json
    (hltools/infusions_data.py), its texts in the interface's language."""
    def swap(d):
        for e in (d.get("infusions") or {}).values():
            e.update(e.get("en") or {})
    return _localized("infusions.json", swap)
def _infusion_id(raw):
    """The infusion skill a gear's `infusion` field names (the skill, or
    the pattern that teaches it)."""
    infs = infusion_data().get("infusions") or {}
    if not raw:
        return None
    if raw in infs:
        return raw
    for sid, e in infs.items():
        if e.get("pattern") == raw or sid == "Infusion_" + raw:
            return sid
    return raw



def _offsets():
    """analysis_out/meter_offsets.json, read once."""
    return _table("meter_offsets.json")
def faction_label(f):
    """A faction's French name (the faction sheet: Apix, Nepsides, Béliers
    écarlates...), else its monster family's."""
    return (_fr_names("faction").get(f) or _fr_names("unitType").get(f)
            or _pretty_id(f or ""))


def _item_flag(bits, name):
    """Whether an item copy carries one st.ItemFlag (bit index from
    meter_offsets.json's ItemFlag, read off the bytecode)."""
    idx = (_offsets().get("ItemFlag") or {}).get(name)
    return isinstance(bits, int) and idx is not None and bool(
        (bits >> idx) & 1)


# ---- gear stats: the game's own computation (hltools/gear_stats_data.py) --

def gear_stats_data():
    return _table("gear_stats.json")

def skill_tip(sid):
    """A skill's tooltip in the interface's language, or None: {name, cd,
    desc} for a skill without ranks, {name, cd, rows: [{r, when, t}]} for a
    weapon skill, whose ranks 2 and 3 open after so many kills with the
    weapon (main hand's count). From analysis_out/skill_tips.json
    (hltools/skilltext.py)."""
    from common import dec_sep
    e = (_table("skill_tips.json").get("skills") or {}).get(sid)
    if not e:
        return None
    t = e.get(i18n.lang()) or e.get("en") or {}
    out = {"name": _skill_label(sid), "desc": _fr_ref(t.get("desc"))}
    cd = e.get("cd")
    if isinstance(cd, (int, float)) and cd > 0:
        out["cd"] = tr("{n} s de recharge", n=f"{cd:g}".replace(".", dec_sep()))
    ranks = [_fr_ref(r) for r in t.get("ranks") or ()]
    if any(ranks):
        per = skill_rank_kills()[0]
        out["rows"] = [{"r": "R1", "when": tr("Départ"), "t": out.pop("desc")}]
        out["rows"] += [{"r": f"R{i + 2}", "when": tr("{n} kills", n=per * (i + 1)),
                         "t": r} for i, r in enumerate(ranks) if r]
    return out


def talent_tip(sid, max_rank, rank=0):
    """A talent's tooltip: {name, desc} or, when its numbers change with its
    points, {name, rows: [{r, t, on}]} for each of its ranks, the one it is
    at marked."""
    tip = skill_tip(sid)
    if not tip:
        return None
    e = (_table("skill_tips.json").get("skills") or {}).get(sid) or {}
    t = e.get(i18n.lang()) or e.get("en") or {}
    by = t.get("byRank") or []
    if t.get("ranks") and any(t["ranks"]):
        # the game's own text per rank: the description, then ranks 2, 3...
        by = [t.get("desc") or ""] + list(t["ranks"])
    if max_rank and max_rank > 1 and len(by) >= max_rank:
        tip.pop("rows", None)
        tip.pop("desc", None)
        tip["rows"] = [{"r": tr("Rang {n}", n=i + 1), "t": _fr_ref(by[i]),
                        "on": i + 1 == rank} for i in range(max_rank)]
    else:
        tip.pop("rows", None)       # a talent's ranks come with points, not kills
    return tip


def weapon_upgrade_skill(item_type, rarity):
    """A weapon's upgrade skill and its rank (the rarity's row, from 0:
    st.item.Weapon.getWeaponUpgradeSkill), or None."""
    u = _table("skill_tips.json").get("upgrades") or {}
    rars = u.get("rarities") or []
    if item_type not in (u.get("types") or {}) or rarity not in rars:
        return None
    return item_type + "_Upgrade", rars.index(rarity)


def weapon_upgrade(item_type, rarity):
    """A weapon's upgrade effect (its type's), at its rarity: (text, upgrade
    level it opens at), or None (skill_tips.json "upgrades")."""
    u = _table("skill_tips.json").get("upgrades") or {}
    t = (u.get("types") or {}).get(item_type)
    rars = u.get("rarities") or []
    if not t or rarity not in rars:
        return None
    texts = t.get(i18n.lang()) or t.get("en") or []
    i = rars.index(rarity)
    if i >= len(texts) or not texts[i]:
        return None
    return _fr_ref(texts[i]), int(u.get("at") or 3)


def skill_rank_kills():
    """Kills per weapon skill rank: (main hand, off hand)."""
    k = _table("skill_tips.json").get("kills") or [20, 26]
    return k[0], k[1]


def build_data():
    """The Build tab's catalogue and rules: analysis_out/build_data.json
    (hltools/build_data.py), the runes' descriptions in the interface's
    language."""
    def swap(d):
        for info in (d.get("skillInfo") or {}).values():
            for r in (info.get("runes") or ()) if isinstance(info, dict) else ():
                if r.get("desc_en"):
                    r["desc"] = r["desc_en"]
    return _localized("build_data.json", swap)

def world_map():
    """{"meta": tiles and transform, "points": [{c, id, x, y, zone,
    region}]} from analysis_out/map.json (hltools/map_data.py)."""
    return _table("map.json")
def _element_done(states, eid):
    """Whether a world element has been completed (chest opened, orb picked
    up, obelisk discovered): Progress.elements has it, with a time (or a
    [byte, time] pair)."""
    v = (states or {}).get(eid)
    if isinstance(v, list):
        v = v[-1] if v else None
    return isinstance(v, (int, float)) and v > 0



def _unit_names():
    """kind -> display name, from analysis_out/unit_names.json (data.cdb,
    via emit_offsets.py). {} when absent. A kind is often NOT the shown name
    ('Cleodora' displays as 'Queen Honeyzabeth')."""
    return _table("unit_names.json")

def _names_table():
    """The game's names in the interface's language: names_<lang>.json
    (names_en.json: data.cdb's own English), names_fr.json without it."""
    lang = i18n.lang()
    got = _table(f"names_{lang}.json") if lang != "fr" else None
    return got or _table("names_fr.json")


def _fr_names(sheet):
    """id -> display name for one of the game's sheets (activity, item,
    rarity, unit...), in the interface's language. {} when absent."""
    return _names_table().get(sheet) or {}

def item_rarity(kind):
    """An item's base rarity from its sheet row (analysis_out/
    item_rarity.json); a weapon's own copy rarity overrides it."""
    return _table("item_rarity.json").get(kind)
_ITEM_ICONS = {}


def item_icon(kind):
    """An item's icon as a data URI (analysis_out/item_icons/<id>.png), or
    "" when there is none. Inlined: the window loads no external files."""
    kind = str(kind or "")
    if kind not in _ITEM_ICONS:
        uri = ""
        if re.fullmatch(r"[A-Za-z0-9_]+", kind):
            try:
                import base64
                data = (ANALYSIS / "item_icons" / f"{kind}.png").read_bytes()
                uri = "data:image/png;base64," + base64.b64encode(data).decode()
            except OSError:
                pass
        _ITEM_ICONS[kind] = uri
    return _ITEM_ICONS[kind]



def dungeon_catalogue():
    """Every dungeon in the game, [{kind, boss, region}], from
    analysis_out/dungeons.json (the game's achievements). [] when absent."""
    return _table("dungeons.json", list)
# the raw materials' types, which the game's translation leaves unnamed
ITEM_TYPE_FR = {"Ore": "Minerai", "Cloth": "Tissu", "Leather": "Cuir",
                "UpgradeComponent": "Composant d'amélioration"}


def item_type_label(t):
    return (_fr_names("itemType").get(t)
            or (ITEM_TYPE_FR.get(t) if i18n.lang() == "fr" else None)
            or _pretty_id(t))


def _fr_desc(sheet):
    """id -> description for a sheet (ach, item, unit, skill), in the
    interface's language (names_<lang>.json's "_desc")."""
    return _names_table().get("_desc", {}).get(sheet) or {}


def item_label(kind):
    """An item's French name, else its prettified id. An infusion pattern's
    name is a template ("Infusion: ::ref_skill::"): it is named after its
    infusion."""
    name = _fr_names("item").get(kind)
    if (not name or "::" in name) and str(kind).startswith(
            "InfusionPattern_"):
        inf = next((e for e in (infusion_data().get("infusions") or {})
                    .values() if e.get("pattern") == kind), None)
        if inf:
            return tr("Patron d'imprégnation : {name}", name=inf.get("name"))
    return name or _pretty_id(kind)


RARITY_FR = {"Common": "Ordinaire", "Uncommon": "Peu ordinaire",
             "Rare": "Rare", "Epic": "Épique", "Legendary": "Légendaire"}


def rarity_label(r):
    return (_fr_names("rarity").get(r)
            or (RARITY_FR.get(r) if i18n.lang() == "fr" else None) or (r or ""))



def _heal_specs():
    """skill id -> {step: [heal effect spec]}, from analysis_out/heal_specs.json.
    Its absence is logged: heals on full-health targets become unsizeable."""
    return _table("heal_specs.json", warn="healing falls back to what "
                  "each skill has been seen to restore")
def _boss_label(kind):
    """The boss's real display name, falling back to the prettified kind for
    anything the unit sheet doesn't carry."""
    return (_fr_names("unit").get(kind) or _unit_names().get(kind)
            or _pretty_id(kind))


def _summon_label(kind):
    """A summon's real display name ('Summon_Imp' -> 'Nightling Terror',
    'Rabbit_EarlyAccess_Spark' -> 'Sparktail'), falling back to the prettified
    kind for anything the unit sheet doesn't carry. (Stripping `Summon_` off
    the kind is not the nameplate name.)"""
    return (_fr_names("unit").get(kind) or _unit_names().get(kind)
            or _pretty_id(kind))


# ---------------------------------------------------------------------------
# Frida host
# ---------------------------------------------------------------------------
def build_script_source():
    data = json.loads((ANALYSIS / "resolver_data.json").read_text(encoding="utf-8"))
    off = (ANALYSIS / "meter_offsets.json").read_text(encoding="utf-8")
    js = (FRIDA_DIR / "meter_hook.js").read_text(encoding="utf-8")
    return (f"const DATA = {json.dumps(data)};\nconst OFF = {off};\n" + js)


DATA_STAMP = ANALYSIS / ".data_stamp.json"


# Set while regenerate_data runs (shown in the title band).
REGENERATING = threading.Event()
# One regenerate at a time (first launch and game link); the second then
# finds the stamp current and returns at once.
_REGEN_LOCK = threading.Lock()
# Bumped by each regenerate that wrote new files: the window resends the
# pictures (skills, collection, dungeons...) when it changes.
DATA_GENERATION = [0]


# Bumped when the generators' output changes shape: data written by older
# tools is regenerated once, though the game itself has not changed.
DATA_FORMAT = 18


def _hook_needs():
    """What the hook reads out of the generated files, parsed from its own
    source: OFF.<group>[.<field>] in meter_offsets.json, DATA.<key> in
    resolver_data.json."""
    try:
        src = (FRIDA_DIR / "meter_hook.js").read_text(encoding="utf-8")
    except OSError:
        return set(), set()
    offsets = {f"{g}.{f}" if f else g
               for g, f in re.findall(r"\bOFF\.(\w+)(?:\.(\w+))?", src)}
    return offsets, set(re.findall(r"\bDATA\.(\w+)", src))


def _data_is_current():
    """True when the generated files carry everything the hook reads and
    every table is there; what is missing is logged."""
    def present(d, key):
        group, _, field = key.partition(".")
        got = d.get(group)
        if got is None:
            return False
        # a field is only checked inside a group of fields
        return not field or not isinstance(got, dict) \
            or got.get(field) is not None

    offsets, resolver = _hook_needs()
    for name, required in (("resolver_data.json", resolver),
                           ("meter_offsets.json", offsets)):
        try:
            d = json.loads((ANALYSIS / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        missing = sorted(k for k in required if not present(d, k))
        if missing:
            print(f"[meter] {name} lacks what the hook reads "
                  f"({', '.join(missing)}); regenerating.", file=sys.stderr)
            return False
    absent = [out for _label, outs, _f, _w in GENERATED_GROUPS
              for out in outs if not (ANALYSIS / out).exists()]
    if absent:
        print(f"[meter] {', '.join(absent)} absent; regenerating.",
              file=sys.stderr)
        return False
    return True

def forget_loaded_data():
    """Drop every table loaded from analysis_out/, so the next use reads the
    files a regenerate just wrote (Réparer)."""
    _TABLES.clear()
    _ITEM_ICONS.clear()                 # a missing icon was cached as ""
    for mod, name in (("bosssheet", "_DATA"), ("bosssheet", "_PLACEHOLDER"),
                      ("goals", "_TYPES")):
        m = sys.modules.get(mod)
        if m is not None:
            setattr(m, name, None)


def regenerate_data(hlboot=None, force=False, on_step=None,
                    on_progress=None):
    with _REGEN_LOCK:
        return _regenerate_data(hlboot, force, on_step, on_progress)


def needs_first_data():
    """The installed app's first launch: only the shipped tables are there;
    the welcome screen asks before reading the rest off the game."""
    return not (ANALYSIS / "build_data.json").is_file()


# Set once the player consents on the welcome screen (or at once when the
# data is already there); the game link waits for it before regenerating.
DATA_CONSENT = threading.Event()


def game_folder_hlboot(folder):
    """The hlboot.dat of a folder the player picked (the game's own, or
    Farever.exe or hlboot.dat itself), remembered for the next launches.
    None when there is no Farever there."""
    p = Path(str(folder or "").strip().strip('"'))
    if p.is_file():
        p = p.parent
    hb = p / "hlboot.dat"
    if not (hb.is_file() and (p / "res.pak").is_file()):
        return None
    try:
        GAME_PATH_FILE.write_text(json.dumps({"hlboot": str(hb)}),
                                  encoding="utf-8")
    except OSError as e:
        print(f"[meter] couldn't remember the game's folder: {e}",
              file=sys.stderr)
    return hb


def _regenerate_data(hlboot=None, force=False, on_step=None,
                     on_progress=None):
    """Re-run the generators against the given hlboot.dat (or their own
    auto-detect when None), e.g. after a Farever patch. Skipped when the
    hlboot.dat and res.light.pak are unchanged since the last success. True
    on success."""
    tools = [ROOT / "hltools" / "build_targets.py",
             ROOT / "hltools" / "emit_offsets.py"]
    missing = [t.name for t in tools if not t.exists()]
    if missing:
        print(f"[meter] can't self-heal — missing {', '.join(missing)} "
              "(reinstall the app).", file=sys.stderr)
        return False
    stamp = None
    if hlboot is not None:
        st = Path(hlboot).stat()
        stamp = {"src": str(hlboot), "mtime": st.st_mtime, "size": st.st_size,
                 "format": DATA_FORMAT}
        # the game's data (data.cdb, in res.light.pak) too: a patch can fix a
        # table (a loot link) without touching the code
        light = Path(hlboot).with_name("res.light.pak")
        if light.is_file():
            lt = light.stat()
            stamp["data"] = {"mtime": lt.st_mtime, "size": lt.st_size}
        if not force:
            try:
                # Log WHY a regenerate (two parses of a 14 MB file) happens.
                on_disk = json.loads(DATA_STAMP.read_text())
                if on_disk != stamp:
                    data_only = ({k: v for k, v in on_disk.items() if k != "data"}
                                 == {k: v for k, v in stamp.items() if k != "data"})
                    if data_only:
                        was = (on_disk.get("data") or {}).get("size")
                        now = (stamp.get("data") or {}).get("size")
                    else:
                        was, now = on_disk.get("size"), stamp["size"]
                    print(f"[meter] {'res.light.pak' if data_only else 'hlboot.dat'} "
                          f"has changed since the last regenerate (stamp {was} "
                          f"bytes, now {now}); regenerating.", file=sys.stderr)
                elif (not (ANALYSIS / "resolver_data.json").is_file()
                        or not (ANALYSIS / "meter_offsets.json").is_file()):
                    print("[meter] a generated file is missing; regenerating.",
                          file=sys.stderr)
                elif _data_is_current():
                    print("[meter] data already matches this build "
                          "(hlboot.dat and res.light.pak unchanged).", file=sys.stderr)
                    return True
            except FileNotFoundError:
                print("[meter] no data stamp yet; regenerating.",
                      file=sys.stderr)
            except Exception as e:
                print(f"[meter] couldn't read the data stamp ({e}); "
                      "regenerating.", file=sys.stderr)
    # Frozen, the tools would write into the bundle's temp directory: point
    # them at the writable copy.
    env = dict(os.environ, FAREVER_ANALYSIS_OUT=str(ANALYSIS))
    REGENERATING.set()
    try:
        ok = _run_generators(tools, hlboot, env, stamp, on_step,
                             on_progress)
    finally:
        REGENERATING.clear()
    if ok:
        forget_loaded_data()
        DATA_GENERATION[0] += 1
    return ok


# What the generators write, grouped as the welcome screen lists them:
# (label, outputs in "[written]" order, picture folders counted for progress,
# share of the time — measured 2026-10-04, 21 s in all).
GENERATED_GROUPS = (
    ("Code et structures du jeu",
     ("resolver_data.json", "meter_offsets.json"), (), 3),
    ("Créatures et textes du jeu",
     ("unit_names.json", "heal_specs.json", "codex_units.json",
      "unit_traits.json", "names_fr.json", "names_en.json"), (), 2),
    ("Images de la collection", ("collection.json",), ("collection_img",), 19),
    ("Sorts, talents et leurs icônes", ("talents.json",), ("skill_img",), 9),
    ("Builds, équipement et succès",
     ("achievements.json", "infusions.json", "build_data.json", "skill_tips.json",
      "gear_stats.json", "codex_items.json"), (), 3),
    ("Bestiaire et décors des donjons", ("bestiary.json",),
     ("bestiary_img", "dungeon_bg"), 34),
    ("Carte du monde", ("map.json",), ("map_tiles",), 18),
    ("Donjons et fiches des boss",
     ("dungeons.json", "boss_sheets.json", "boss_portraits"),
     ("boss_portraits",), 2),
    ("Icônes des objets",
     ("augments.json", "rift_rewards.json", "luck.json",
      "item_types.json", "item_rarity.json", "item_icons"),
     ("item_icons",), 10),
)
# Roughly how many pictures each folder ends with (progress bar estimate only).
GENERATED_PICTURES = {"collection_img": 855, "skill_img": 806,
                      "bestiary_img": 408, "dungeon_bg": 36, "map_tiles": 115,
                      "boss_portraits": 13, "item_icons": 1200}
# ...and relative cost per picture (a full-screen backdrop ~ a dozen icons)
GENERATED_PICTURE_COST = {"dungeon_bg": 12}


def generated_pictures(folder):
    """How many pictures a generator has written so far into a folder."""
    try:
        return sum(1 for p in (ANALYSIS / folder).iterdir()
                   if p.suffix in (".webp", ".png"))
    except OSError:
        return 0


def _run_generators(tools, hlboot, env, stamp, on_step=None,
                    on_progress=None):
    labels = {"build_targets.py": "cibles du code",
              "emit_offsets.py": "structures, images et tables"}
    for t in tools:
        print(f"[meter] regenerating {t.name} for this build ...", file=sys.stderr)
        if on_step:
            on_step(tr(labels.get(t.name, t.name)))
        # Frozen, sys.executable is this program: re-invoke it in tool mode.
        cmd = ([sys.executable, TOOL_FLAG, t.name] if FROZEN
               else [sys.executable, str(t)])
        if hlboot is not None:
            cmd.append(str(hlboot))
        # CREATE_NO_WINDOW: no console flash. Unbuffered and read line by line:
        # each "[written]" line is a progress step.
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace",
                             env=dict(env, PYTHONUNBUFFERED="1"),
                             creationflags=CREATE_NO_WINDOW)
        out = []
        for line in p.stdout:
            out.append(line)
            if on_progress and line.startswith("[written]"):
                on_progress(t.name, line)
        p.wait()
        if p.returncode != 0:
            print(f"[meter] {t.name} failed:\n{''.join(out)}",
                  file=sys.stderr)
            return False
    if stamp is not None:
        # Written and read back: a stamp that fails to land silently costs a
        # regenerate on every launch.
        try:
            DATA_STAMP.write_text(json.dumps(stamp), encoding="utf-8")
            back = json.loads(DATA_STAMP.read_text())
            if back != stamp:
                print("[meter] the data stamp did not take — every launch will "
                      f"regenerate. Wrote {stamp['size']} bytes, read back "
                      f"{back.get('size')}. Check {DATA_STAMP}.",
                      file=sys.stderr)
        except Exception as e:
            print(f"[meter] couldn't write the data stamp ({e}) — data is "
                  "correct, but every launch will regenerate it. "
                  f"Check {DATA_STAMP}.", file=sys.stderr)
    print("[meter] data regenerated for current build.", file=sys.stderr)
    return True


def _exe_path_of_pid(pid):
    """Full image path of a running process (None if unavailable)."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(4096)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        k32.CloseHandle(h)
    return None


RARITY_ORDER = {"Common": 0, "Uncommon": 1, "Rare": 2, "Epic": 3,
                "Legendary": 4}


def locate_hlboot(pid):
    """Find the hlboot.dat matching the *running* game. Priority: the
    FAREVER_HLBOOT override, the remembered game folder, the file next to the
    process's exe, then the drive auto-detect. Returns a Path or None (= use
    shipped data as-is)."""
    env = os.environ.get("FAREVER_HLBOOT")
    if env:
        if Path(env).is_file():
            return Path(env)
        print(f"[meter] FAREVER_HLBOOT points to a missing file: {env}",
              file=sys.stderr)
    try:
        saved = Path(json.loads(GAME_PATH_FILE.read_text(encoding="utf-8"))
                     ["hlboot"])
        if saved.is_file():
            return saved
    except (OSError, ValueError, KeyError, TypeError):
        pass
    exe = _exe_path_of_pid(pid) if pid else None
    if exe:
        cand = Path(exe).parent / "hlboot.dat"
        if cand.is_file():
            return cand
        print(f"[meter] no hlboot.dat next to {exe} — searching drives.",
              file=sys.stderr)
    sys.path.insert(0, str(ROOT / "hltools"))
    try:
        from gamepath import find_hlboot
        return Path(find_hlboot(argv_index=99))
    except (SystemExit, Exception):
        pass
    print("[meter] hlboot.dat not found — using the shipped data files. Set "
          "FAREVER_HLBOOT to its full path if Farever is installed somewhere "
          "unusual.", file=sys.stderr)
    return None



# ---------------------------------------------------------------------------
# 3D models, for the Collection's viewer
# ---------------------------------------------------------------------------
MODELS_DIR = ANALYSIS / "models"
MODEL_FORMAT = 18
_model_lock = threading.Lock()


def _game_dir():
    """The game's folder: the hlboot.dat the data was last built from, else
    the usual search."""
    try:
        src = json.loads(DATA_STAMP.read_text(encoding="utf-8")).get("src")
        if src and Path(src).is_file():
            return Path(src).parent
    except (OSError, ValueError):
        pass
    hb = locate_hlboot(None)
    return hb.parent if hb else None


# The game's window frame: its corners and rivets (a 9-slice texture), the
# top-right one plain, as the game's windows have it (its close button's
# corner).
UI_FRAME = "UI/Elements/background_close.png"
# The game's window titles' font, a bitmap one (Heaps' BFNT: its glyphs in
# an atlas).
UI_TITLE_FONT = "Font/platypi-bold-20.fnt"
# Bumped when what ensure_ui_frame copies changes: copied again.
UI_ASSETS = 7
# the game's plus (white, tinted where it is shown): the bonus dungeon's mark
UI_PLUS = "UI/Elements/plus.png"
# its windows' close button: its cross, cut out of it (the button's ground
# is its hovered look)
UI_CLOSE = "UI/Elements/closeButton.png"


def ensure_ui_frame():
    """The game's interface pieces for the overlays (menu_host), copied from
    its files into analysis_out/ once: its window frame (ui_frame.png) and
    its opaque pixels (ui_frame_mask.json, one "0"/"1" string a row) that
    cut the window to its outline, its two mouse cursors, its title font
    (ui_font_title.png and .json). False when the game or a piece is
    missing."""
    stamp = ANALYSIS / "ui_assets.json"
    try:
        if json.loads(stamp.read_text(encoding="utf-8")).get("v") == UI_ASSETS:
            return True
    except (OSError, ValueError):
        pass
    game = _game_dir()
    if game is None or not (game / "res.pak").is_file():
        return False
    if str(ROOT / "hltools") not in sys.path:
        sys.path.insert(0, str(ROOT / "hltools"))
    try:
        import io
        import pak_extract
        from PIL import Image
        raw = pak_extract.read_entry(game / "res.pak", UI_FRAME)
        if not raw:
            return False
        alpha = Image.open(io.BytesIO(raw)).convert("RGBA").getchannel("A")
        rows = ["".join("1" if alpha.getpixel((x, y)) >= 128 else "0"
                        for x in range(alpha.width))
                for y in range(alpha.height)]
        ANALYSIS.mkdir(parents=True, exist_ok=True)
        (ANALYSIS / "ui_frame.png").write_bytes(raw)
        (ANALYSIS / "ui_frame_mask.json").write_text(json.dumps(rows),
                                                     encoding="utf-8")
        _ui_cursors(game)
        _ui_title_font(game)
        close = Image.open(io.BytesIO(pak_extract.read_entry(
            game / "res.pak", UI_CLOSE))).convert("RGBA")
        close.save(ANALYSIS / "ui_close.png")
        (ANALYSIS / "ui_plus.png").write_bytes(
            pak_extract.read_entry(game / "res.pak", UI_PLUS))
        # the cross alone: its white over the button's orange (blue apart)
        cross = Image.new("L", close.size, 0)
        for y in range(close.height):
            for x in range(close.width):
                r, g, b, a = close.getpixel((x, y))
                cross.putpixel((x, y), max(0, min(255, (b - 70) * 255 // 185)) * a // 255)
        # white, its shape in the alpha (a CSS mask reads the alpha)
        white = Image.new("RGBA", close.size, (255, 255, 255, 0))
        white.putalpha(cross)
        white.save(ANALYSIS / "ui_close_x.png")
        stamp.write_text(json.dumps({"v": UI_ASSETS}), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[meter] the game's interface pieces couldn't be read: {e!r}",
              file=sys.stderr)
        return False


def _ui_title_font(game):
    """The title font's atlas and glyphs (analysis_out/ui_font_title.png,
    .json: {size, lineHeight, base, glyphs: {code: [x, y, w, h, dx, dy,
    advance]}}), read from its BFNT file (hxd.fmt.bfnt: a header, then each
    glyph's code, rectangle, offsets, advance and kerning pairs)."""
    import struct
    import pak_extract
    b = pak_extract.read_entry(game / "res.pak", UI_TITLE_FONT)
    if b[:4] != b"BFNT":
        raise ValueError(f"{UI_TITLE_FONT}: not a BFNT font")
    p = 6

    def r(fmt):
        nonlocal p
        v = struct.unpack_from(fmt, b, p)[0]
        p += struct.calcsize(fmt)
        return v

    def text():
        nonlocal p
        n = r("<H")
        v = b[p:p + n].decode("utf-8")
        p += n
        return v
    text()                                  # its name
    size, atlas = r("<h"), text()
    line, base = r("<h"), r("<h")
    r("<i")                                 # the missing glyph's code
    glyphs = {}
    while p < len(b):
        code = r("<i")
        if code == 0:
            break
        g = [r("<H"), r("<H"), r("<H"), r("<H"), r("<h"), r("<h"), r("<h")]
        for _ in range(r("<i")):            # kerning pairs: unused
            r("<i")
            r("<h")
        glyphs[code] = g
    folder = UI_TITLE_FONT.rsplit("/", 1)[0]
    (ANALYSIS / "ui_font_title.png").write_bytes(
        pak_extract.read_entry(game / "res.pak", f"{folder}/{atlas}"))
    (ANALYSIS / "ui_font_title.json").write_text(json.dumps(
        {"size": size, "lineHeight": line, "base": base, "glyphs": glyphs}),
        encoding="utf-8")


# The game's two mouse cursors (ui.BaseUI.setSystemCursor): data.cdb's
# icons, each a cell of UI/Inputs/cursor.png, its hotspot props.cursor's
# offset (none: the top-left corner, ui.BaseUI.getIconCursor).
UI_CURSORS = {"CursorDefault": "default", "CursorButton": "button"}


def _ui_cursors(game):
    """The game's cursors copied to analysis_out/ui_cursor_<name>.png."""
    import io
    import pak_extract
    from PIL import Image
    cdb = json.loads(pak_extract.read_entry(game / "res.light.pak", "data.cdb"))
    icons = {ln.get("id"): ln for s in cdb.get("sheets") or ()
             if s.get("name") == "icon" for ln in s.get("lines") or ()}
    sheets = {}
    for cid, name in UI_CURSORS.items():
        g = (icons.get(cid) or {}).get("gfx") or {}
        if not g.get("file"):
            continue
        if g["file"] not in sheets:
            sheets[g["file"]] = Image.open(io.BytesIO(pak_extract.read_entry(
                game / "res.pak", g["file"]))).convert("RGBA")
        n = int(g.get("size") or 32)
        x, y = int(g.get("x") or 0) * n, int(g.get("y") or 0) * n
        sheets[g["file"]].crop((x, y, x + n, y + n)).save(
            ANALYSIS / f"ui_cursor_{name}.png")


ART_DIR = ANALYSIS / "item_art"
_ART = {}


def item_art(kind, unit=False):
    """An item's (a companion's: `unit`) picture at the game's own size (its
    gfx tile in res.pak, 256 px or so: the icons are 48, the Collection's
    128), as a data URI; "" without it. Made once, kept in item_art/."""
    kind = str(kind or "")
    if not re.fullmatch(r"[A-Za-z0-9_]+", kind):
        return ""
    key = ("u:" if unit else "i:") + kind
    if key in _ART:
        return _ART[key]
    path = ART_DIR / f"{'u_' if unit else ''}{kind}.webp"
    uri = ""
    try:
        uri = "data:image/webp;base64," + base64.b64encode(
            path.read_bytes()).decode()
    except OSError:
        game = _game_dir()
        if game is not None and (game / "res.pak").is_file():
            try:
                if str(ROOT / "hltools") not in sys.path:
                    sys.path.insert(0, str(ROOT / "hltools"))
                import io
                import hmd_model
                from PIL import Image
                g = (hmd_model._sheets(game)["unit" if unit else "item"]
                     .get(kind) or {}).get("gfx") or {}
                raw = hmd_model._read(game / "res.pak", g.get("file") or "")
                if raw:
                    img = Image.open(io.BytesIO(raw)).convert("RGBA")
                    n = int(g.get("size") or img.width)
                    x, y = int(g.get("x") or 0) * n, int(g.get("y") or 0) * n
                    w = int(g.get("width") or 1) * n
                    h = int(g.get("height") or 1) * n
                    if x + w <= img.width and y + h <= img.height:
                        tile = img.crop((x, y, x + w, y + h))
                        ART_DIR.mkdir(parents=True, exist_ok=True)
                        tile.save(path, "WEBP", quality=88, method=4)
                        uri = "data:image/webp;base64," + base64.b64encode(
                            path.read_bytes()).decode()
            except Exception as e:
                print(f"[meter] picture of {kind}: {e!r}", file=sys.stderr)
    _ART[key] = uri
    return uri


def item_model_json(item_id):
    """One collectible's model for the viewer, as JSON text, cached and keyed
    to res.pak (a patch rebuilds it). None when there is no readable model or
    no game.
    "<id>@anim" asks for a monster's idle animation with it;
    "hero:<slot>=<id>.<slot>=<id>…" the hero wearing those pieces (a
    build's), in its idle; "npc:<element>" a character of the open world in
    the hero's body, its looks and clothes, in its idle."""
    hero = npc = None
    m_ = re.fullmatch(r"npc:([A-Za-z0-9_]+)(@anim)?", str(item_id or ""))
    if m_:
        npc = next((n for n in collection_catalogue().get("npcs") or ()
                    if n.get("el") == m_.group(1)), None)
        if not npc or not npc.get("skin"):
            return None
        anim = False
        item_id = "npc_" + m_.group(1)
    elif str(item_id or "").startswith("hero:"):
        hero = dict(p.split("=", 1) for p in str(item_id)[5:].split(".")
                    if re.fullmatch(r"[A-Za-z0-9_]+=[A-Za-z0-9_]+", p))
        anim = False
        item_id = "hero_" + hashlib.sha1(
            ".".join(f"{k}={v}" for k, v in sorted(hero.items())).encode()).hexdigest()[:12]
    else:
        m_ = re.fullmatch(r"([A-Za-z0-9_]+)(@anim)?", str(item_id or ""))
        if not m_:
            return None
        anim = bool(m_.group(2))
        item_id = m_.group(1)
    game = _game_dir()
    if game is None or not (game / "res.pak").is_file():
        return None
    st = (game / "res.pak").stat()
    # the converter's version too: a better one rebuilds what the last made
    key = f"{MODEL_FORMAT}:{st.st_size}:{int(st.st_mtime)}"
    cache = MODELS_DIR / f"{item_id}{'_anim' if anim else ''}.json"
    with _model_lock:
        try:
            got = json.loads(cache.read_text(encoding="utf-8"))
            if got.get("key") == key:
                return json.dumps(got.get("m")) if got.get("m") else None
        except (OSError, ValueError):
            pass
        if str(ROOT / "hltools") not in sys.path:
            sys.path.insert(0, str(ROOT / "hltools"))
        try:
            import hmd_model
            m = (hmd_model.hero_model(game, hero) if hero is not None
                 else hmd_model.npc_model(game, npc.get("skin"),
                                          npc.get("gear") or {})
                 if npc is not None
                 else hmd_model.item_model(game, item_id, anim=anim))
        except Exception as e:
            # not cached: a fix to the reader should get its chance
            print(f"[meter] model of {item_id} failed: {e!r}", file=sys.stderr)
            return None
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"key": key, "m": m}), encoding="utf-8")
        except OSError:
            pass
        return json.dumps(m) if m else None
