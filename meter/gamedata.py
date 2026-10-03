"""The game's data as the app reads it: the tables generated into analysis_out/
(names, items, catalogues), their regeneration after a game patch, and the
hook's source."""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from common import (
    ANALYSIS, CREATE_NO_WINDOW, FRIDA_DIR, FROZEN, ROOT, TOOL_FLAG, _pretty_id)


def dungeon_name(kind):
    """The dungeon's French name, as the game shows it
    ("R1_POI_CleodorasNest" -> "Tronc-ruche d'Élizabeille"); the prettified
    id when the game's translation doesn't have it."""
    fr = _fr_names("activity").get(str(kind or ""))
    if fr:
        return fr
    s = re.sub(r"^R\d+_POI_(Dungeon_)?", "", str(kind or ""))
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s.replace("_", " "))
    return " ".join(s.split()) or "Donjon"


_COLLECTION = None


def collection_catalogue():
    """{"mounts": [...], "gliders": [...], "pets": [...]}: every collectible
    with how it is obtained, from analysis_out/collection.json (built from the
    game's data and levels by hltools/collection_data.py). {} when absent."""
    global _COLLECTION
    if _COLLECTION is None:
        try:
            _COLLECTION = json.loads(
                (ANALYSIS / "collection.json").read_text(encoding="utf-8"))
        except Exception:
            _COLLECTION = {}
    return _COLLECTION


_CODEX_ITEMS = None


def codex_items_catalogue():
    """[{id, rarity, type, src, uses}]: the items the game's item codex
    counts (crafting components, ores, cloth, leather), from
    analysis_out/codex_items.json (hltools/codex_items.py)."""
    global _CODEX_ITEMS
    if _CODEX_ITEMS is None:
        try:
            _CODEX_ITEMS = json.loads(
                (ANALYSIS / "codex_items.json").read_text(encoding="utf-8"))
        except Exception:
            _CODEX_ITEMS = []
    return _CODEX_ITEMS


# The game's generic [terms] (skill kinds), named in no sheet.
# (singular, plural): the texts write "[WeaponSkill]s".
FR_TERMS = {"Skill": ("compétence", "compétences"),
            "WeaponSkill": ("compétence d'arme", "compétences d'arme"),
            "ClassSkill": ("compétence de classe", "compétences de classe"),
            "ComboAttack": ("attaque combo", "attaques combo")}


def _fr_ref(text):
    """The game's [Id] references in a French text, replaced by names."""
    def one(m):
        rid, plural = m.group(1), m.group(2)
        if rid in FR_TERMS:
            return FR_TERMS[rid][1 if plural else 0]
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
    return (_fr_names("unit").get(u) or _unit_names().get(u)
            or _pretty_id(u or ""))


_SPARK = None


def _spark_units():
    global _SPARK
    if _SPARK is None:
        try:
            _SPARK = set(json.loads((ANALYSIS / "unit_traits.json")
                                    .read_text(encoding="utf-8"))["spark"])
        except Exception:
            _SPARK = set()
    return _SPARK


_BESTIARY = None


def bestiary_catalogue():
    """{"placed": [{id, family, tier, zones, regions, lvl}] — every monster
    the levels place —, "units": {id: {family, tier, region}} — every monster
    of the game —, "families": [...]}, from analysis_out/bestiary.json
    (hltools/bestiary_data.py)."""
    global _BESTIARY
    if _BESTIARY is None:
        try:
            _BESTIARY = json.loads(
                (ANALYSIS / "bestiary.json").read_text(encoding="utf-8"))
        except Exception:
            _BESTIARY = {}
        if isinstance(_BESTIARY, list):         # before the family view
            _BESTIARY = {"placed": _BESTIARY}
    return _BESTIARY


_CODEX_SETS = None


def _codex_thresholds():
    """tier -> the kill counts of the codex's three ranks (the game's own
    numbers, analysis_out/codex_units.json)."""
    global _CODEX_SETS
    if _CODEX_SETS is None:
        try:
            _CODEX_SETS = json.loads((ANALYSIS / "codex_units.json")
                                     .read_text(encoding="utf-8"))
        except Exception:
            _CODEX_SETS = {}
    return _CODEX_SETS.get("thresholds") or {}


def _family_label(fam):
    if fam == "Demon_Rift":             # "Démons" too in the game's text
        return "Démons des failles"
    if fam == "Human":                  # the game has no French name for it
        return "Humains"
    return (_fr_names("unitType").get(fam) or _pretty_id(fam)) if fam else ""


_ITEM_TYPES = None


def item_type(kind):
    global _ITEM_TYPES
    if _ITEM_TYPES is None:
        try:
            _ITEM_TYPES = json.loads(
                (ANALYSIS / "item_types.json").read_text(encoding="utf-8"))
        except Exception:
            _ITEM_TYPES = {}
    return _ITEM_TYPES.get(kind) or ""


_AUGMENTS = None


def _augments_data():
    global _AUGMENTS
    if _AUGMENTS is None:
        try:
            _AUGMENTS = json.loads(
                (ANALYSIS / "augments.json").read_text(encoding="utf-8"))
        except Exception:
            _AUGMENTS = {}
    return _AUGMENTS


def _skill_label(sid):
    return _fr_names("skill").get(sid) or _pretty_id(sid)


_TALENTS = None


def talent_data():
    """{"trees": {class: {root, talents: [{s, tier, branch, max}]}},
    "runes": {rune: skill}} from analysis_out/talents.json
    (hltools/skills_data.py)."""
    global _TALENTS
    if _TALENTS is None:
        try:
            _TALENTS = json.loads(
                (ANALYSIS / "talents.json").read_text(encoding="utf-8"))
        except Exception:
            _TALENTS = {}
    return _TALENTS


_LUCK = None


def luck_data():
    global _LUCK
    if _LUCK is None:
        try:
            _LUCK = json.loads((ANALYSIS / "luck.json").read_text(
                encoding="utf-8"))
        except Exception:
            _LUCK = {}
    return _LUCK


_ACHIEVEMENTS = None


def achievements_catalogue():
    """{"categories": [{id, parent}], "achievements": [{id, cat, parent,
    points, obj, reward, copyDesc, consts}]} from analysis_out/
    achievements.json (hltools/achievements_data.py)."""
    global _ACHIEVEMENTS
    if _ACHIEVEMENTS is None:
        try:
            _ACHIEVEMENTS = json.loads((ANALYSIS / "achievements.json")
                                       .read_text(encoding="utf-8"))
        except Exception:
            _ACHIEVEMENTS = {}
    return _ACHIEVEMENTS


_RIFT_REWARDS = None


def rift_rewards_data():
    """analysis_out/rift_rewards.json (emit_offsets.extract_rift_rewards)."""
    global _RIFT_REWARDS
    if _RIFT_REWARDS is None:
        try:
            _RIFT_REWARDS = json.loads((ANALYSIS / "rift_rewards.json")
                                       .read_text(encoding="utf-8"))
        except Exception:
            _RIFT_REWARDS = {}
    return _RIFT_REWARDS


_INFUSIONS = None


def infusion_data():
    """{"infusions": {skill: {f, role, name, pattern, t2, t4, t6}},
    "item_faction": {item: faction}} from analysis_out/infusions.json
    (hltools/infusions_data.py)."""
    global _INFUSIONS
    if _INFUSIONS is None:
        try:
            _INFUSIONS = json.loads(
                (ANALYSIS / "infusions.json").read_text(encoding="utf-8"))
        except Exception:
            _INFUSIONS = {}
    return _INFUSIONS


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


_OFFSETS = None


def _offsets():
    """analysis_out/meter_offsets.json, read once."""
    global _OFFSETS
    if _OFFSETS is None:
        try:
            _OFFSETS = json.loads(
                (ANALYSIS / "meter_offsets.json").read_text(encoding="utf-8"))
        except Exception:
            _OFFSETS = {}
    return _OFFSETS


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
_GEAR_STATS = None


def gear_stats_data():
    global _GEAR_STATS
    if _GEAR_STATS is None:
        try:
            _GEAR_STATS = json.loads((ANALYSIS / "gear_stats.json").read_text(
                encoding="utf-8"))
        except Exception:
            _GEAR_STATS = {}
    return _GEAR_STATS


_BUILD_DATA = None


def build_data():
    """The Build tab's catalogue and rules: analysis_out/build_data.json
    (hltools/build_data.py)."""
    global _BUILD_DATA
    if _BUILD_DATA is None:
        try:
            _BUILD_DATA = json.loads((ANALYSIS / "build_data.json").read_text(
                encoding="utf-8"))
        except Exception:
            _BUILD_DATA = {}
    return _BUILD_DATA


_WORLD_MAP = None


def world_map():
    """{"meta": tiles and transform, "points": [{c, id, x, y, zone,
    region}]} from analysis_out/map.json (hltools/map_data.py)."""
    global _WORLD_MAP
    if _WORLD_MAP is None:
        try:
            _WORLD_MAP = json.loads(
                (ANALYSIS / "map.json").read_text(encoding="utf-8"))
        except Exception:
            _WORLD_MAP = {}
    return _WORLD_MAP


def _element_done(states, eid):
    """Whether a world element has been completed (chest opened, orb picked
    up, obelisk discovered): Progress.elements has it, with a time. A value
    kept from the first measuring build is a [byte, time] pair."""
    v = (states or {}).get(eid)
    if isinstance(v, list):
        v = v[-1] if v else None
    return isinstance(v, (int, float)) and v > 0


_UNIT_NAMES = None


def _unit_names():
    """kind -> display name, from analysis_out/unit_names.json — the game's
    own data.cdb rows, extracted by emit_offsets.py on the same self-heal
    cycle as the offsets. Loaded once; {} when the file is absent.

    Names the boss kill toast and every combat history dataset: a unit's kind
    is routinely NOT the name the game shows (measured: 'Cleodora' displays as
    'Queen Honeyzabeth', 'Phrixes' as 'High Inquisitor Chakram' — the kind
    often names the LAIR, not the boss)."""
    global _UNIT_NAMES
    if _UNIT_NAMES is None:
        try:
            _UNIT_NAMES = json.loads(
                (ANALYSIS / "unit_names.json").read_text(encoding="utf-8"))
        except Exception:
            _UNIT_NAMES = {}
    return _UNIT_NAMES


_FR_NAMES = None


def _fr_names(sheet):
    """id -> French display name for one of the game's sheets (activity,
    item, rarity, unit), from analysis_out/names_fr.json — the game's own
    translation, extracted by emit_offsets.py. {} when absent."""
    global _FR_NAMES
    if _FR_NAMES is None:
        try:
            _FR_NAMES = json.loads(
                (ANALYSIS / "names_fr.json").read_text(encoding="utf-8"))
        except Exception:
            _FR_NAMES = {}
    return _FR_NAMES.get(sheet) or {}


_ITEM_RARITY = None


def item_rarity(kind):
    """An item's base rarity from its sheet row (analysis_out/
    item_rarity.json); a weapon's own copy rarity overrides it."""
    global _ITEM_RARITY
    if _ITEM_RARITY is None:
        try:
            _ITEM_RARITY = json.loads(
                (ANALYSIS / "item_rarity.json").read_text(encoding="utf-8"))
        except Exception:
            _ITEM_RARITY = {}
    return _ITEM_RARITY.get(kind)


_ITEM_ICONS = {}


def item_icon(kind):
    """An item's icon as a data URI (analysis_out/item_icons/<id>.png,
    extracted from the game by emit_offsets.py), or "" when there is none.
    Inlined because the window loads nothing from anywhere."""
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


_DUNGEONS = None


def dungeon_catalogue():
    """Every dungeon in the game, [{kind, boss, region}], from
    analysis_out/dungeons.json (the game's achievements). [] when absent."""
    global _DUNGEONS
    if _DUNGEONS is None:
        try:
            _DUNGEONS = json.loads(
                (ANALYSIS / "dungeons.json").read_text(encoding="utf-8"))
        except Exception:
            _DUNGEONS = []
    return _DUNGEONS


# the raw materials' types, which the game's translation leaves unnamed
ITEM_TYPE_FR = {"Ore": "Minerai", "Cloth": "Tissu", "Leather": "Cuir"}


def item_type_label(t):
    return (_fr_names("itemType").get(t) or ITEM_TYPE_FR.get(t)
            or _pretty_id(t))


def _fr_desc(sheet):
    """id -> French description for a sheet (ach, item), from
    names_fr.json's "_desc"."""
    _fr_names(sheet)
    return (_FR_NAMES or {}).get("_desc", {}).get(sheet) or {}


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
            return f"Patron d'imprégnation : {inf.get('name')}"
    return name or _pretty_id(kind)


RARITY_FR = {"Common": "Ordinaire", "Uncommon": "Peu ordinaire",
             "Rare": "Rare", "Epic": "Épique", "Legendary": "Légendaire"}


def rarity_label(r):
    return _fr_names("rarity").get(r) or RARITY_FR.get(r) or (r or "")


_HEAL_SPECS = None


def _heal_specs():
    """skill id -> {step: [heal effect spec]}, from analysis_out/heal_specs.json.

    The game's own cdb, extracted on the same self-heal cycle as the offsets.
    Without it every heal on a full-health target is unsizeable and healing
    collapses back to "health actually restored" — so its absence is logged
    rather than swallowed."""
    global _HEAL_SPECS
    if _HEAL_SPECS is None:
        try:
            _HEAL_SPECS = json.loads(
                (ANALYSIS / "heal_specs.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[meter] heal_specs.json unavailable ({e}) — healing falls "
                  "back to what each skill has been seen to restore",
                  file=sys.stderr)
            _HEAL_SPECS = {}
    return _HEAL_SPECS


def _boss_label(kind):
    """The boss's real display name, falling back to the prettified kind for
    anything the unit sheet doesn't carry."""
    return (_fr_names("unit").get(kind) or _unit_names().get(kind)
            or _pretty_id(kind))


def _summon_label(kind):
    """A summon's real display name ('Summon_Imp' -> 'Nightling Terror',
    'Rabbit_EarlyAccess_Spark' -> 'Sparktail'), falling back to the prettified
    kind for anything the unit sheet doesn't carry.

    Same sheet and the same reason as _boss_label: a unit's kind is a backend
    id, not what the game puts on its nameplate. Stripping the `Summon_`/
    `Totem_` prefix off the kind instead looks like it works — `Summon_Imp`
    reduces to a plausible "Imp" — but it is a guess that happens to read well,
    and it degenerates to a raw id on every summon not named that way."""
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


# Set while regenerate_data runs: the title band says the game's data is
# being re-read, rather than a bare "Connexion…" for a minute.
REGENERATING = threading.Event()


# Top-level keys the current hook needs out of the two generated files. Data
# generated by older tools predates some of these, and the hlboot.dat stamp
# alone can't tell (the *game* hasn't changed, our tools have) — so a file
# missing any of them forces a regenerate.
#
# BOTH files have to be checked, and that is not a detail. 2.3 shipped with
# only the resolver list here, and every upgrade from 2.2 came up with a dead
# minimap: %LOCALAPPDATA% still held offsets generated before the minimap
# existed, missing Entity, ArrayObj, Element and the rest. The game hadn't
# changed, so the stamp matched; the resolver keys listed here were all
# present, so the currency check passed; and sweepWorld's first line is
# `if (!OFF.Entity || !OFF.ArrayObj) return`, which fails silently forever.
# Add to these lists whenever the hook starts reading something new.
REQUIRED_RESOLVER_KEYS = ("anchors", "boss_fns", "boss_targets", "cam_targets",
                          "count_targets", "funcs", "ui_targets",
                          # The codex (3.6). Without the hooks no popup ever
                          # fires, and without the map natives the kind->rank
                          # mirror is never read — so the "only missing from
                          # codex" filter shows everything, forever, with
                          # nothing anywhere saying why.
                          "codex_targets", "map_natives",
                          # Critter captures (3.7.3). Without these the collected
                          # list still refreshes on its 20s timer, so the cost
                          # is only a laggy filter — but the regenerate is the
                          # same either way.
                          "pet_targets")


# The minimap's half of the offsets. The combat half (DamageResult, HitData,
# ...) is deliberately not listed wholesale: it has been there since the first
# release, so it can't be what an upgrade is missing, and a list that mentions
# everything is a list nobody maintains.
#
# "Key.subkey" entries check INSIDE a group. DamageResult has existed forever,
# so its presence proves nothing — but 2.3.3 started reading `blocker` out of
# it to drop damage against an invulnerable target, and a 2.3.2 file has the
# group without that field. The hook degrades quietly when it's absent (no
# gate, immune damage counted again), which is precisely the silent-upgrade
# failure the rest of this comment is about.
REQUIRED_OFFSET_KEYS = ("DungeonCtx", "InstanceLobby", "Dungeon",
                        "Activity.globalCtx", "Group.instanceLobbies",
                        "Activity.contexts", "Player.activityCtx",
                        "Activity", "ArrayObj", "BossInfo", "BossesInfo",
                        "Camera", "DamageResult.blocker", "DamageResult.effect",
                        "Element", "Entity", "Foe", "GameLayer", "Hero",
                        "Interactible", "State", "String", "Unit", "Unit.attr",
                        # Healing on a full-health target. Without these the
                        # hook sends no dynVal/attributes and every overheal is
                        # sized 0 — the exact bug the feature exists to fix.
                        "BaseSkill.dynVal1", "HitData.step", "SkillStep",
                        "UnitAttributes.faith",
                        # The legendary pickup cue. Hero has existed forever,
                        # so its presence proves nothing — the subkeys are what
                        # a pre-3.1 file is missing, and sweepInventory() bails
                        # silently without them.
                        "Hero.loadout", "Hero.weaponInHand", "Inventory",
                        "Item", "Item.uid", "Loadout", "Weapon", "Weapon.rarity",
                        # The zone signal / map backdrop identity. GameLayer
                        # has existed forever; these subkeys are what a
                        # pre-3.0.4 file is missing — without them the hook
                        # falls back to no zone identity at all (getMapId is a
                        # hostname) and the backdrop never
                        # draws.
                        "GameLayer.world", "World.level",
                        # The party roster's array walk (Group.players is an
                        # hxbit proxy, not a plain array). Arrived with the
                        # retired mount feature's collection walk and stayed
                        # when that went: the roster reads through the same
                        # two hops.
                        "ArrayProxyData", "ArrayDyn",
                        # Ore/herb nodes. A pre-3.2.1 file lacks the group and
                        # sweepArray quietly draws no nodes at all.
                        "Gatherable",
                        # Summon and pet damage. A pre-3.3.4 file has no foe
                        # class list, and without it the hook cannot safely
                        # read summonOwner off a dealer — so it doesn't, and
                        # every pet's damage silently vanishes from the parse
                        # exactly as it did before the feature existed.
                        "foeClasses",
                        # The Social tab's shard roster. Player, Hero and
                        # GameLayer have all existed for releases, so their
                        # presence proves nothing — these five subkeys are what
                        # a pre-3.2.2 file lacks. readShard() returns an empty
                        # list on its first line without them, so the tab would
                        # sit there looking like an empty shard rather than
                        # like a stale data directory: exactly the silent
                        # upgrade failure this list exists to prevent.
                        "Player.uid", "Player.hero", "GameLayer.players",
                        "Hero.kind", "Hero.level",
                        # Naming a hit's target, which is what names a combat
                        # history dataset. A pre-3.5 file has no unit class
                        # list, and without it the hook refuses to read
                        # `Unit.kind` off a DamageResult.target (typed
                        # ent.GameObject, so the field may not be there at
                        # all) — every dataset would fall back to its zone
                        # name alone.
                        "DamageResult.target", "unitClasses",
                        # The codex (3.6). st.Player has existed forever, so
                        # its presence proves nothing — `progress` is the hop a
                        # pre-3.6 file lacks, and unitsProgressMap() returns
                        # null on its first line without it. The rest are new
                        # groups. Absent, the popups never fire and the map
                        # filter has no ranks to filter on.
                        "Player.progress", "Progress", "MapData", "StringMap",
                        "CodexProxy",
                        # Which shard you are on (3.7.1). GameLayer is listed
                        # above and has existed forever, so its presence proves
                        # nothing — `serverName` is the subkey a pre-3.7.1 file
                        # lacks. checkRift() guards on it being non-null and so
                        # simply never sends the shard, leaving the settings
                        # footer reading "…" for good with nothing anywhere
                        # saying why. Same silent-upgrade shape as the roster
                        # subkeys above.
                        "GameLayer.serverName",
                        # Collected critters (3.7.3). Player has existed forever;
                        # `accountProgress` and the two new groups are what a
                        # pre-3.7.3 file lacks — readPets() returns null without
                        # them and the "only uncollected" filter silently shows
                        # every critter forever.
                        "Player.accountProgress", "AccountProgress",
                        "Collection",
                        # The buff tracker (3.8). Unit is listed above and has
                        # existed forever, so `statuses` is the subkey a pre-3.8
                        # file lacks; the Status group is entirely new.
                        # sweepStatuses() bails on its first line without them
                        # and every tray sits empty, which reads as "I picked
                        # the wrong buffs" rather than as a stale data
                        # directory. GameLayer.time and TimeState are what turn
                        # an expiry into a countdown — without them a buff would
                        # show as up forever and never sweep.
                        "Unit.statuses", "Status", "Status.refreshDuration",
                        "GameLayer.time", "TimeState")


def _data_is_current():
    """True if both generated files carry everything the hook reads.

    Missing keys are named rather than just counted: this runs before the
    overlay exists, so the log is the only place anyone can see why a
    multi-second regenerate just happened.

    A key may be "Group.field", which checks for the field inside the group —
    a group that has existed for releases can still be missing a field this
    build depends on."""
    def present(d, key):
        group, _, field = key.partition(".")
        got = d.get(group)
        if not got:
            return False
        # `is not None` rather than truthiness: an offset of 0 is a real
        # offset, and `not 0` would condemn a perfectly good file.
        return True if not field else (isinstance(got, dict)
                                       and got.get(field) is not None)

    for name, required in (("resolver_data.json", REQUIRED_RESOLVER_KEYS),
                           ("meter_offsets.json", REQUIRED_OFFSET_KEYS)):
        try:
            d = json.loads((ANALYSIS / name).read_text())
        except Exception:
            return False
        missing = [k for k in required if not present(d, k)]
        if missing:
            print(f"[meter] {name} predates this build — missing "
                  f"{', '.join(missing)}; regenerating.", file=sys.stderr)
            return False
    # unit_names.json arrived with the boss kill timer (3.4). The generators
    # only re-run when this returns False, so an upgrade over an older
    # analysis_out has to fail here once or bosses and history datasets show
    # backend ids until the next game patch. Existence only — its content is
    # cosmetic and self-describing.
    if not (ANALYSIS / "unit_names.json").exists():
        print("[meter] unit_names.json absent — regenerating for the boss "
              "names.", file=sys.stderr)
        return False
    # heal_specs.json arrived when healing started counting overheal — without
    # it a heal that restores nothing cannot be sized, which is the whole
    # feature. Same upgrade trap as the two above.
    try:
        best = json.loads((ANALYSIS / "bestiary.json").read_text(
            encoding="utf-8"))
        if isinstance(best, list):
            print("[meter] bestiary.json predates the family view — "
                  "regenerating.", file=sys.stderr)
            return False
        if "spawns" not in best:
            print("[meter] bestiary.json predates the monster page — "
                  "regenerating.", file=sys.stderr)
            return False
    except (OSError, ValueError):
        pass
    if not (ANALYSIS / "codex_items.json").exists():
        print("[meter] codex_items.json absent — regenerating for the item "
              "collection.", file=sys.stderr)
        return False
    if not (ANALYSIS / "augments.json").exists():
        print("[meter] augments.json absent — regenerating for the gear "
              "augments.", file=sys.stderr)
        return False
    if not (ANALYSIS / "talents.json").exists():
        print("[meter] talents.json absent — regenerating for the talent "
              "trees.", file=sys.stderr)
        return False
    if not (ANALYSIS / "item_types.json").exists():
        print("[meter] item_types.json absent — regenerating for the "
              "Character tab.", file=sys.stderr)
        return False
    if not (ANALYSIS / "map_tiles" / "icon_rift.webp").exists():
        print("[meter] map icons absent — regenerating for the Map tab.",
              file=sys.stderr)
        return False
    if not (ANALYSIS / "map.json").exists():
        print("[meter] map.json absent — regenerating for the Map tab.",
              file=sys.stderr)
        return False
    if not (ANALYSIS / "bestiary.json").exists():
        print("[meter] bestiary.json absent — regenerating for the hunting "
              "log.", file=sys.stderr)
        return False
    try:
        has_gears = "gears" in json.loads(
            (ANALYSIS / "collection.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        has_gears = False
    if not has_gears:
        print("[meter] collection.json absent — regenerating for the "
              "Collection tab.", file=sys.stderr)
        return False
    if not (ANALYSIS / "ui_logo.png").exists():
        print("[meter] ui_logo.png absent — regenerating for the window's "
              "header.", file=sys.stderr)
        return False
    if not (ANALYSIS / "boss_portraits").is_dir():
        print("[meter] boss_portraits absent — regenerating for the dungeon "
              "list.", file=sys.stderr)
        return False
    try:
        dgs = json.loads((ANALYSIS / "dungeons.json").read_text(
            encoding="utf-8"))
        if dgs and "loot" not in dgs[0]:
            print("[meter] dungeons.json predates the loot tables — "
                  "regenerating.", file=sys.stderr)
            return False
    except (OSError, ValueError):
        pass
    if not (ANALYSIS / "dungeons.json").exists():
        print("[meter] dungeons.json absent — regenerating for the dungeon "
              "list.", file=sys.stderr)
        return False
    if not (ANALYSIS / "item_icons").is_dir():
        print("[meter] item_icons absent — regenerating for the loot "
              "icons.", file=sys.stderr)
        return False
    if not (ANALYSIS / "item_rarity.json").exists():
        print("[meter] item_rarity.json absent — regenerating for the loot "
              "rarities.", file=sys.stderr)
        return False
    if not (ANALYSIS / "names_fr.json").exists():
        print("[meter] names_fr.json absent — regenerating for the French "
              "names (dungeons, items, bosses).", file=sys.stderr)
        return False
    if not (ANALYSIS / "achievements.json").exists():
        print("[meter] achievements.json absent — regenerating for the "
              "Succès tab.", file=sys.stderr)
        return False
    if not (ANALYSIS / "rift_rewards.json").exists():
        print("[meter] rift_rewards.json absent — regenerating for the rift "
              "rewards.", file=sys.stderr)
        return False
    if not (ANALYSIS / "luck.json").exists():
        print("[meter] luck.json absent — regenerating for the luck "
              "counters.", file=sys.stderr)
        return False
    if not (ANALYSIS / "infusions.json").exists():
        print("[meter] infusions.json absent — regenerating for the gear "
              "infusions.", file=sys.stderr)
        return False
    if not (ANALYSIS / "heal_specs.json").exists():
        print("[meter] heal_specs.json absent — regenerating so healing can "
              "be counted on full-health targets.", file=sys.stderr)
        return False
    return True


def forget_loaded_data():
    """Drop every table loaded from analysis_out/, so the next use reads the
    files a regenerate just wrote (Réparer)."""
    g = globals()
    for name in ("_BUILD_DATA", "_GEAR_STATS", "_COLLECTION", "_CODEX_ITEMS", "_SPARK", "_BESTIARY", "_CODEX_SETS", "_ITEM_TYPES", "_AUGMENTS", "_TALENTS", "_LUCK", "_ACHIEVEMENTS", "_RIFT_REWARDS", "_INFUSIONS", "_OFFSETS", "_WORLD_MAP", "_UNIT_NAMES", "_FR_NAMES", "_ITEM_RARITY", "_DUNGEONS", "_HEAL_SPECS",):
        g[name] = None


def regenerate_data(hlboot=None, force=False, on_step=None):
    """Re-run the target/offset generators against the given hlboot.dat (or the
    tools' own auto-detect when None). Self-heals the shipped JSONs after a
    Farever patch. Skips the multi-second reparse when the same hlboot.dat is
    unchanged since the last successful run. Returns True on success."""
    tools = [ROOT / "hltools" / "build_targets.py",
             ROOT / "hltools" / "emit_offsets.py"]
    missing = [t.name for t in tools if not t.exists()]
    if missing:
        print(f"[meter] can't self-heal — missing {', '.join(missing)} "
              "(copy the whole farevermeter-plus folder).", file=sys.stderr)
        return False
    stamp = None
    if hlboot is not None:
        st = Path(hlboot).stat()
        stamp = {"src": str(hlboot), "mtime": st.st_mtime, "size": st.st_size}
        if not force:
            try:
                # Say WHY when the skip doesn't happen. Regenerating costs two
                # subprocess parses of a 14 MB bytecode file, right as the game
                # is loading, and without this the log shows the cost with no
                # reason attached — which is exactly the state that made a
                # stale stamp take an hour to spot. `_data_is_current` prints
                # its own reason, so only the stamp arm needs one here.
                on_disk = json.loads(DATA_STAMP.read_text())
                if on_disk != stamp:
                    print(f"[meter] hlboot.dat has changed since the last "
                          f"regenerate (stamp {on_disk.get('size')} bytes, "
                          f"now {stamp['size']}); regenerating.",
                          file=sys.stderr)
                elif (not (ANALYSIS / "resolver_data.json").is_file()
                        or not (ANALYSIS / "meter_offsets.json").is_file()):
                    print("[meter] a generated file is missing; regenerating.",
                          file=sys.stderr)
                elif _data_is_current():
                    print("[meter] data already matches this build "
                          "(hlboot.dat unchanged).", file=sys.stderr)
                    return True
            except FileNotFoundError:
                print("[meter] no data stamp yet; regenerating.",
                      file=sys.stderr)
            except Exception as e:
                print(f"[meter] couldn't read the data stamp ({e}); "
                      "regenerating.", file=sys.stderr)
    # The tools write beside their own location, which frozen is the bundle's
    # temp directory — the output would be thrown away with it on exit. Point
    # them at the writable copy instead. Harmless from source, where the two
    # paths are already the same.
    env = dict(os.environ, FAREVER_ANALYSIS_OUT=str(ANALYSIS))
    REGENERATING.set()
    try:
        return _run_generators(tools, hlboot, env, stamp, on_step)
    finally:
        REGENERATING.clear()


def _run_generators(tools, hlboot, env, stamp, on_step=None):
    labels = {"build_targets.py": "cibles du code",
              "emit_offsets.py": "structures, images et tables"}
    for t in tools:
        print(f"[meter] regenerating {t.name} for this build ...", file=sys.stderr)
        if on_step:
            on_step(labels.get(t.name, t.name))
        # Frozen there is no python.exe to hand a script to, and sys.executable
        # is this program — so it re-invokes itself in tool mode instead.
        cmd = ([sys.executable, TOOL_FLAG, t.name] if FROZEN
               else [sys.executable, str(t)])
        if hlboot is not None:
            cmd.append(str(hlboot))
        # Without CREATE_NO_WINDOW a console flashes up for each tool on every
        # launch of the windowed build — twice, right as the game is loading.
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            print(f"[meter] {t.name} failed:\n{r.stdout}\n{r.stderr}",
                  file=sys.stderr)
            return False
    if stamp is not None:
        # Written AND read back. A stamp that silently fails to land costs a
        # full regenerate on every single launch — the data stays correct, so
        # nothing looks wrong except several seconds of startup, and the old
        # `except OSError: pass` made that invisible. Whatever goes wrong here,
        # the log now says so once per launch instead of never.
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
    """Find the hlboot.dat matching the *running* game. Priority: explicit
    FAREVER_HLBOOT override, then the file next to the process's own exe (which
    makes a multi-install mismatch impossible), then the drive auto-detect, and
    finally just asking. Returns a Path or None (= use shipped data as-is)."""
    env = os.environ.get("FAREVER_HLBOOT")
    if env:
        if Path(env).is_file():
            return Path(env)
        print(f"[meter] FAREVER_HLBOOT points to a missing file: {env}",
              file=sys.stderr)
    exe = _exe_path_of_pid(pid)
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
    # Nothing is asked any more: this runs on the game link's thread, with no
    # window of its own to ask from. The shipped data is used as-is, which is
    # fine unless the game has patched since.
    print("[meter] hlboot.dat not found — using the shipped data files. Set "
          "FAREVER_HLBOOT to its full path if Farever is installed somewhere "
          "unusual.", file=sys.stderr)
    return None



# ---------------------------------------------------------------------------
# 3D models, for the Collection's viewer
# ---------------------------------------------------------------------------
MODELS_DIR = ANALYSIS / "models"
MODEL_FORMAT = 2
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


def item_model_json(item_id):
    """One collectible's model for the viewer, as JSON text — read off the
    game's files the first time (a second or so), from the cache after. The
    cache is keyed to res.pak, so a game patch rebuilds it. None when the
    item has no model this reader understands, or the game isn't found."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", str(item_id or "")):
        return None
    game = _game_dir()
    if game is None or not (game / "res.pak").is_file():
        return None
    st = (game / "res.pak").stat()
    # the converter's version too: a better one rebuilds what the last made
    key = f"{MODEL_FORMAT}:{st.st_size}:{int(st.st_mtime)}"
    cache = MODELS_DIR / f"{item_id}.json"
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
            m = hmd_model.item_model(game, item_id)
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
