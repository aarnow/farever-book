"""
emit_offsets.py — compute every runtime field offset the meter needs and write
analysis_out/meter_offsets.json. Re-run after a Farever patch.

Offsets come from hlbc_parser.field_offsets() (mirrors hl_get_obj_rt) and the
HL virtual vfield indices for the skill-name chain.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode, HOBJ, HSTRUCT
from gamepath import find_hlboot

# Overridable for the same reason as build_targets.py's: the installed meter
# runs this from a bundle directory that doesn't survive the process.
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
    """Where the hook finds each field it reads: offsets in the game's
    objects, read off its bytecode, so a patch that moves a field moves
    them too."""
    byname = {t.name: t for t in code.types
              if t.kind in (HOBJ, HSTRUCT) and t.name}

    def offs(name):
        f = code.field_offsets(byname[name].index)
        # Fields a patch renamed keep the name the hook reads them by.
        for (cls, old), new in FIELD_RENAMES.items():
            if cls == name and old not in f and new in f:
                f[old] = f[new]
        return f

    def descendants(root):
        """Every class that inherits from `root`, itself included.

        Needed because a summon's runtime class is not always the literal
        `ent.Foe` — the hook has to recognise the whole family before it dares
        read `summonOwner` off a dealer, since that offset means something
        entirely different on a class that hasn't got the field."""
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
    state = offs("st.State")
    activity = offs("st.Activity")
    arrobj = offs("hl.types.ArrayObj")
    foe = offs("ent.Foe")
    unit = offs("ent.Unit")
    status = offs("st.skill.Status")
    elem = offs("ent.Element")
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
    # The codex counter proxy. hxbit generates one class per record shape and
    # NAMES IT after the shape, which is how the layout was confirmed rather
    # than guessed — ObjProxy_OkillCount_Int_rank_Int really does carry
    # killCount then rank. Resolved by name so a patch that adds a field to the
    # record (and so renames the class) fails loudly here instead of reading a
    # stale offset.
    kproxy = offs("hxbit.ObjProxy_OkillCount_Int_rank_Int")
    eproxy = offs("hxbit.ObjProxy_Ocompleted_Float")
    spec = offs("st.player.HeroSpecialization")
    gear = offs("st.item.Gear")
    weapon_ = offs("st.item.Weapon")
    skill = offs("st.skill.Skill")
    # Dungeons. GameLayer.mainActivity is the running activity; for a dungeon
    # it is an st.activity.Dungeon, whose globalCtx is the DungeonContext that
    # carries the run's state, clock and death count. The difficulty lives on
    # the group's instance lobby, not on the run.
    dctx = offs("st.activity.DungeonContext")
    dact = offs("st.activity.Dungeon")
    lobby = offs("st.player.InstanceLobby")

    # st.Equipment extends st.Inventory, so one `content` offset serves both
    # containers. Verified rather than assumed — if the two ever diverge, the
    # equipment sweep would read a wrong offset and report nonsense items.
    if equip["content"][0] != inv["content"][0]:
        raise SystemExit(
            f"[!] st.Equipment.content@{equip['content'][0]} != "
            f"st.Inventory.content@{inv['content'][0]} — the containers no "
            "longer share a layout; fix the inventory sweep before shipping.")

    meta = {
        "String": {"bytes": string["bytes"][0], "length": string["length"][0]},
        # `blocker` and `effect` are read for the nullified-hit diagnostic: the
        # meter counts a hit's _amount whether or not the target actually took
        # it, so damage against a boss in an immunity phase inflates the parse.
        # Which of _block / blocker / effect marks that is not settled yet:
        # the hook reports them from normal play.
        "DamageResult": {k: dr[k][0] for k in
            ["_amount", "affinity", "_critical", "_kill", "_block",
             "blocker", "effect", "baseSkill"]},
        # dynVal1-3 are how MOST player heal skills carry their amount: the
        # cdb's skill@steps@effects rows name `dynVal` rather than a baseVal or
        # a scaling ratio, and these three f64s are hxbit-replicated, so the
        # number the server computed is sitting here on the client. That is the
        # only reason healing can be counted at all — nothing else on a client
        # knows what a heal was worth.
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
        # Hero.layer is st.State.layer, inherited — it points at the GameLayer
        # the hero is in, which is how the hook reaches the rift flag without
        # calling anything (a plain pointer walk is safe off the game thread).
        "Hero": {"name": hero["name"][0], "player": hero["player"][0],
                 "isInCombat": hero["isInCombat"][0],
                 "layer": hero["layer"][0],
                 # the hero's containers (pickups, the goals' counts)
                 "loadout": hero["loadout"][0],
                 # The class ("Warrior"/"Priest"/"Rogue"/"Mage") and level.
                 # st.player.HeroData would be the tidier home for both, but
                 # it is null on the client for every player including
                 # yourself: the entity is the only source.
                 "kind": hero["kind"][0], "level": hero["_level"][0]},
        "Loadout": {"inventory": loadout["inventory"][0],
                    "equipment": loadout["equipment"][0],
                    # the bank's tabs (hxbit.ArrayProxyData), for the goals'
                    # "owned" count
                    "banks": loadout["banks"][0]},
        # content is an ArrayObj of SLOT VIRTUALS, not of items: each entry is
        # a standalone hl vvirtual carrying inline {count:Int, item:st.Item}.
        # The hook reads the `item` field by name out of the virtual's own
        # field table — see readSlot() in meter_hook.js. Decoding entries as
        # st.Item directly does not throw, it just yields garbage.
        "Inventory": {"content": inv["content"][0]},
        # __uid is NOT stable identity — it is reassigned on every container
        # move, so a uid-diff alone reports a re-equip as a fresh pickup. It is
        # emitted because it is still the only per-slot discriminator; the
        # pickup rule guards on `kind` as well.
        "Item": {"kind": item["kind"][0], "uid": item["__uid"][0],
                 # the copy's flags (st.ItemFlag: Flawless, Prismatic), an
                 # hxbit.EnumFlagsData whose `value` holds the bits
                 "flags": item["flags"][0]},
        "EnumFlagsData": {"value": offs("hxbit.EnumFlagsData")["value"][0]},
        # st.ItemFlag constructor -> bit, by name
        "ItemFlag": next({(c[0] if isinstance(c, tuple) else c.name): i
                          for i, c in enumerate(t.constructs)}
                         for t in code.types if t.name == "st.ItemFlag"),
        # `rarity` is declared ONLY on st.item.Weapon (hierarchy:
        # st.Item -> st.item.Gear -> st.item.Armor / st.item.Weapon). Reading
        # it at any other class is past the end of the object. Live values are
        # capitalised: Legendary, Epic, Rare.
        "Weapon": {"rarity": weapon["rarity"][0], "level": weapon["level"][0]},
        "GameLayer": {"isRift": layer["isRift"][0],
                      # Which shard you are on. hxbit-replicated (the class
                      # carries __net_mark_serverName), so this is the SERVER's
                      # own name for itself pushed to the client — measured
                      # 2026-08-05 as "Sfojuxa3386_6601_na": a generated server
                      # name, an instance number, and the region code. Distinct
                      # from the zone, which is layer.world.level below.
                      "serverName": layer["serverName"][0],
                      "mainActivity": layer["mainActivity"][0],
                      "time": layer["_time"][0],
                      # world -> world.World, whose `level` string is the
                      # honest zone/world identity. Main.getMapId() — the old
                      # zone signal — turned out to return the MACHINE NAME.
                      "world": layer["world"][0],
                      # the whole shard's roster: every player the client
                      # holds, not only those streamed in around you
                      "players": layer["players"][0]},
        # The loaded level's identity, for the zone signal and the map
        # backdrop. `level` is the primary; name/branchName/_isWorldMap ship
        # so the hook can report what they actually hold from normal play —
        # field names lie in this game until measured.
        "World": {"level": world["level"][0],
                  "name": world["name"][0],
                  "branchName": world["branchName"][0],
                  "_isWorldMap": world["_isWorldMap"][0]},
        # Despawned-but-still-listed entries. Filtered out of the sweep.
        "State": {"removed": state["removed"][0]},
        "Activity": {"kind": activity["kind"][0],
                     "globalCtx": activity["globalCtx"][0],
                     "contexts": activity["contexts"][0]},
        "Dungeon": {"bossId": dact["bossId"][0]},
        "DungeonCtx": {"dungeonState": dctx["dungeonState"][0],
                       "lastStateChanged": dctx["lastStateChanged"][0],
                       "startActivity": dctx["startActivity"][0],
                       "endActivity": dctx["endActivity"][0],
                       "nbPlayerDeaths": dctx["nbPlayerDeaths"][0],
                       "step": dctx["step"][0]},
        "InstanceLobby": {"activityId": lobby["activityId"][0],
                          "difficulty": lobby["difficulty"][0]},
        # every placed world object (chest, orb, obelisk) is an ent.Element,
        # `kind` its id ("Z1_World_Greenlands_WorldChest_60")
        "Element": {"kind": elem["kind"][0]},
        # A foe with a summonOwner is somebody's pet, not a mob. That's the
        # only reliable way to tell them apart — they're the same class.
        # `kind` is the internal id ("Crimson_Z2W_Sword"); `inf` is the CDB row
        # it came from, whose texts.name is the display name on the nameplate.
        "Unit": {"kind": unit["kind"][0], "inf": unit["inf"][0],
                 # attr.health is what the boss bar is actually reading. NOTE:
                 # UnitAttributes.maxHealth reads 0 for the whole of a boss
                 # fight (measured), so health is only good for "did it die",
                 # never for a percentage.
                 "attr": unit["attr"][0],
                 # an hxbit proxy array of st.skill.Status (plain pointer
                 # reads, safe from a timer)
                 "statuses": unit["statuses"][0]},
        # One live buff/debuff, resolved off the Status subclass. Measured:
        # stopTime is -1 on every status (not a clock): it ends at startTime
        # + duration, and duration grows on every refresh.
        "Status": {"kind": status["kind"][0],
                   "startTime": status["startTime"][0],
                   "stopTime": status["stopTime"][0],
                   "duration": status["duration"][0],
                   "removed": status["removed"][0]},
        "Foe": {"summonOwner": foe["summonOwner"][0]},
        # hl.types.ArrayObj: length, then a pointer to an hl_varray whose
        # ELEMENTS START AT +24, past its (t, at, size, pad) header. Reading
        # from +0 yields the header as your first entity and faults instantly.
        "ArrayObj": {"length": arrobj["length"][0], "array": arrobj["array"][0],
                     "data": 24},
        # the server's clock (statuses' times are on it)
        "TimeState": {"serverNow": tstate["serverNow"][0]},
        # `uid` is the player's STEAM ACCOUNT ID, not an internal handle:
        # "S" + the id's bytes as hex in LITTLE-ENDIAN order, trailing zero
        # bytes trimmed (measured 2026-08-02 against steam_get_steam_id() and
        # the registry's ActiveUser: read big-endian, it is a wrong,
        # plausible-looking number).
        # `hero` is the player's live ent.Hero: populated for 24/24 players on
        # the layer when measured, so every player has a class, not only those
        # nearby.
        "Player": {"name": player["name"][0], "group": player["group"][0],
                   "isMe": player["isMe"][0], "lobbyId": player["lobbyId"][0],
                   "uid": player["uid"][0], "hero": player["hero"][0],
                   # ...and the per-character codex/collection store.
                   "progress": player["progress"][0],
                   # The ACCOUNT-wide store — collected companions, mounts,
                   # gliders. Distinct from `progress`, which is per character.
                   "accountProgress": player["accountProgress"][0],
                   # The activity contexts the server replicates to this
                   # player — where a dungeon's DungeonContext lives on the
                   # client (Activity.globalCtx reads null there).
                   "activityCtx": player["activityCtx"][0]},
        # Collected critters (companions), measured 2026-08-07:
        # Collection.pets is an hxbit proxy array of plain UNIT KINDS
        # ("Turtle_Grey", "Frog_Demon"), the same string as ent.Unit.kind (the
        # game's own "already caught?" check, Collection.hasPet(kind), takes
        # exactly these). NOT item ids.
        "AccountProgress": {"collection": acct["collection"][0],
                            "achievements": acct["achievements"][0]},
        # mounts / gliders: the same proxy arrays, of item kinds.
        "Collection": {"pets": coll["pets"][0], "mounts": coll["mounts"][0],
                       "gliders": coll["gliders"][0],
                       "gears": coll["gears"][0]},
        # hxbit wraps a replicated array in a proxy: Group.players is an
        # ArrayProxyData whose ArrayDyn wraps an ArrayObj. Two hops, and the
        # party roster is the reason they are here.
        "ArrayProxyData": {"array": aproxy["array"][0]},
        "ArrayDyn": {"array": adyn["array"][0]},
        "Group": {"players": group["players"][0],
                  "instanceLobbies": group["instanceLobbies"][0]},
        # The codex (hunting log), measured 2026-08-05. The
        # whole thing is replicated to the client and reachable by plain
        # pointer reads from the hero:
        #   Hero.player -> Player.progress -> Progress.unitsProgress
        #   -> MapData.map (a virtual; hl_vvirtual.value @8 is the real
        #      StringMap) -> StringMap.h -> $std.hbget(h, utf16(unitKind))
        #   -> ObjProxy { killCount, rank }
        # `rank` is how many thresholds the count has passed, and every UNIT
        # threshold set has three tiers, so rank==3 means the entry is done and
        # nothing has to know WHICH set applies.
        "Progress": {"unitsProgress": progress["unitsProgress"][0],
                     "itemProgress": progress["itemProgress"][0],
                     # element id -> ProgressState (discovered, completed):
                     # the world's chests, orbs, obelisks... per character
                     "elements": progress["elements"][0],
                     # counter id -> value (a plain StringMap): the loot
                     # luck counters (counter sheet, Luck_*) among them
                     "counters": progress["counters"][0],
                     # achievement id -> state, per character
                     "achievements": progress["achievements"][0]},
        "MapData": {"map": mapdata["map"][0], "value": 8},
        # Progress.elements' value (measured 2026-09-28): `completed` is when
        # the element was completed — a chest opened, an orb picked up, an
        # obelisk discovered. An element never completed has no entry.
        "ElementProxy": {"completed": eproxy["completed"][0]},
        # Any player's profile (the Character tab), measured 2026-09-28:
        # the client holds every hero's equipment, talents and skills.
        "HeroDetail": {k: hero[k][0] for k in
                       ("skills", "specialization", "skillSlots",
                        "weaponSkills", "secondarySkill")},
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
        # A gear's upgrades and what is set on it (a profile's equipment):
        # upgradeLevel (the stars), slots (augments: the "corrupted gifts"),
        # and a weapon's effects (its enchantment formula).
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
        # The game's boss/elite healthbar. `bossInfos` is NOT a fixed pool —
        # measured lengths were only ever 0 (no bar) or 1 (bar up), so its
        # length alone says whether a bar is on screen. Each entry's `active`
        # is the per-slot gate; UIElement.visible tracks it but diverged on a
        # couple of samples at transitions, so `active` is the one to read.
        "BossesInfo": {"bossInfos": bosses["bossInfos"][0]},
        "BossInfo": {"active": bossinfo["active"][0],
                     "unit": bossinfo["unit"][0]},
        # Which runtime classes are foes — the set a dealer must belong to
        # before `Foe.summonOwner` may be read off it. Measured 2026-07-30:
        # only 7 classes descend from ent.Foe, so this is a small closed set,
        # and a summon's class is not reliably the literal "ent.Foe".
        "foeClasses": descendants("ent.Foe"),
    }
    return meta


# ---- the tables: what the app shows, read off the game's files ------------
class Table:
    """One output of the data folder: a JSON file built from the game, or a
    folder (or file) of pictures its builder writes itself (dump None).
    `summary` says what was written, after "[written] <path>" (the app's
    progress reads those lines). A table that fails is reported and
    skipped, the others are still written."""

    def __init__(self, out, build, summary=None, dump=None, label=None):
        self.out, self.build, self.summary = out, build, summary
        self.dump, self.label = dump, label or out


# how each JSON is written (kept as each table always was)
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


def _n(key):
    return lambda d: f"{len(d[key])}"


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
    Table("ui_logo.png",
          lambda g, o, t: extract_title_logo(g, o / "ui_logo.png"),
          label="title logo"),
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


# skill@steps@effects.effect is an enum; "5:Damage,Heal,Shield,GainAtb,Status"
# makes Heal index 1. Read off the column's own typeStr rather than hardcoded,
# because a patch that inserts an effect kind would silently re-point it.
HEAL_EFFECT_NAME = "Heal"


def extract_heal_specs(game_dir):
    """skill id -> {step index: how that step's heal amount is computed}.

    This is what lets a client know what a heal was WORTH. Measured
    2026-08-03: no heal amount is ever sent to a client (fifteen entry points
    hooked, only playHitHealFX fires and its HitData.amount reads 0), so the
    only way to count a heal that restored nothing is to compute it the way the
    game does. The cdb says how, per skill:

      dyn  -> the amount is in BaseSkill.dynVal1/2/3, which ARE replicated
      scale-> ratio x one of the caster's attributes (Faith on most of them)
      base -> a flat number

    Shapes seen in this build (44 heal skills): dyn only (12), scale only (27),
    base+dyn (4), base+scale (2), base only (2). Where both a base and a dyn
    are given the dyn is the real value and the base is its floor, so the host
    prefers dyn when it is non-zero.
    """
    import pak_extract
    data, entries, data_off = pak_extract.load(Path(game_dir) / "res.light.pak")
    e = next(x for x in entries if x.path.endswith("data.cdb"))
    cdb = json.loads(data[data_off + e.pos: data_off + e.pos + e.size])
    sheets = {sh["name"]: sh for sh in cdb["sheets"]}

    # Which enum index means Heal, from the column definition itself.
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


# The sheets whose French names the app shows: dungeons (activity), loot
# (item, rarity) and bosses (unit).
FR_SHEETS = ("ach", "activity", "attribute", "faction", "gatherable",
             "item", "itemType", "job", "rarity", "skill", "unit",
             "unitType", "zone")
# Sheets whose French descriptions the app shows (the collection's details,
# a monster's page).
FR_DESC = {"ach": ("desc",), "item": ("texts.flavorDesc", "texts.desc"),
           "unit": ("texts.desc",), "skill": ("texts.desc",)}


def extract_fr_names(game_dir):
    """sheet -> id -> French display name, from the game's own translation
    (res.pak lang/export_fr.xml: <sheet name=...><Id><texts.name>...). The
    same text the game shows when it runs in French — e.g. the activity
    R1_POI_CleodorasNest is "Tronc-ruche d'Élizabeille"."""
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
    """What a rift gives, as the game's code hands it out (read from
    hlboot.dat with hltools/hlbc_code.py, 2026-10-01 — st.activity.
    RiftContext.dropBossActivityLoot / onElementStateComplete / isTier):

    * the boss chest opens at Rift_RewardTiers[0] gates closed; opened, it
      gives every player the boss's lootTable (one of its two weapons, by
      weight) and bossLootTable, both at min. rarity Rare, then
      Rift_Bosschest, plus Rift_Tier4 from tier 3 (10 gates) and Rift_Tier6
      from tier 5 (15 gates);
    * the other chests (tiers 1, 2, 4: 5, 9, 14 gates) give every player
      Rift_BonusChest;
    * a weapon's rarity is drawn by ent.Hero.makeLootItem from the rarity
      sheet's generationChance at the player's level, Rare at least: the
      Legendary share first, through the Luck_LegendaryWeapon counter."""
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
                                         "itemMin", "itemMax")}
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
    rarities = {r["id"]: (r.get("props") or {}).get("generationChance")
                for r in sheets["rarity"]["lines"]
                if isinstance(r.get("id"), str)}
    return {"tiers": tiers, "bosses": bosses,
            "bossChest": lines("Rift_Bosschest"),
            "bonusChest": lines("Rift_BonusChest"),
            "tier4": lines("Rift_Tier4"), "tier6": lines("Rift_Tier6"),
            "soulstone": lines("Soulstone"),
            "rarities": rarities}


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
    """What each augment does — the items set into a gear's slots: corrupted
    gifts, formulas, sigils, gems, plates, embroideries. {id: {t: type,
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
    """item id -> the item's base rarity, from data.cdb. Only a weapon
    carries its own rarity per copy (st.item.Weapon.rarity); every other item
    — armour, materials — is the rarity its sheet row says."""
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
    """Every dungeon in the game, in the game's order: [{kind, boss,
    region}]. The activity sheet ships empty in data.cdb, so the list comes
    from the achievements — one per dungeon, "Defeat [Boss] in ::target::
    on Normal difficulty…", whose target is the dungeon's activity id and
    whose category (Combat_Z1…) names its region (Z1_Region…)."""
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

    * The reward chest (Gameplay/Elements/Activities/BossChest.prefab, placed
      in each dungeon with lootTable = the boss) rolls the boss's table, which
      has the Weights flag: ONE item, chosen by weight.
    * The boss's other table rolls on its death, each line on its own chance.
      The unit's `bossLootTable`/`lootTable` props point at the two, but not
      always the same way round — the Weights flag says which is which.
    * Every dungeon activity (Gameplay/Activities/Base.prefab) gives
      DungeonCrate: spark shards, the quantity by level.
    * The faction's armour, by the game's code (read from hlboot.dat with
      hltools/hlbc_code.py, 2026-10-01 — st.activity.DungeonContext.
      dropBossLoot): in Normal / Hard every player gets ONE piece for sure
      (dropFactionLoot, chance 1.0), drawn evenly among the faction's Rare
      non-weapon gear his class can wear (HItem.getFactionLootTable, flags
      WithAffinity + BLP_LootLog: the last 2 pieces received are left out);
      in Heroic that is replaced by the boss's heroicLootTable (Epic pieces,
      one drawn among the class's), and a boss without one gives no armour.
      "pools": {mode: {class: pieces eligible}}.
    A line can require a difficulty (conditions.difficulty.min: since the
    2026-09-30 patch the bosses' infusion pattern, Heroic only): `diff`.
    Each entry: {item, type, rarity, apt, src, chance (0..1 or None), qty,
    diff}."""
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


def extract_title_logo(game_dir, out, height=96):
    """The game's "FAREVER" wordmark (res.pak UI/Window/TitleScreen/
    title.png), cut to its letters and scaled to `height` pixels, for the
    window's header. Out of the player's own game files like every other
    picture: the game's art is never shipped with the app."""
    import io
    import pak_extract
    from PIL import Image
    raw = pak_extract.read_entry(Path(game_dir) / "res.pak",
                                 "UI/Window/TitleScreen/title.png")
    if not raw:
        raise FileNotFoundError("UI/Window/TitleScreen/title.png")
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    img = img.crop(img.getchannel("A").getbbox())
    w = round(img.width * height / img.height)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.resize((w, height), Image.LANCZOS).save(out, optimize=True)


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
    """Every item's icon as a small PNG, out_dir/<item id>.png — for the
    dungeon loot list. data.cdb's item row names it: gfx {file, size, x, y},
    a `size`-pixel tile at column x, row y of `file` in res.pak. res.pak is
    close to a gigabyte, so only its directory and the files used are read."""
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


# Which rank-threshold set a unit's codex entry uses. Measured 2026-08-05 on a
# fresh character, 11/11 samples agreeing.
# The bucket names are ours; the numbers are the cdb's own constants.
CODEX_SETS = {
    "elite": "EliteAndBossProgressThresholds",
    "big": "BigFoeProgressLevelThreshold",
    "foe": "FoeProgressLevelThreshold",
    "item": "ItemProgressLevelThreshold",
}


def extract_codex_units(game_dir):
    """What the meter needs to say "12/20" and "still missing" per mob.

    Three facts per unit, all from data.cdb so nothing has to be asked of the
    running game:

      * `NoCodex` — the game's own "this mob has no codex entry" flag. Its BIT
        INDEX is read off the flags column definition rather than hardcoded to
        18, because a patch inserting a flag above it would otherwise silently
        re-point it at NeutralAggro.
      * elite/boss — `flags & (Elite|Boss)`, which is a 1-kill entry.
      * "big" — the `inherit` column referencing a `*_Big` base
        (`W_Base_Big`, `D_Base_Big`). NOT model scale and NOT a flag: measured,
        `OgreManfish_Z1W_Claws` and `Manfish_Z1W_Claws` share a type and a
        faction, the big one carries no `scale` at all, and only `inherit`
        separates them. It is what `Progress.unitInheritFrom` tests.

    Emitted as three id lists plus the thresholds — anything not listed is an
    ordinary foe, which keeps the file to about 130 ids instead of 500 rows.
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
            continue                       # no entry at all; bucket is moot
        if ((fl >> bits["Elite"]) & 1) or ((fl >> bits["Boss"]) & 1):
            elite.append(uid)
        elif any("Big" in ref for ref in inherit_refs(uid)):
            big.append(uid)
    return {"thresholds": out_thr, "noCodex": sorted(no_codex),
            "elite": sorted(elite), "big": sorted(big)}


def extract_unit_traits(game_dir):
    """{"spark": [unit ids]}: the units carrying the `Spark` flag (its bit
    read off the column definition), the rare "Sparkling ..." variants."""
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
