"""The game's data as the app reads it: the tables generated into analysis_out/
(names, items, catalogues), their regeneration after a game patch, and the
hook's source."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from common import (
    ANALYSIS, CREATE_NO_WINDOW, FRIDA_DIR, FROZEN, GAME_PATH_FILE, ROOT,
    TOOL_FLAG, _pretty_id)


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



# The tables of analysis_out/, each read once, when first needed.
_TABLES = {}


def _table(name, empty=dict, shape=None, warn=None):
    """analysis_out/<name>, parsed (then shaped), read once; `empty()` when
    it is absent or unreadable — said in the log when `warn` says what that
    costs."""
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
        return "Démons des failles"
    if fam == "Human":                  # the game has no French name for it
        return "Humains"
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

def infusion_data():
    """{"infusions": {skill: {f, role, name, pattern, t2, t4, t6}},
    "item_faction": {item: faction}} from analysis_out/infusions.json
    (hltools/infusions_data.py)."""
    return _table("infusions.json")
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

def build_data():
    """The Build tab's catalogue and rules: analysis_out/build_data.json
    (hltools/build_data.py)."""
    return _table("build_data.json")

def world_map():
    """{"meta": tiles and transform, "points": [{c, id, x, y, zone,
    region}]} from analysis_out/map.json (hltools/map_data.py)."""
    return _table("map.json")
def _element_done(states, eid):
    """Whether a world element has been completed (chest opened, orb picked
    up, obelisk discovered): Progress.elements has it, with a time. A value
    kept from the first measuring build is a [byte, time] pair."""
    v = (states or {}).get(eid)
    if isinstance(v, list):
        v = v[-1] if v else None
    return isinstance(v, (int, float)) and v > 0



def _unit_names():
    """kind -> display name, from analysis_out/unit_names.json — the game's
    own data.cdb rows, extracted by emit_offsets.py on the same self-heal
    cycle as the offsets. Loaded once; {} when the file is absent.

    Names the boss kill toast and every combat history dataset: a unit's kind
    is routinely NOT the name the game shows (measured: 'Cleodora' displays as
    'Queen Honeyzabeth', 'Phrixes' as 'High Inquisitor Chakram' — the kind
    often names the LAIR, not the boss)."""
    return _table("unit_names.json")

def _fr_names(sheet):
    """id -> French display name for one of the game's sheets (activity,
    item, rarity, unit), from analysis_out/names_fr.json — the game's own
    translation, extracted by emit_offsets.py. {} when absent."""
    return _table("names_fr.json").get(sheet) or {}

def item_rarity(kind):
    """An item's base rarity from its sheet row (analysis_out/
    item_rarity.json); a weapon's own copy rarity overrides it."""
    return _table("item_rarity.json").get(kind)
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



def dungeon_catalogue():
    """Every dungeon in the game, [{kind, boss, region}], from
    analysis_out/dungeons.json (the game's achievements). [] when absent."""
    return _table("dungeons.json", list)
# the raw materials' types, which the game's translation leaves unnamed
ITEM_TYPE_FR = {"Ore": "Minerai", "Cloth": "Tissu", "Leather": "Cuir"}


def item_type_label(t):
    return (_fr_names("itemType").get(t) or ITEM_TYPE_FR.get(t)
            or _pretty_id(t))


def _fr_desc(sheet):
    """id -> French description for a sheet (ach, item, unit), from
    names_fr.json's "_desc"."""
    return _table("names_fr.json").get("_desc", {}).get(sheet) or {}


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



def _heal_specs():
    """skill id -> {step: [heal effect spec]}, from analysis_out/heal_specs.json.

    The game's own cdb, extracted on the same self-heal cycle as the offsets.
    Without it every heal on a full-health target is unsizeable and healing
    collapses back to "health actually restored" — so its absence is logged
    rather than swallowed."""
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
# One regenerate at a time: the first launch's and the game link's would
# otherwise write the same files together. The second, once the first is
# done, finds the stamp current and returns at once.
_REGEN_LOCK = threading.Lock()
# Bumped by each regenerate that wrote new files: the window resends the
# pictures (skills, collection, dungeons...) when it changes.
DATA_GENERATION = [0]


# Bumped when the generators' output changes shape: data written by older
# tools is regenerated once, though the game itself has not changed.
DATA_FORMAT = 2


def _hook_needs():
    """What the hook reads out of the generated files, read off its own
    source: OFF.<group>[.<field>] in meter_offsets.json, DATA.<key> in
    resolver_data.json. Never out of step with it."""
    try:
        src = (FRIDA_DIR / "meter_hook.js").read_text(encoding="utf-8")
    except OSError:
        return set(), set()
    offsets = {f"{g}.{f}" if f else g
               for g, f in re.findall(r"\bOFF\.(\w+)(?:\.(\w+))?", src)}
    return offsets, set(re.findall(r"\bDATA\.(\w+)", src))


def _data_is_current():
    """True when the generated files carry everything the hook reads and
    every table is there. What is missing is named in the log: this runs
    before the window exists."""
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
    """The installed app's first launch: its data folder holds only the few
    tables it ships with — no pictures, icons, models, build data. The
    welcome screen asks the player before reading them off the game."""
    return not (ANALYSIS / "build_data.json").is_file()


# Set once the player has agreed to the game's files being read (the
# welcome screen), or at once when the data is already there. The game link
# waits for it before its own regenerate.
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
        stamp = {"src": str(hlboot), "mtime": st.st_mtime, "size": st.st_size,
                 "format": DATA_FORMAT}
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
        ok = _run_generators(tools, hlboot, env, stamp, on_step,
                             on_progress)
    finally:
        REGENERATING.clear()
    if ok:
        forget_loaded_data()
        DATA_GENERATION[0] += 1
    return ok


# What the generators write, grouped as the welcome screen lists them: each
# group's outputs ("[written] <file>" lines, in this order), the picture
# folders whose files it counts as they arrive, and its share of the time
# (measured 2026-10-04: 21 s in all, the pictures nearly all of it).
GENERATED_GROUPS = (
    ("Code et structures du jeu",
     ("resolver_data.json", "meter_offsets.json"), (), 3),
    ("Créatures et textes en français",
     ("unit_names.json", "heal_specs.json", "codex_units.json",
      "unit_traits.json", "names_fr.json"), (), 2),
    ("Images de la collection", ("collection.json",), ("collection_img",), 19),
    ("Sorts, talents et leurs icônes", ("talents.json",), ("skill_img",), 9),
    ("Builds, équipement et succès",
     ("achievements.json", "infusions.json", "build_data.json",
      "gear_stats.json", "codex_items.json"), (), 3),
    ("Bestiaire et décors des donjons", ("bestiary.json",),
     ("bestiary_img", "dungeon_bg"), 34),
    ("Carte du monde", ("map.json",), ("map_tiles",), 18),
    ("Donjons et fiches des boss",
     ("dungeons.json", "boss_sheets.json", "boss_portraits"),
     ("boss_portraits",), 2),
    ("Icônes des objets",
     ("ui_logo.png", "augments.json", "rift_rewards.json", "luck.json",
      "item_types.json", "item_rarity.json", "item_icons"),
     ("item_icons",), 10),
)
# Roughly how many pictures each folder ends with: only the bar's progress
# inside a group leans on them (the counts shown are the real ones).
GENERATED_PICTURES = {"collection_img": 855, "skill_img": 806,
                      "bestiary_img": 408, "dungeon_bg": 36, "map_tiles": 115,
                      "boss_portraits": 13, "item_icons": 1200}
# ...and what one costs against the others: a dungeon's backdrop is a full
# screen, a dozen icons' time
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
            on_step(labels.get(t.name, t.name))
        # Frozen there is no python.exe to hand a script to, and sys.executable
        # is this program — so it re-invokes itself in tool mode instead.
        cmd = ([sys.executable, TOOL_FLAG, t.name] if FROZEN
               else [sys.executable, str(t)])
        if hlboot is not None:
            cmd.append(str(hlboot))
        # Without CREATE_NO_WINDOW a console flashes up for each tool on every
        # launch of the windowed build — twice, right as the game is loading.
        # Read as it comes: each "[written]" line is a step of the progress.
        # unbuffered: a pipe is otherwise filled in blocks, and every line
        # came at the end, the progress jumping from 4 % to done
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
MODEL_FORMAT = 13
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
    item has no model this reader understands, or the game isn't found.
    "<id>@anim" asks for a monster's idle animation with it;
    "hero:<slot>=<id>.<slot>=<id>…" the hero wearing those pieces (a
    build's), in its idle."""
    hero = None
    if str(item_id or "").startswith("hero:"):
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
