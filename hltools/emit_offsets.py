"""
emit_offsets.py — compute the field offsets the hook reads (from
hlbc_parser.field_offsets(), which mirrors hl_get_obj_rt) into
analysis_out/meter_offsets.json, then write every data table. Re-run after
a Farever patch.
"""
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode, HOBJ, HSTRUCT
from gamepath import find_hlboot

# Overridable: the installed app runs this from a temporary bundle directory.
_OUT_DIR = Path(os.environ.get("FAREVER_ANALYSIS_OUT")
                or Path(__file__).resolve().parent.parent / "analysis_out")
_OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = _OUT_DIR / "meter_offsets.json"


# (class, the name the hook uses) -> the field's name since a patch renamed it
FIELD_RENAMES = {
    # 2026-09-30 patch
    ("st.skill.DamageResult", "baseSkill"): "skill",
    ("st.skill.HitData", "baseSkill"): "skill",
}


def main():
    hlboot = find_hlboot()
    print(f"[*] parsing {hlboot}")
    layout = hook_layout(HLCode(hlboot).parse())
    OUT.write_text(json.dumps(layout, indent=2), encoding="utf-8")
    print(f"[written] {OUT}")
    write_tables(Path(hlboot).parent)
    print(json.dumps(layout, indent=2))


def hook_layout(code):
    """The offset of each field the hook reads, from the game's bytecode."""
    byname = {t.name: t for t in code.types
              if t.kind in (HOBJ, HSTRUCT) and t.name}

    def offs(name):
        f = code.field_offsets(byname[name].index)
        for (cls, old), new in FIELD_RENAMES.items():
            if cls == name and old not in f and new in f:
                f[old] = f[new]
        return f

    def descendants(root):
        """Every class that inherits from `root`, itself included."""
        ti = byname[root].index
        out = []
        for t in code.types:
            if t.kind not in (HOBJ, HSTRUCT) or not t.name:
                continue
            i, seen = t.index, set()
            while 0 <= i < len(code.types) and i not in seen:
                seen.add(i)
                if i == ti:
                    out.append(t.name)
                    break
                i = code.types[i].super_index
        return out

    dr = offs("st.skill.DamageResult")
    hd = offs("st.skill.HitData")
    ua = offs("ent.UnitAttributes")
    hero = offs("ent.Hero")
    layer = offs("st.GameLayer")
    tstate = offs("TimeState")
    player = offs("st.Player")
    group = offs("st.Group")
    base = offs("st.skill.BaseSkill")
    step = offs("st.skill.SkillStep")
    string = offs("String")
    activity = offs("st.Activity")
    arrobj = offs("hl.types.ArrayObj")
    foe = offs("ent.Foe")
    unit = offs("ent.Unit")
    status = offs("st.skill.Status")
    bosses = offs("ui.hud.BossesInfo")
    bossinfo = offs("ui.hud.BossInfo")
    loadout = offs("st.Loadout")
    inv = offs("st.Inventory")
    equip = offs("st.Equipment")
    item = offs("st.Item")
    weapon = offs("st.item.Weapon")
    world = offs("world.World")
    aproxy = offs("hxbit.ArrayProxyData")
    adyn = offs("hl.types.ArrayDyn")
    progress = offs("st.player.Progress")
    acct = offs("st.player.AccountProgress")
    coll = offs("st.player.Collection")
    mapdata = offs("hxbit.MapData")
    smap = offs("haxe.ds.StringMap")
    # hxbit names each proxy class after its record's fields, so a patch that
    # changes the record renames the class and fails loudly here.
    kproxy = offs("hxbit.ObjProxy_OkillCount_Int_rank_Int")
    eproxy = offs("hxbit.ObjProxy_Ocompleted_Float")
    spec = offs("st.player.HeroSpecialization")
    gear = offs("st.item.Gear")
    weapon_ = offs("st.item.Weapon")
    skill = offs("st.skill.Skill")
    # A dungeon run's state is on its DungeonContext; its difficulty is on the
    # group's instance lobby.
    dctx = offs("st.activity.DungeonContext")
    dact = offs("st.activity.Dungeon")
    lobby = offs("st.player.InstanceLobby")

    # st.Equipment extends st.Inventory: the hook reads both with one offset.
    if equip["content"][0] != inv["content"][0]:
        raise SystemExit(
            f"[!] st.Equipment.content@{equip['content'][0]} != "
            f"st.Inventory.content@{inv['content'][0]} — the containers no "
            "longer share a layout; fix the inventory sweep before shipping.")

    meta = {
        "String": {"bytes": string["bytes"][0]},
        # _block / blocker / effect: which one marks a nullified hit (boss
        # immunity phase) is not settled yet.
        "DamageResult": {k: dr[k][0] for k in
            ["_amount", "affinity", "_critical", "_kill", "_block",
             "blocker", "effect", "baseSkill"]},
        # dynVal1-3 (hxbit-replicated) carry most heals' server-computed
        # amount: the only place a client learns what a heal was worth.
        "BaseSkill": {"kind": base["kind"][0], "inf": base["inf"][0],
                      "owner": base["owner"][0],
                      "ownerPlayer": base["ownerPlayer"][0],
                      "dynVal1": base["dynVal1"][0],
                      "dynVal2": base["dynVal2"][0],
                      "dynVal3": base["dynVal3"][0]},
        "HitData": {"baseSkill": hd["baseSkill"][0],
                    "step": hd["step"][0]},
        "SkillStep": {"index": step["index"][0]},
        # The rest of the heal skills scale off one of the caster's primary
        # attributes (Faith on 17 of them, then Intellect/Strength/Dexterity).
        "UnitAttributes": {"unit": ua["unit"][0], "health": ua["health"][0],
                           "faith": ua["faith"][0],
                           "intellect": ua["intellect"][0],
                           "strength": ua["strength"][0],
                           "dexterity": ua["dexterity"][0]},
        # Hero.layer (inherited from st.State) is the hero's GameLayer.
        "Hero": {"name": hero["name"][0], "player": hero["player"][0],
                 "isInCombat": hero["isInCombat"][0],
                 "layer": hero["layer"][0],
                 "loadout": hero["loadout"][0],
                 # class and level: st.player.HeroData is null on the client
                 "kind": hero["kind"][0], "level": hero["_level"][0]},
        "Loadout": {"inventory": loadout["inventory"][0],
                    "equipment": loadout["equipment"][0],
                    # the bank's tabs (hxbit.ArrayProxyData)
                    "banks": loadout["banks"][0]},
        # content holds slot virtuals {count, item}, not items (see readSlot()
        # in meter_hook.js).
        "Inventory": {"content": inv["content"][0]},
        "Item": {"kind": item["kind"][0],
                 # the copy's flags (st.ItemFlag: Flawless, Prismatic), an
                 # hxbit.EnumFlagsData whose `value` holds the bits
                 "flags": item["flags"][0]},
        "EnumFlagsData": {"value": offs("hxbit.EnumFlagsData")["value"][0]},
        # st.ItemFlag constructor -> bit, by name
        "ItemFlag": next({(c[0] if isinstance(c, tuple) else c.name): i
                          for i, c in enumerate(t.constructs)}
                         for t in code.types if t.name == "st.ItemFlag"),
        # `rarity` exists ONLY on st.item.Weapon: past the end of any other item.
        "Weapon": {"rarity": weapon["rarity"][0], "level": weapon["level"][0]},
        "GameLayer": {"isRift": layer["isRift"][0],
                      # the shard, replicated: "Sfojuxa3386_6601_na" (name,
                      # instance, region; measured 2026-08-05)
                      "serverName": layer["serverName"][0],
                      "mainActivity": layer["mainActivity"][0],
                      "time": layer["_time"][0],
                      # world.World.level is the zone (Main.getMapId() returns
                      # the machine name)
                      "world": layer["world"][0],
                      # every player on the shard, not only those nearby
                      "players": layer["players"][0]},
        "World": {"level": world["level"][0]},
        "Activity": {"kind": activity["kind"][0]},
        "Dungeon": {"bossId": dact["bossId"][0]},
        "DungeonCtx": {"dungeonState": dctx["dungeonState"][0],
                       "endActivity": dctx["endActivity"][0],
                       "nbPlayerDeaths": dctx["nbPlayerDeaths"][0]},
        "InstanceLobby": {"activityId": lobby["activityId"][0],
                          "difficulty": lobby["difficulty"][0]},
        # `kind` is the internal id ("Crimson_Z2W_Sword").
        "Unit": {"kind": unit["kind"][0],
                 # maxHealth reads 0 during a boss fight (measured): health
                 # only tells "did it die", never a percentage.
                 "attr": unit["attr"][0],
                 # an hxbit proxy array of st.skill.Status
                 "statuses": unit["statuses"][0]},
        # Measured: stopTime is always -1; a status ends at startTime +
        # duration, and duration grows on every refresh.
        "Status": {"kind": status["kind"][0],
                   "startTime": status["startTime"][0],
                   "stopTime": status["stopTime"][0],
                   "duration": status["duration"][0],
                   "removed": status["removed"][0]},
        # a foe with a summonOwner is a pet (same class as a mob)
        "Foe": {"summonOwner": foe["summonOwner"][0]},
        # the hl_varray's elements start at +24, past its (t, at, size, pad)
        # header
        "ArrayObj": {"length": arrobj["length"][0], "array": arrobj["array"][0],
                     "data": 24},
        # the server's clock (statuses' times are on it)
        "TimeState": {"serverNow": tstate["serverNow"][0]},
        # `hero` is set for every player on the layer, not only those nearby
        "Player": {"name": player["name"][0], "group": player["group"][0],
                   "isMe": player["isMe"][0], "hero": player["hero"][0],
                   # per character
                   "progress": player["progress"][0],
                   # account-wide: companions, mounts, gliders
                   "accountProgress": player["accountProgress"][0],
                   # where a dungeon's DungeonContext lives on the client
                   # (Activity.globalCtx reads null there)
                   "activityCtx": player["activityCtx"][0]},
        # Collection.pets holds UNIT kinds ("Turtle_Grey"), not item ids
        # (measured 2026-08-07).
        "AccountProgress": {"collection": acct["collection"][0],
                            "achievements": acct["achievements"][0]},
        # mounts / gliders: the same proxy arrays, of item kinds.
        "Collection": {"pets": coll["pets"][0], "mounts": coll["mounts"][0],
                       "gliders": coll["gliders"][0],
                       "gears": coll["gears"][0]},
        # a replicated array: ArrayProxyData -> ArrayDyn -> ArrayObj
        "ArrayProxyData": {"array": aproxy["array"][0]},
        "ArrayDyn": {"array": adyn["array"][0]},
        "Group": {"players": group["players"][0],
                  "instanceLobbies": group["instanceLobbies"][0]},
        # The codex (measured 2026-08-05): Progress.unitsProgress -> MapData.map
        # (a virtual, the StringMap at +8) -> StringMap.h -> hbget(unitKind)
        # -> {killCount, rank}. Every unit set has three tiers: rank 3 = done.
        "Progress": {"unitsProgress": progress["unitsProgress"][0],
                     "itemProgress": progress["itemProgress"][0],
                     # element id -> state: chests, orbs, obelisks...
                     "elements": progress["elements"][0],
                     # counter id -> value, the Luck_* counters among them
                     "counters": progress["counters"][0],
                     "achievements": progress["achievements"][0]},
        "MapData": {"map": mapdata["map"][0], "value": 8},
        # Progress.elements' value; a never-completed element has no entry
        # (measured 2026-09-28)
        "ElementProxy": {"completed": eproxy["completed"][0]},
        # the client holds every hero's equipment, talents and skills
        "HeroDetail": {k: hero[k][0] for k in
                       ("skills", "specialization", "weaponSkills")},
        "Specialization": {k: spec[k][0] for k in
                           ("talents", "skillSlots", "skillMasteries",
                            "arsenals", "prayerSequence")},
        # the talent map's values ({rank}) and the arsenal map's ({skills})
        "RankProxy": {"rank": offs("hxbit.ObjProxy_Orank_Int")["rank"][0]},
        # Progress.itemProgress's values: the item codex (count, rank)
        "ItemProxy": {k: offs("hxbit.ObjProxy_OitemCount_Int_rank_Int")[k][0]
                      for k in ("itemCount", "rank")},
        "SkillsProxy": {"skills": offs(
            "hxbit.ObjProxy_Oskills_Arr_Data_SkillKind")["skills"][0]},
        "Skill": {"kind": skill["kind"][0]},
        # upgradeLevel = stars, slots = augments, effects = a weapon's
        # enchantment formula
        "Gear": {"level": gear["level"][0],
                 "upgradeLevel": gear["upgradeLevel"][0],
                 "slots": gear["slots"][0],
                 "effects": weapon_["effects"][0],
                 # the infusion (2026-09-30 patch) and its faction bonus stat
                 **{k: gear[k][0] for k in ("infusion", "infusionBonusStat")
                    if k in gear}},
        "StringMap": {"h": smap["h"][0]},
        "CodexProxy": {"count": kproxy["killCount"][0],
                       "rank": kproxy["rank"][0]},
        # The boss healthbar: bossInfos' length is 0 or 1 (measured); read
        # `active`, not UIElement.visible, which lags at transitions.
        "BossesInfo": {"bossInfos": bosses["bossInfos"][0]},
        "BossInfo": {"active": bossinfo["active"][0],
                     "unit": bossinfo["unit"][0]},
        # A dealer must be one of these before Foe.summonOwner is read off it:
        # a summon's class is not always the literal ent.Foe.
        "foeClasses": descendants("ent.Foe"),
    }
    return meta


class Table:
    """One output of the data folder: a JSON file, or pictures its builder
    writes itself (dump None). `summary` follows "[written] <path>", which
    the app's progress reads."""

    def __init__(self, out, build, summary=None, dump=None, label=None):
        self.out, self.build, self.summary = out, build, summary
        self.dump, self.label = dump, label or out


# how each JSON is written
PLAIN = {"indent": 0}
TEXT = {"ensure_ascii": False, "indent": 0}
COMPACT = {"ensure_ascii": False, "separators": (",", ":")}


def _module(name):
    """A generator module of hltools, imported when its table is built."""
    import importlib
    return importlib.import_module(name)


def _cdb(game):
    import pak_extract
    return json.loads(pak_extract.read_entry(game / "res.light.pak",
                                             "data.cdb"))


# In the app's order (meter/gamedata.py GENERATED_GROUPS lists the same).
# A builder gets (game folder, data folder, the tables built so far).
TABLES = (
    Table("unit_names.json", lambda g, o, t: extract_display_names(g),
          lambda d: f"{len(d)} units", TEXT, "display names"),
    Table("heal_specs.json", lambda g, o, t: extract_heal_specs(g),
          lambda d: f"{len(d)} heal skills", PLAIN),
    Table("codex_units.json", lambda g, o, t: extract_codex_units(g),
          lambda d: (f"{len(d['noCodex'])} excluded, {len(d['elite'])} "
                     f"elite/boss, {len(d['big'])} big, "
                     f"thresholds {d['thresholds']}"), PLAIN),
    Table("unit_traits.json", lambda g, o, t: extract_unit_traits(g),
          lambda d: f"{len(d['spark'])} sparkling", PLAIN),
    Table("names_fr.json", lambda g, o, t: extract_fr_names(g),
          lambda d: ", ".join(f"{len(v)} {k}" for k, v in d.items()), TEXT),
    Table("names_en.json", lambda g, o, t: extract_en_names(g),
          lambda d: ", ".join(f"{len(v)} {k}" for k, v in d.items()), TEXT),
    Table("collection.json",
          lambda g, o, t: _module("collection_data").build(
              g, o / "collection_img"),
          lambda d: ", ".join(f"{len(v)} {k}" for k, v in d.items()), PLAIN,
          "collection catalogue"),
    Table("talents.json",
          lambda g, o, t: _module("skills_data").build(g, o / "skill_img"),
          lambda d: f"{len(d['trees'])} trees, {len(d['runes'])} runes",
          PLAIN, "talent trees"),
    Table("achievements.json",
          lambda g, o, t: _module("achievements_data").build(
              g, o / "collection_img"),
          lambda d: f"{len(d['achievements'])} achievements", TEXT),
    Table("infusions.json",
          lambda g, o, t: _module("infusions_data").build(g),
          lambda d: f"{len(d['infusions'])} infusions", TEXT),
    Table("build_data.json", lambda g, o, t: _module("build_data").build(g),
          lambda d: f"{len(d['items'])} gear items", COMPACT, "build data"),
    Table("gear_stats.json",
          lambda g, o, t: _module("gear_stats_data").build(g),
          lambda d: f"{len(d['items'])} items", COMPACT, "gear stats"),
    Table("codex_items.json",
          lambda g, o, t: _module("codex_items").build(
              g, o / "collection_img"),
          lambda d: f"{len(d)} items", PLAIN, "item codex catalogue"),
    Table("bestiary.json",
          lambda g, o, t: _module("bestiary_data").build(
              g, t["codex_units.json"], o / "bestiary_img"),
          lambda d: f"{len(d)} monsters", PLAIN, "bestiary"),
    Table("map.json",
          lambda g, o, t: _module("map_data").build(g, o / "map_tiles"),
          lambda d: (f"{len(d['points'])} points, "
                     f"{len(d['meta']['tiles'])} tiles"), PLAIN, "world map"),
    Table("dungeons.json", lambda g, o, t: extract_dungeons(g),
          lambda d: f"{len(d)} dungeons", PLAIN),
    Table("boss_sheets.json",
          lambda g, o, t: _module("boss_sheets").build(
              _cdb(g), [d["boss"] for d in t["dungeons.json"]
                        if d.get("boss")]),
          lambda d: f"{len(d['bosses'])} bosses", PLAIN, "boss sheets"),
    Table("boss_portraits",
          lambda g, o, t: extract_boss_portraits(
              g, [d["boss"] for d in t["dungeons.json"]], o / "boss_portraits"),
          lambda n: f"{n} portraits", label="boss portraits"),
    Table("augments.json", lambda g, o, t: extract_augments(g),
          lambda d: f"{len(d)} augments", PLAIN),
    Table("rift_rewards.json", lambda g, o, t: extract_rift_rewards(g),
          lambda d: f"{len(d['bosses'])} bosses", PLAIN),
    Table("luck.json", lambda g, o, t: extract_luck(g),
          lambda d: f"{len(d)} counters", PLAIN),
    Table("item_types.json", lambda g, o, t: extract_item_types(g),
          lambda d: f"{len(d)} items", PLAIN),
    Table("item_rarity.json", lambda g, o, t: extract_item_rarity(g),
          lambda d: f"{len(d)} items", PLAIN),
    Table("item_icons",
          lambda g, o, t: extract_item_icons(g, o / "item_icons"),
          lambda n: f"{n} icons", label="item icons"),
)


def write_tables(game):
    """Every table, in order, into the data folder."""
    built = {}
    for table in TABLES:
        path = _OUT_DIR / table.out
        try:
            data = table.build(game, _OUT_DIR, built)
            if table.dump is not None:
                path.write_text(json.dumps(data, **table.dump),
                                encoding="utf-8")
        except Exception as e:
            print(f"[!] {table.label} skipped ({e})")
            continue
        built[table.out] = data
        print(f"[written] {path}"
              + (f" ({table.summary(data)})" if table.summary else ""))


# skill@steps@effects.effect is an enum ("5:Damage,Heal,..."): Heal's index is
# read off the column's typeStr, a patch could insert a kind before it.
HEAL_EFFECT_NAME = "Heal"


def extract_heal_specs(game_dir):
    """skill id -> {step index: how that step's heal amount is computed}.

    No heal amount reaches the client (measured 2026-08-03), so heals are
    computed the way the game does:
      dyn   -> the amount is in BaseSkill.dynVal1/2/3 (replicated)
      scale -> ratio x one of the caster's attributes
      base  -> a flat number (a floor when a dyn is also given)
    """
    import pak_extract
    data, entries, data_off = pak_extract.load(Path(game_dir) / "res.light.pak")
    e = next(x for x in entries if x.path.endswith("data.cdb"))
    cdb = json.loads(data[data_off + e.pos: data_off + e.pos + e.size])
    sheets = {sh["name"]: sh for sh in cdb["sheets"]}

    heal_idx = 1
    for c in sheets.get("skill@steps@effects", {}).get("columns", []):
        if c.get("name") == "effect" and isinstance(c.get("typeStr"), str):
            parts = c["typeStr"].split(":", 1)
            if len(parts) == 2:
                names = parts[1].split(",")
                if HEAL_EFFECT_NAME in names:
                    heal_idx = names.index(HEAL_EFFECT_NAME)
            break

    out = {}
    for ln in sheets["skill"]["lines"]:
        sid = ln.get("id")
        if not isinstance(sid, str):
            continue
        steps = {}
        for i, st in enumerate(ln.get("steps") or []):
            specs = []
            for ef in (st.get("effects") or []):
                if ef.get("effect") != heal_idx:
                    continue
                scale = [[sc.get("ratio"), sc.get("atb")]
                         for sc in (ef.get("scaling") or [])
                         if isinstance(sc.get("ratio"), (int, float))
                         and isinstance(sc.get("atb"), str)]
                spec = {}
                if isinstance(ef.get("baseVal"), (int, float)):
                    spec["base"] = ef["baseVal"]
                if scale:
                    spec["scale"] = scale
                if isinstance(ef.get("dynVal"), int) and ef["dynVal"] > 0:
                    spec["dyn"] = ef["dynVal"]
                if spec:
                    specs.append(spec)
            if specs:
                steps[str(i)] = specs
        if steps:
            out[sid] = steps
    return out


def extract_display_names(game_dir):
    """id -> texts.name for the cdb's unit sheet (the boss events' names)."""
    import pak_extract
    data, entries, data_off = pak_extract.load(Path(game_dir) / "res.light.pak")
    e = next(x for x in entries if x.path.endswith("data.cdb"))
    cdb = json.loads(data[data_off + e.pos: data_off + e.pos + e.size])

    def sheet_names(name):
        sheet = next(s for s in cdb["sheets"] if s["name"] == name)
        out = {}
        for ln in sheet["lines"]:
            iid, nm = ln.get("id"), (ln.get("texts") or {}).get("name")
            if isinstance(iid, str) and isinstance(nm, str) and nm:
                out[iid] = nm
        return out

    return sheet_names("unit")


# the sheets whose French names the app shows
FR_SHEETS = ("ach", "activity", "attribute", "faction", "gatherable",
             "item", "itemType", "job", "rarity", "skill", "unit",
             "unitType", "zone")
# the sheets whose French descriptions the app shows
FR_DESC = {"ach": ("desc",), "item": ("texts.flavorDesc", "texts.desc"),
           "unit": ("texts.desc",), "skill": ("texts.desc",)}


def extract_fr_names(game_dir):
    """sheet -> id -> French display name, from the game's own translation
    (res.pak lang/export_fr.xml: <sheet name=...><Id><texts.name>...)."""
    import xml.etree.ElementTree as ET
    import pak_extract
    raw = pak_extract.read_entry(Path(game_dir) / "res.pak",
                                 "lang/export_fr.xml")
    if raw is None:
        raise RuntimeError("lang/export_fr.xml not in res.pak")
    root = ET.fromstring(raw)
    out = {"_desc": {}}
    for sheet in root.findall("sheet"):
        name = sheet.get("name")
        if name not in FR_SHEETS:
            continue
        rows = out.setdefault(name, {})
        descs = out["_desc"].setdefault(name, {}) if name in FR_DESC else None
        for row in sheet:
            if descs is not None:
                for tag in FR_DESC[name]:
                    d = row.find(tag)
                    txt = "".join(d.itertext()).strip() if d is not None \
                        else ""
                    if txt:
                        descs[row.tag] = txt
                        break
            node = row.find("texts.name")
            if node is None:
                node = row.find("texts.name.v")     # itemType: {v, plural}
            if node is None:
                node = row.find("name")
            txt = "".join(node.itertext()).strip() if node is not None else ""
            if txt:
                rows[row.tag] = txt
            # a skill's masteries are rows of their own, nested in it
            for m in row.findall("mastery/*"):
                mn = m.find("text.name")
                mt = "".join(mn.itertext()).strip() if mn is not None else ""
                if mt:
                    rows[m.tag] = mt
    return out


def extract_en_names(game_dir):
    """names_fr.json's shape in English: the game's own texts, which data.cdb
    holds in English (the translations are the lang/export_*.xml)."""
    import pak_extract
    cdb = json.loads(pak_extract.read_entry(Path(game_dir) / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}

    def at(row, path):
        for k in path.split("."):
            row = row.get(k) if isinstance(row, dict) else None
        if isinstance(row, dict):           # itemType: {v, plural}
            row = row.get("v")
        return row.strip() if isinstance(row, str) else ""

    out = {"_desc": {}}
    for name in FR_SHEETS:
        rows = out.setdefault(name, {})
        descs = out["_desc"].setdefault(name, {}) if name in FR_DESC else None
        for row in (sheets.get(name) or {}).get("lines") or ():
            rid = row.get("id")
            if not isinstance(rid, str):
                continue
            if descs is not None:
                txt = next((at(row, tag) for tag in FR_DESC[name]
                            if at(row, tag)), "")
                if txt:
                    descs[rid] = txt
            txt = at(row, "texts.name") or at(row, "name")
            if txt:
                rows[rid] = txt
            for m in row.get("mastery") or ():
                if isinstance(m, dict) and isinstance(m.get("id"), str)                         and at(m, "text.name"):
                    rows[m["id"]] = at(m, "text.name")
    out["activity"] = _activity_names(game_dir)
    return out


def _activity_names(game_dir):
    """The activities' English names: their rows are not in data.cdb but in
    the prefabs that place them (activity objects, "$cdbtype": "activity"),
    the dungeons' in their level's gameplayData."""
    import hbson
    import pak_extract
    out = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("$cdbtype") == "activity" and isinstance(o.get("id"), str):
                name = (o.get("texts") or {}).get("name")
                if isinstance(name, str) and name.strip():
                    out[o["id"]] = name.strip()
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for pak_name, wanted in (("res.pak", "Gameplay/Activities/"),
                             ("res.levels.pak", "/gameplayData/")):
        pak = Path(game_dir) / pak_name
        with open(pak, "rb") as f:
            head = f.read(12)
            f.seek(0)
            entries, data_off = pak_extract.read_tree(
                f.read(struct.unpack_from("<i", head, 4)[0]), pak.name)
            for e in entries:
                if not e.path.endswith(".prefab") or wanted not in e.path:
                    continue
                f.seek(data_off + e.pos)
                blob = f.read(e.size)
                if b"activity" not in blob:
                    continue
                try:
                    walk(hbson.loads(blob))
                except Exception:
                    continue
    return out


def extract_luck(game_dir):
    """The loot luck counters (counter sheet, rows with luckParams): {id:
    {status, base, increment, max, itemType, minRarity}}. A character's
    Progress.counters holds each one's count; the bonus is base + count *
    increment, capped at max, tied to the status the Soulwell grants."""
    import pak_extract
    cdb = json.loads(pak_extract.read_entry(Path(game_dir) / "res.light.pak",
                                            "data.cdb"))
    sheet = next(s for s in cdb["sheets"] if s["name"] == "counter")
    return {ln["id"]: ln["luckParams"] for ln in sheet["lines"]
            if isinstance(ln.get("id"), str) and ln.get("luckParams")}


def extract_rift_rewards(game_dir):
    """What a rift gives, as st.activity.RiftContext hands it out (read in
    hlboot.dat, 2026-10-01):
    * the boss chest (Rift_RewardTiers[0] gates): the boss's lootTable (one
      weapon, by weight) and bossLootTable, Rare at least, then
      Rift_Bosschest, plus Rift_Tier4 from tier 3 and Rift_Tier6 from tier 5;
    * the other chests (tiers 1, 2, 4): Rift_BonusChest;
    * a weapon's rarity: the rarity sheet's generationChance at the player's
      level, Rare at least, Legendary through Luck_LegendaryWeapon."""
    import pak_extract
    cdb = json.loads(pak_extract.read_entry(Path(game_dir) / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}
    tables = {ln["id"]: ln for ln in sheets["lootTable"]["lines"]
              if isinstance(ln.get("id"), str)}
    consts = {ln["id"]: ln for ln in sheets["constant"]["lines"]
              if isinstance(ln.get("id"), str)}

    def lines(tid):
        return [{k: ln.get(k) for k in ("item", "lootTable", "proba",
                                         "itemMin")}
                for ln in (tables.get(tid) or {}).get("loot") or ()]

    tiers = [{"gates": f.get("v"), "desc": f.get("desc") or ""}
             for f in ((consts.get("Rift_RewardTiers") or {}).get("v") or {})
             .get("floats") or ()]
    bosses = []
    for u in sheets["unit"]["lines"]:
        p = u.get("props") or {}
        main = tables.get(p.get("lootTable")) or {}
        # a rift boss: a Demon whose loot table is one weighted pick
        if u.get("faction") != "Demon" or not p.get("bossLootTable") \
                or not (main.get("flags") or 0) & 1:
            continue
        bosses.append({"id": u["id"], "weapons": lines(p["lootTable"]),
                       "extra": lines(p["bossLootTable"])})
    return {"tiers": tiers, "bosses": bosses,
            "bossChest": lines("Rift_Bosschest"),
            "bonusChest": lines("Rift_BonusChest"),
            "tier4": lines("Rift_Tier4"), "tier6": lines("Rift_Tier6"),
            "soulstone": lines("Soulstone")}


def extract_item_types(game_dir):
    """item id -> its type (Head, Hands, GreatAxe, Pickaxe, Mount...), from
    data.cdb: what sorts a character's equipment into gear and the rest."""
    import pak_extract
    cdb = json.loads(pak_extract.read_entry(Path(game_dir) / "res.light.pak",
                                            "data.cdb"))
    sheet = next(s for s in cdb["sheets"] if s["name"] == "item")
    return {ln["id"]: ln["type"] for ln in sheet["lines"]
            if isinstance(ln.get("id"), str) and ln.get("type")}


def extract_augments(game_dir):
    """What each augment (an item set into a gear's slot) does: {id: {t: type,
    a: [[attribute, value]], s: [skill]}}, from data.cdb."""
    import pak_extract
    cdb = json.loads(pak_extract.read_entry(Path(game_dir) / "res.light.pak",
                                            "data.cdb"))
    sheet = next(s for s in cdb["sheets"] if s["name"] == "item")
    out = {}
    for ln in sheet["lines"]:
        t = ln.get("type") or ""
        if not isinstance(ln.get("id"), str) or not t.startswith("Augment"):
            continue
        out[ln["id"]] = {
            "t": t,
            "a": [[(a.get("target") or {}).get("attribute"), a.get("val")]
                  for a in ln.get("affixes") or ()
                  if (a.get("target") or {}).get("attribute")
                  and a.get("val") is not None],
            "s": [s.get("skill") for s in ln.get("skills") or ()
                  if s.get("skill")]}
    return out


def extract_item_rarity(game_dir):
    """item id -> base rarity, from data.cdb (only a weapon has a per-copy
    rarity)."""
    import pak_extract
    raw = pak_extract.read_entry(Path(game_dir) / "res.light.pak", "data.cdb")
    if raw is None:
        raise RuntimeError("data.cdb not in res.light.pak")
    cdb = json.loads(raw)
    sheet = next(s for s in cdb["sheets"] if s["name"] == "item")
    return {ln["id"]: ln["rarity"] for ln in sheet["lines"]
            if isinstance(ln.get("id"), str)
            and isinstance(ln.get("rarity"), str) and ln["rarity"]}


# An achievement naming a boss the unit sheet spells differently.
BOSS_ALIASES = {"Splongeblob": "SpongeBlob"}


def extract_dungeons(game_dir):
    """Every dungeon, in the game's order: [{kind, boss, region, ...}]. The
    activity sheet ships empty, so the list comes from the "Defeat [Boss] in
    ::target::" achievements (target = activity id, category = region)."""
    import re
    import pak_extract
    raw = pak_extract.read_entry(Path(game_dir) / "res.light.pak", "data.cdb")
    if raw is None:
        raise RuntimeError("data.cdb not in res.light.pak")
    cdb = json.loads(raw)
    units = {ln["id"] for s in cdb["sheets"] if s["name"] == "unit"
             for ln in s["lines"] if isinstance(ln.get("id"), str)}
    lower = {u.lower(): u for u in units}
    out, seen = [], set()
    ach = next(s for s in cdb["sheets"] if s["name"] == "ach")
    unit_rows = {ln["id"]: ln for s in cdb["sheets"] if s["name"] == "unit"
                 for ln in s["lines"] if isinstance(ln.get("id"), str)}
    item_rows = {ln["id"]: ln for s in cdb["sheets"] if s["name"] == "item"
                 for ln in s["lines"] if isinstance(ln.get("id"), str)}
    tables = {ln["id"]: ln for s in cdb["sheets"] if s["name"] == "lootTable"
              for ln in s["lines"] if isinstance(ln.get("id"), str)}
    itypes = {ln["id"]: ln for s in cdb["sheets"] if s["name"] == "itemType"
              for ln in s["lines"] if isinstance(ln.get("id"), str)}
    for ln in ach["lines"]:
        desc = ln.get("desc") or ""
        m = re.match(r"Defeat \[(\w+)\].*::target::.*difficulty", desc)
        if not m:
            continue
        for o in ln.get("objectives") or ():
            if o.get("ref") != "ActivityCompleted":
                continue
            for t in o.get("targets") or ():
                ref = t.get("ref")
                if not (isinstance(ref, list) and len(ref) == 2
                        and isinstance(ref[1], str)) or ref[1] in seen:
                    continue
                boss = BOSS_ALIASES.get(m.group(1), m.group(1))
                boss = boss if boss in units else lower.get(boss.lower(), boss)
                cat = str(ln.get("category") or "")
                region = cat.split("_", 1)[1] if "_" in cat else ""
                seen.add(ref[1])
                out.append({"kind": ref[1], "boss": boss,
                            "region": f"{region}_Region" if region else "",
                            **dungeon_loot(unit_rows.get(boss) or {},
                                           item_rows, tables, itypes)})
    return out


# Armour slots: the dungeon's faction set, which no loot table lists.
APTITUDE_CLASS = {"Fighter": "warrior", "Wizard": "mage",
                  "Assassin": "rogue", "Cleric": "priest"}


def dungeon_loot(boss, item_rows, tables, itypes=None):
    """What the end of a dungeon can give, from the game's own data.

    * The boss's table with the Weights flag is the reward chest: ONE item,
      by weight. The other rolls on its death, each line on its own chance
      (bossLootTable/lootTable are not always the same way round).
    * Every dungeon gives DungeonCrate (spark shards, quantity by level).
    * Faction armour (st.activity.DungeonContext.dropBossLoot, read
      2026-10-01): in Normal/Hard ONE sure piece, drawn evenly among the
      faction's Rare non-weapon gear the class can wear (the last 2 received
      left out); in Heroic the boss's heroicLootTable instead, if any.
      "pools": {mode: {class: pieces eligible}}.
    Each entry: {item, type, rarity, apt, src, chance (0..1 or None), qty,
    diff (conditions.difficulty.min)}."""
    def min_diff(ln):
        m = (((ln.get("conditions") or {}).get("difficulty") or {})
             .get("min"))
        return m[0] if isinstance(m, list) and m else None

    def entry(iid, src, chance, qty=None, diff=None):
        row = item_rows.get(iid) or {}
        return {"item": iid, "type": row.get("type") or "",
                "rarity": row.get("rarity") or "",
                "apt": [APTITUDE_CLASS[a["ref"]]
                        for a in row.get("aptitudes") or ()
                        if a.get("ref") in APTITUDE_CLASS],
                "src": src, "chance": chance, "qty": qty, "diff": diff}

    out = []
    props = boss.get("props") or {}
    for tid in (props.get("bossLootTable"), props.get("lootTable")):
        t = tables.get(tid) or {}
        lines = [ln for ln in t.get("loot") or () if ln.get("item")]
        if (t.get("flags") or 0) & 1:
            total = sum(float(ln.get("proba") or 0) for ln in lines) or 1.0
            out += [entry(ln["item"], "coffre",
                          float(ln.get("proba") or 0) / total,
                          diff=min_diff(ln)) for ln in lines]
        else:
            out += [entry(ln["item"], "boss", float(ln.get("proba") or 0),
                          diff=min_diff(ln))
                    for ln in lines]
    itypes = itypes or {}

    def is_weapon(t):
        seen = set()
        while t in itypes and t not in seen:
            seen.add(t)
            if t == "Weapon":
                return True
            t = itypes[t].get("inherit")
        return False

    def pool(ids):
        # a piece with no aptitude fits every class (HItem.hasUnitAptitude)
        return {c: sum(1 for i in ids
                       if not entry(i, "", None)["apt"]
                       or c in entry(i, "", None)["apt"])
                for c in APTITUDE_CLASS.values()}

    faction = boss.get("faction")
    pools = {}
    if faction:
        rare = [iid for iid, row in item_rows.items()
                if row.get("faction") == faction
                and row.get("rarity") == "Rare"
                and not is_weapon(row.get("type"))]
        out += [entry(iid, "faction", None) for iid in rare]
        pools["faction"] = pool(rare)
    heroic = (tables.get(props.get("heroicLootTable")) or {}).get("loot") or []
    epic = [ln["item"] for ln in heroic if ln.get("item")]
    if epic:
        out += [entry(iid, "heroic", None, diff=2) for iid in epic]
        pools["heroic"] = pool(epic)
    crate = [ln for ln in (tables.get("DungeonCrate") or {}).get("loot") or ()
             if ln.get("item")]
    for iid in dict.fromkeys(ln["item"] for ln in crate):
        out.append(entry(iid, "coffre", 1.0, [
            [ln.get("itemMin"), ln.get("itemMax"), ln.get("minLvl"),
             ln.get("maxLvl")] for ln in crate if ln["item"] == iid]))
    return {"loot": out, "pools": pools}


BOSS_PORTRAIT_PX = 192


def extract_boss_portraits(game_dir, bosses, out_dir):
    """Each dungeon boss's portrait, out_dir/<unit id>.png, for the dungeon
    list. The unit's row in data.cdb names it: gfx {file, size, x, y} in
    res.pak (UI/Portraits/Units/...)."""
    import io
    import struct
    import pak_extract
    from PIL import Image

    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    gfx = {ln["id"]: ln.get("gfx") for s in cdb["sheets"]
           if s["name"] == "unit" for ln in s["lines"]
           if isinstance(ln.get("id"), str)}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pak = game_dir / "res.pak"
    done = 0
    with open(pak, "rb") as f:
        header_size = struct.unpack_from("<i", f.read(12), 4)[0]
        f.seek(0)
        entries, data_off = pak_extract.read_tree(f.read(header_size),
                                                  pak.name)
        by_path = {e.path: e for e in entries}
        for boss in bosses:
            g = gfx.get(boss)
            e = by_path.get(g.get("file")) if isinstance(g, dict) else None
            if e is None:
                continue
            f.seek(data_off + e.pos)
            img = Image.open(io.BytesIO(f.read(e.size))).convert("RGBA")
            n = int(g.get("size") or img.width)
            x, y = int(g.get("x") or 0) * n, int(g.get("y") or 0) * n
            img = img.crop((x, y, x + n, y + n))
            img = img.resize((BOSS_PORTRAIT_PX, BOSS_PORTRAIT_PX),
                             Image.LANCZOS)
            img.save(out_dir / f"{boss}.png", optimize=True)
            done += 1
    return done


ITEM_ICON_PX = 48


def extract_item_icons(game_dir, out_dir):
    """Every item's icon, out_dir/<item id>.png: the item row's gfx {file,
    size, x, y} is a tile of `file` in res.pak. res.pak is close to a
    gigabyte, so only its directory and the files used are read."""
    import io
    import struct
    import pak_extract
    from PIL import Image

    game_dir = Path(game_dir)
    raw = pak_extract.read_entry(game_dir / "res.light.pak", "data.cdb")
    if raw is None:
        raise RuntimeError("data.cdb not in res.light.pak")
    cdb = json.loads(raw)
    sheet = next(s for s in cdb["sheets"] if s["name"] == "item")
    wanted = {}
    for ln in sheet["lines"]:
        g = ln.get("gfx")
        if isinstance(ln.get("id"), str) and isinstance(g, dict) \
                and g.get("file") and g.get("size"):
            wanted[ln["id"]] = g

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pak = game_dir / "res.pak"
    done = 0
    with open(pak, "rb") as f:
        header_size = struct.unpack_from("<i", f.read(12), 4)[0]
        f.seek(0)
        entries, data_off = pak_extract.read_tree(f.read(header_size),
                                                  pak.name)
        by_path = {e.path: e for e in entries}
        sheets = {}
        for iid, g in wanted.items():
            path = g["file"]
            if path not in sheets:
                e = by_path.get(path)
                img = None
                if e is not None:
                    f.seek(data_off + e.pos)
                    try:
                        img = Image.open(io.BytesIO(f.read(e.size)))
                        img = img.convert("RGBA")
                    except Exception:
                        img = None
                sheets[path] = img
            img = sheets[path]
            if img is None:
                continue
            n = int(g["size"])
            x, y = int(g.get("x") or 0) * n, int(g.get("y") or 0) * n
            if x + n > img.width or y + n > img.height:
                continue
            tile = img.crop((x, y, x + n, y + n))
            tile = tile.resize((ITEM_ICON_PX, ITEM_ICON_PX), Image.LANCZOS)
            tile.save(out_dir / f"{iid}.png", optimize=True)
            done += 1
    return done


# Which cdb threshold constant each codex bucket uses (measured 2026-08-05).
CODEX_SETS = {
    "elite": "EliteAndBossProgressThresholds",
    "big": "BigFoeProgressLevelThreshold",
    "foe": "FoeProgressLevelThreshold",
    "item": "ItemProgressLevelThreshold",
}


def extract_codex_units(game_dir):
    """Each unit's codex bucket, from data.cdb, plus the thresholds:
      * `NoCodex` — no codex entry (bit read off the flags column);
      * elite/boss — `flags & (Elite|Boss)`;
      * "big" — inherits a `*_Big` base (not scale, not a flag: what
        Progress.unitInheritFrom tests).
    Any unit not listed is an ordinary foe.
    """
    import pak_extract
    data, entries, data_off = pak_extract.load(Path(game_dir) / "res.light.pak")
    e = next(x for x in entries if x.path.endswith("data.cdb"))
    cdb = json.loads(data[data_off + e.pos: data_off + e.pos + e.size])
    sheets = {s["name"]: s for s in cdb["sheets"]}
    rows = {ln["id"]: ln for ln in sheets["unit"]["lines"]
            if isinstance(ln.get("id"), str)}

    bits = {}
    for c in sheets["unit"]["columns"]:
        if c["name"] == "flags" and isinstance(c.get("typeStr"), str):
            names = c["typeStr"].split(":", 1)[-1].split(",")
            bits = {n: i for i, n in enumerate(names)}
    for need in ("NoCodex", "Elite", "Boss"):
        if need not in bits:
            raise ValueError(f"unit flags column has no {need} bit")

    thresholds = {}
    for ln in sheets["constant"]["lines"]:
        v = ln.get("v") or {}
        if isinstance(v, dict) and isinstance(v.get("floats"), list):
            thresholds[ln.get("id")] = [f.get("v") for f in v["floats"]]
    out_thr = {}
    for bucket, const in CODEX_SETS.items():
        vals = thresholds.get(const)
        if not vals:
            raise ValueError(f"cdb constant {const} missing")
        out_thr[bucket] = [int(x) for x in vals]

    def inherit_refs(uid, seen=None):
        """The full inherit closure — a base can itself inherit."""
        seen = set() if seen is None else seen
        acc = set()
        for h in (rows.get(uid, {}).get("inherit") or []):
            ref = h.get("ref")
            if ref and ref not in seen:
                seen.add(ref)
                acc.add(ref)
                acc |= inherit_refs(ref, seen)
        return acc

    no_codex, elite, big = [], [], []
    for uid, r in rows.items():
        fl = r.get("flags")
        fl = fl if isinstance(fl, int) else 0
        if (fl >> bits["NoCodex"]) & 1:
            no_codex.append(uid)
            continue
        if ((fl >> bits["Elite"]) & 1) or ((fl >> bits["Boss"]) & 1):
            elite.append(uid)
        elif any("Big" in ref for ref in inherit_refs(uid)):
            big.append(uid)
    return {"thresholds": out_thr, "noCodex": sorted(no_codex),
            "elite": sorted(elite), "big": sorted(big)}


def extract_unit_traits(game_dir):
    """{"spark": [unit ids]}: the "Sparkling ..." variants (`Spark` flag)."""
    import pak_extract
    data, entries, data_off = pak_extract.load(Path(game_dir) / "res.light.pak")
    e = next(x for x in entries if x.path.endswith("data.cdb"))
    cdb = json.loads(data[data_off + e.pos: data_off + e.pos + e.size])
    sheets = {s["name"]: s for s in cdb["sheets"]}

    bits = {}
    for c in sheets["unit"]["columns"]:
        if c["name"] == "flags" and isinstance(c.get("typeStr"), str):
            names = c["typeStr"].split(":", 1)[-1].split(",")
            bits = {n: i for i, n in enumerate(names)}
    if "Spark" not in bits:
        raise ValueError("unit flags column has no Spark bit")
    spark_bit = bits["Spark"]

    spark = []
    for ln in sheets["unit"]["lines"]:
        uid = ln.get("id")
        fl = ln.get("flags")
        if isinstance(uid, str) and isinstance(fl, int) and (fl >> spark_bit) & 1:
            spark.append(uid)
    return {"spark": sorted(spark)}


if __name__ == "__main__":
    main()
