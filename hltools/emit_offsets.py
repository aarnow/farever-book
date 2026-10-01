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
from hlbc_parser import HLCode, HOBJ, HSTRUCT, HVIRTUAL
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
    code = HLCode(hlboot).parse()
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
    wevents = offs("st.event.WorldEvents")
    wevent = offs("st.event.WorldEvent")
    player = offs("st.Player")
    group = offs("st.Group")
    base = offs("st.skill.BaseSkill")
    step = offs("st.skill.SkillStep")
    string = offs("String")
    entity = offs("ent.Entity")
    state = offs("st.State")
    inter = offs("ent.Interactible")
    activity = offs("st.Activity")
    arrobj = offs("hl.types.ArrayObj")
    foe = offs("ent.Foe")
    unit = offs("ent.Unit")
    status = offs("st.skill.Status")
    elem = offs("ent.Element")
    cam = offs("client.BaseCamera")
    bosses = offs("ui.hud.BossesInfo")
    bossinfo = offs("ui.hud.BossInfo")
    loadout = offs("st.Loadout")
    inv = offs("st.Inventory")
    equip = offs("st.Equipment")
    item = offs("st.Item")
    weapon = offs("st.item.Weapon")
    world = offs("world.World")
    gath = offs("ent.interactible.Gatherable")
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

    # skill display-name chain: BaseSkill.inf (a virtual) -> texts -> name.
    # The virtual is found through the field's own type: its index moves with
    # every patch (#963 before the 2026-09-30 one).
    inf_ti = next(f.type_index
                  for t in code._super_chain(byname["st.skill.BaseSkill"].index)
                  for f in getattr(t, "fields", ()) if f.name == "inf")
    row = code.types[inf_ti]
    texts_ti = next(f.type_index for f in row.vfields if f.name == "texts")
    texts = code.types[texts_ti]
    vidx = lambda vt, nm: next(i for i, f in enumerate(vt.vfields) if f.name == nm)

    meta = {
        "String": {"bytes": string["bytes"][0], "length": string["length"][0]},
        # `blocker` and `effect` are read for the nullified-hit diagnostic: the
        # meter counts a hit's _amount whether or not the target actually took
        # it, so damage against a boss in an immunity phase inflates the parse.
        # Which of _block / blocker / effect marks that is not settled yet —
        # these ship so the hook can report them from normal play instead of
        # needing a probe session timed to an immune phase.
        "DamageResult": {k: dr[k][0] for k in
            ["_amount", "affinity", "_critical", "_kill", "_hitCount",
             "_block", "blocker", "effect", "target", "serverSource", "ctx",
             "baseSkill"]},
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
                 # Legendary-pickup cue: the hero's containers, plus the
                 # equipped weapon (which is the same st.item.Weapon pointer
                 # the equipment slot holds — measured).
                 "loadout": hero["loadout"][0],
                 "weaponInHand": hero["weaponInHand"][0],
                 # The class ("Warrior"/"Priest"/"Rogue"/"Mage") and level, for
                 # the Social tab's roster. st.player.HeroData would be the
                 # tidier home for both, but it is null on the client for every
                 # player including yourself — the entity is the only source.
                 "kind": hero["kind"][0], "level": hero["_level"][0]},
        "Loadout": {"inventory": loadout["inventory"][0],
                    "equipment": loadout["equipment"][0]},
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
        "Item": {"kind": item["kind"][0], "uid": item["__uid"][0]},
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
                      "worldEvents": layer["worldEvents"][0],
                      "time": layer["_time"][0],
                      # world -> world.World, whose `level` string is the
                      # honest zone/world identity. Main.getMapId() — the old
                      # zone signal — turned out to return the MACHINE NAME.
                      "world": layer["world"][0],
                      # Minimap: the layer keeps these lists built already, so
                      # the sweep is a walk of three arrays rather than a
                      # search. units = heroes + foes, interactibles = chests /
                      # orbs / obelisks / respawn points, entities = the widest
                      # net and the only place activities show up.
                      "units": layer["units"][0],
                      "interactibles": layer["interactibles"][0],
                      "entities": layer["entities"][0],
                      # The whole-shard roster — every player the client has
                      # state for, not merely the ones streamed in around you.
                      # This is what the Social tab lists; `units` would only
                      # ever show your neighbours.
                      "players": layer["players"][0]},
        # The loaded level's identity, for the zone signal and the map
        # backdrop. `level` is the primary; name/branchName/_isWorldMap ship
        # so the hook can report what they actually hold from normal play —
        # field names lie in this game until measured.
        "World": {"level": world["level"][0],
                  "name": world["name"][0],
                  "branchName": world["branchName"][0],
                  "_isWorldMap": world["_isWorldMap"][0]},
        # Every drawable thing descends from ent.Entity, so one set of position
        # offsets serves heroes, foes, interactibles and activities alike.
        # rotationZ is radians (measured: observed values span ~2*pi).
        "Entity": {"posx": entity["posx"][0], "posy": entity["posy"][0],
                   "posz": entity["posz"][0],
                   "rotationZ": entity["rotationZ"][0],
                   "radius": entity["radius"][0]},
        # Despawned-but-still-listed entries. Filtered out of the sweep.
        "State": {"removed": state["removed"][0]},
        # `enabled` is reported per interactible rather than filtered on, so
        # whether a looted chest flips this flag or leaves the array entirely
        # stays a display decision instead of an assumption baked into the hook.
        "Interactible": {"enabled": inter["enabled"][0],
                         "isOffScreen": inter["isOffScreen"][0]},
        "Activity": {"kind": activity["kind"][0],
                     "globalCtx": activity["globalCtx"][0],
                     "contexts": activity["contexts"][0]},
        "Dungeon": {"bossId": dact["bossId"][0],
                    "bossPhaseReached": dact["bossPhaseReached"][0]},
        "DungeonCtx": {"dungeonState": dctx["dungeonState"][0],
                       "lastStateChanged": dctx["lastStateChanged"][0],
                       "startActivity": dctx["startActivity"][0],
                       "endActivity": dctx["endActivity"][0],
                       "nbPlayerDeaths": dctx["nbPlayerDeaths"][0],
                       "step": dctx["step"][0]},
        "InstanceLobby": {"activityId": lobby["activityId"][0],
                          "difficulty": lobby["difficulty"][0]},
        # Every placed world object is an ent.Element. `kind` is its id
        # ("Z1_World_Greenlands_WorldChest_60", "RedOrb_World_140") and
        # `stateId` its state machine — measured: chests read Closed or Locked,
        # obelisks Closed, orbs Enabled. currentVisualState is NOT the same
        # thing: it reads "Opened" on chests that are plainly shut.
        "Element": {"kind": elem["kind"][0], "stateId": elem["stateId"][0],
                    "currentVisualState": elem["currentVisualState"][0]},
        # Ore/herb nodes (ent.interactible.Gatherable, an Element subclass).
        # hitPoints is the replicated "gatherable right now" signal — measured
        # 2026-08-01: each gather tick steps it down (200..0), depletion flips
        # enabled 1->0 and NOTHING else (stateId stays "None"), and the respawn
        # arrives as hp back at max. gatherInf is the CDB row; its texts.type
        # ("Ore" | "Plant") and texts.name resolve on the game thread the same
        # way foe nameplates do.
        "Gatherable": {"hitPoints": gath["hitPoints"][0],
                       "gatherInf": gath["gatherInf"][0]},
        # curDirection is the camera yaw actually being rendered; `direction`
        # is the value it is easing towards. Following the eased one would make
        # the minimap lead the view it is supposed to match.
        "Camera": {"direction": cam["direction"][0],
                   "curDirection": cam["curDirection"][0],
                   "distance": cam["distance"][0]},
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
                 # The buff tracker's source. An hxbit proxy array of
                 # st.skill.Status — same ArrayProxyData -> ArrayDyn -> ArrayObj
                 # chain as Collection.pets, so it is plain pointer reads and
                 # safe from a timer rather than needing the game thread.
                 "statuses": unit["statuses"][0]},
        # One live buff/debuff. Status extends BaseSkill, so these are resolved
        # off the SUBCLASS — the inherited fields land at the same offsets, but
        # asking the subclass is what guarantees it.
        #
        # Measured 2026-08-08 (frida/run_status.py), and none of it is what the
        # field names suggest:
        #   * stopTime is -1 on every status. It is not a clock. The expiry is
        #     startTime + duration.
        #   * duration GROWS on every refresh while startTime stays put (a
        #     Zealot was watched going 15.00 -> 64.03), so duration is
        #     lifetime-at-expiry and NOT the length of the buff.
        #   * refreshDuration IS the nominal length and stays constant, which
        #     makes it the only correct denominator for a clock-swipe.
        #   * refreshDuration == 0 means the status has no timer at all —
        #     Dash, UnderWater, "Surge of Violence". Those end on an event.
        #   * stacks is 1-based and climbs on ONE entry; a status does not
        #     appear once per stack.
        #   * originItem is the only way to tell two ItemStatuses apart: every
        #     one of them reports the literal kind "ItemStatus".
        "Status": {"kind": status["kind"][0],
                   "stacks": status["stacks"][0],
                   "startTime": status["startTime"][0],
                   "duration": status["duration"][0],
                   "refreshDuration": status["refreshDuration"][0],
                   "originItem": status["originItem"][0],
                   "removed": status["removed"][0]},
        "Foe": {"summonOwner": foe["summonOwner"][0],
                "persistantSummon": foe["persistantSummon"][0]},
        # hl.types.ArrayObj: length, then a pointer to an hl_varray whose
        # ELEMENTS START AT +24, past its (t, at, size, pad) header. Reading
        # from +0 yields the header as your first entity and faults instantly.
        "ArrayObj": {"length": arrobj["length"][0], "array": arrobj["array"][0],
                     "data": 24},
        # Rift countdown: worldEvents.currentEvents is an hxbit proxy array
        # (same shape as Group.players), holding st.event.WorldEvent objects.
        # startTime and serverNow share the server clock.
        "TimeState": {"serverNow": tstate["serverNow"][0],
                      "serverStart": tstate["serverStart"][0]},
        "WorldEvents": {"currentEvents": wevents["currentEvents"][0]},
        "WorldEvent": {"kind": wevent["kind"][0],
                       "creationTime": wevent["creationTime"][0],
                       "startTime": wevent["startTime"][0],
                       "stopTime": wevent["stopTime"][0]},
        # `uid` is the player's STEAM ACCOUNT ID, not an internal handle:
        # "S" + the id's bytes as hex in LITTLE-ENDIAN order, trailing zero
        # bytes trimmed. Measured 2026-08-02 (frida/steamid_probe.js) and
        # calibrated against both steam_get_steam_id() and the registry's
        # ActiveUser. Read the digits big-endian and you get a wrong,
        # plausible-looking number — see steam64_from_uid() in the meter.
        # `hero` is the player's live ent.Hero; it was populated for 24/24
        # players on the layer when measured, which is what lets the Social
        # tab show a class for everyone rather than only for people nearby.
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
        # Collected critters (companions), measured 2026-08-07
        # (frida/critter_probe.js): Collection.pets is an hxbit proxy array of
        # plain UNIT KINDS ("Turtle_Grey", "Frog_Demon") — the same string as
        # ent.Unit.kind, which is what lets the map filter compare them at all.
        # The game's own "already caught?" check is Collection.hasPet(kind),
        # seen firing live with exactly these strings. NOT item ids: only two
        # Critter_* items exist in the cdb and both are special grants.
        "AccountProgress": {"collection": acct["collection"][0]},
        # mounts / gliders: the same proxy arrays, of item kinds.
        "Collection": {"pets": coll["pets"][0], "mounts": coll["mounts"][0],
                       "gliders": coll["gliders"][0],
                       "gears": coll["gears"][0]},
        # hxbit wraps a replicated array in a proxy: Group.players is an
        # ArrayProxyData whose ArrayDyn wraps an ArrayObj. Two hops, and the
        # party roster is the reason they are here.
        "ArrayProxyData": {"array": aproxy["array"][0]},
        "ArrayDyn": {"array": adyn["array"][0]},
        "Group": {"groupId": group["groupId"][0], "players": group["players"][0],
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
                     "elements": progress["elements"][0]},
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
                 "effects": weapon_["effects"][0]},
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
        # Which runtime classes are units — the set a DamageResult.target must
        # belong to before `Unit.kind` may be read off it. `target` is typed
        # ent.GameObject, which is two levels above ent.Unit, so `kind`@600 is
        # past the end of the object on a GameObject that isn't a unit. 12
        # classes descend from ent.Unit (heroes, foes, bosses and the two
        # vehicles), so this is the same small closed set foeClasses is.
        "unitClasses": descendants("ent.Unit"),
        # HL virtual field indices for the skill display name
        "SkillRow": {"id_vidx": vidx(row, "id"), "texts_vidx": vidx(row, "texts")},
        "Texts": {"name_vidx": vidx(texts, "name"), "desc_vidx": vidx(texts, "desc")},
    }
    OUT.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[written] {OUT}")

    # Display names, from the game's own data.cdb (res.light.pak, found
    # 2026-08-01 — NOT res.pak, which only carries assets and the non-English
    # lang exports). Units name the boss kill toast and every combat history
    # dataset — a unit's kind is routinely NOT the name on the bar (measured
    # 2026-08-02: 'Cleodora' displays 'Queen Honeyzabeth', 'Phrixes' displays
    # 'High Inquisitor Chakram').
    # Cosmetic, so a failure costs the labels and nothing else — but it is
    # still reported, because "it shows ids again" should be explainable from
    # the log.
    try:
        unit_names = extract_display_names(Path(hlboot).parent)
        out_units = _OUT_DIR / "unit_names.json"
        out_units.write_text(json.dumps(unit_names, ensure_ascii=False,
                                        indent=0), encoding="utf-8")
        print(f"[written] {out_units} ({len(unit_names)} units)")
        specs = extract_heal_specs(Path(hlboot).parent)
        out_heal = _OUT_DIR / "heal_specs.json"
        out_heal.write_text(json.dumps(specs, indent=0), encoding="utf-8")
        print(f"[written] {out_heal} ({len(specs)} heal skills)")
        codex = extract_codex_units(Path(hlboot).parent)
        out_codex = _OUT_DIR / "codex_units.json"
        out_codex.write_text(json.dumps(codex, indent=0), encoding="utf-8")
        print(f"[written] {out_codex} ({len(codex['noCodex'])} excluded, "
              f"{len(codex['elite'])} elite/boss, {len(codex['big'])} big, "
              f"thresholds {codex['thresholds']})")
        traits = extract_unit_traits(Path(hlboot).parent)
        out_traits = _OUT_DIR / "unit_traits.json"
        out_traits.write_text(json.dumps(traits, indent=0), encoding="utf-8")
        print(f"[written] {out_traits} ({len(traits['critter'])} critters, "
              f"{len(traits['spark'])} sparkling)")
        fr = extract_fr_names(Path(hlboot).parent)
        out_fr = _OUT_DIR / "names_fr.json"
        out_fr.write_text(json.dumps(fr, ensure_ascii=False, indent=0),
                          encoding="utf-8")
        print(f"[written] {out_fr} ("
              + ", ".join(f"{len(v)} {k}" for k, v in fr.items()) + ")")
        try:
            import collection_data
            coll = collection_data.build(Path(hlboot).parent,
                                         _OUT_DIR / "collection_img")
            out_coll = _OUT_DIR / "collection.json"
            out_coll.write_text(json.dumps(coll, indent=0),
                                encoding="utf-8")
            print(f"[written] {out_coll} ("
                  + ", ".join(f"{len(v)} {k}" for k, v in coll.items())
                  + ")")
        except Exception as e:
            print(f"[!] collection catalogue skipped ({e})")
        try:
            import skills_data
            tal = skills_data.build(Path(hlboot).parent,
                                    _OUT_DIR / "skill_img")
            (_OUT_DIR / "talents.json").write_text(json.dumps(tal, indent=0),
                                                   encoding="utf-8")
            print(f"[written] {_OUT_DIR / 'talents.json'} "
                  f"({len(tal['trees'])} trees, {len(tal['runes'])} runes)")
        except Exception as e:
            print(f"[!] talent trees skipped ({e})")
        try:
            import codex_items
            ci = codex_items.build(Path(hlboot).parent,
                                   _OUT_DIR / "collection_img")
            (_OUT_DIR / "codex_items.json").write_text(
                json.dumps(ci, indent=0), encoding="utf-8")
            print(f"[written] {_OUT_DIR / 'codex_items.json'} "
                  f"({len(ci)} items)")
        except Exception as e:
            print(f"[!] item codex catalogue skipped ({e})")
        try:
            import bestiary_data
            best = bestiary_data.build(Path(hlboot).parent, codex,
                                       _OUT_DIR / "bestiary_img")
            out_best = _OUT_DIR / "bestiary.json"
            out_best.write_text(json.dumps(best, indent=0), encoding="utf-8")
            print(f"[written] {out_best} ({len(best)} monsters)")
        except Exception as e:
            print(f"[!] bestiary skipped ({e})")
        try:
            import map_data
            wmap = map_data.build(Path(hlboot).parent, _OUT_DIR / "map_tiles")
            out_map = _OUT_DIR / "map.json"
            out_map.write_text(json.dumps(wmap, indent=0), encoding="utf-8")
            print(f"[written] {out_map} ({len(wmap['points'])} points, "
                  f"{len(wmap['meta']['tiles'])} tiles)")
        except Exception as e:
            print(f"[!] world map skipped ({e})")
        dungeons = extract_dungeons(Path(hlboot).parent)
        out_dg = _OUT_DIR / "dungeons.json"
        out_dg.write_text(json.dumps(dungeons, indent=0), encoding="utf-8")
        print(f"[written] {out_dg} ({len(dungeons)} dungeons)")
        try:
            n = extract_boss_portraits(Path(hlboot).parent,
                                       [d["boss"] for d in dungeons],
                                       _OUT_DIR / "boss_portraits")
            print(f"[written] {_OUT_DIR / 'boss_portraits'} ({n} portraits)")
        except Exception as e:
            print(f"[!] boss portraits skipped ({e})")
        aug = extract_augments(Path(hlboot).parent)
        (_OUT_DIR / "augments.json").write_text(json.dumps(aug, indent=0),
                                                encoding="utf-8")
        print(f"[written] {_OUT_DIR / 'augments.json'} ({len(aug)} augments)")
        types = extract_item_types(Path(hlboot).parent)
        (_OUT_DIR / "item_types.json").write_text(json.dumps(types, indent=0),
                                                  encoding="utf-8")
        print(f"[written] {_OUT_DIR / 'item_types.json'} ({len(types)} items)")
        rar = extract_item_rarity(Path(hlboot).parent)
        out_rar = _OUT_DIR / "item_rarity.json"
        out_rar.write_text(json.dumps(rar, indent=0), encoding="utf-8")
        print(f"[written] {out_rar} ({len(rar)} items)")
        try:
            n = extract_item_icons(Path(hlboot).parent,
                                   _OUT_DIR / "item_icons")
            print(f"[written] {_OUT_DIR / 'item_icons'} ({n} icons)")
        except Exception as e:
            print(f"[!] item icons skipped ({e}) — the loot list shows "
                  f"names only")
        smeta = extract_status_meta(Path(hlboot).parent)
        out_status = _OUT_DIR / "status_meta.json"
        out_status.write_text(json.dumps(smeta, ensure_ascii=False, indent=0),
                              encoding="utf-8")
        named = sum(1 for r in smeta["status"].values() if r.get("name"))
        print(f"[written] {out_status} ({len(smeta['status'])} statuses, "
              f"{named} named, {len(smeta['types'])} types, "
              f"{len(smeta['items'])} status-granting items)")
    except Exception as e:
        print(f"[!] display names skipped ({e}) — boss toasts and history "
              f"dataset names fall back to ids")

    print(json.dumps(meta, indent=2))


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
    """id -> texts.name for the cdb's unit sheet — what names a boss toast
    and a combat history dataset. The item sheet was read here too until
    the mount and glider features were removed; nothing labels items now."""
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
FR_SHEETS = ("ach", "activity", "attribute", "gatherable", "item",
             "itemType", "job", "rarity", "skill", "unit", "unitType",
             "zone")
# Sheets whose French descriptions the app shows (the collection's details).
FR_DESC = {"ach": ("desc",), "item": ("texts.flavorDesc", "texts.desc")}


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
                            "loot": dungeon_loot(unit_rows.get(boss) or {},
                                                 item_rows, tables)})
    return out


# Armour slots: the dungeon's faction set, which no loot table lists.
ARMOUR_TYPES = {"Head", "Shoulders", "Chest", "Hands", "Waist", "Legs",
                "Feet", "Back"}
APTITUDE_CLASS = {"Fighter": "warrior", "Wizard": "mage",
                  "Assassin": "rogue", "Cleric": "priest"}


def dungeon_loot(boss, item_rows, tables):
    """What the end of a dungeon can give, from the game's own data.

    * The reward chest (Gameplay/Elements/Activities/BossChest.prefab, placed
      in each dungeon with lootTable = the boss) rolls the boss's table, which
      has the Weights flag: ONE item, chosen by weight.
    * The boss's other table rolls on its death, each line on its own chance.
      The unit's `bossLootTable`/`lootTable` props point at the two, but not
      always the same way round — the Weights flag says which is which.
    * Every dungeon activity (Gameplay/Activities/Base.prefab) gives
      DungeonCrate: spark shards, the quantity by level.
    * The faction's armour set: in no table, rolled by the game's code
      (st.Player.dropFactionLoot) — so no chance is known.
    Each entry: {item, type, rarity, apt, src, chance (0..1 or None), qty}."""
    def entry(iid, src, chance, qty=None):
        row = item_rows.get(iid) or {}
        return {"item": iid, "type": row.get("type") or "",
                "rarity": row.get("rarity") or "",
                "apt": [APTITUDE_CLASS[a["ref"]]
                        for a in row.get("aptitudes") or ()
                        if a.get("ref") in APTITUDE_CLASS],
                "src": src, "chance": chance, "qty": qty}

    out = []
    props = boss.get("props") or {}
    for tid in (props.get("bossLootTable"), props.get("lootTable")):
        t = tables.get(tid) or {}
        lines = [ln for ln in t.get("loot") or () if ln.get("item")]
        if (t.get("flags") or 0) & 1:
            total = sum(float(ln.get("proba") or 0) for ln in lines) or 1.0
            out += [entry(ln["item"], "coffre",
                          float(ln.get("proba") or 0) / total) for ln in lines]
        else:
            out += [entry(ln["item"], "boss", float(ln.get("proba") or 0))
                    for ln in lines]
    faction = boss.get("faction")
    if faction:
        out += [entry(iid, "faction", None)
                for iid, row in item_rows.items()
                if row.get("faction") == faction
                and row.get("type") in ARMOUR_TYPES]
    crate = [ln for ln in (tables.get("DungeonCrate") or {}).get("loot") or ()
             if ln.get("item")]
    for iid in dict.fromkeys(ln["item"] for ln in crate):
        out.append(entry(iid, "coffre", 1.0, [
            [ln.get("itemMin"), ln.get("itemMax"), ln.get("minLvl"),
             ln.get("maxLvl")] for ln in crate if ln["item"] == iid]))
    return out


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
    """Two per-unit traits the minimap wants, straight out of data.cdb.

      critter - `unit.type == "Critter"`, which the game's own unitType sheet
                calls "Companions". 74 of them, and they are NOT foes in any
                sense that matters on a map: they wander, they don't fight, and
                drawing them as red dots among real mobs is what this fixes.
      spark   - the `Spark` unit FLAG (bit 22, read off the column definition
                rather than hardcoded). 36 units carry it and every one of them
                is named "Sparkling ..." in the cdb: Sparkling Grassflopper,
                Sparktail, Sparkling Skunk, Sparkling Crab. Ten are critters;
                the rest are the rare `_U` variants of ordinary mobs.

    `Base_Critter` is excluded — it is the inherit template every critter
    derives from, not something that spawns.

    Emitted as two id lists. Anything not listed has neither trait.
    """
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

    critters, spark = [], []
    for ln in sheets["unit"]["lines"]:
        uid = ln.get("id")
        if not isinstance(uid, str) or uid == "Base_Critter":
            continue
        fl = ln.get("flags")
        fl = fl if isinstance(fl, int) else 0
        if ln.get("type") == "Critter":
            critters.append(uid)
        if (fl >> spark_bit) & 1:
            spark.append(uid)
    return {"critter": sorted(critters), "spark": sorted(spark)}


# Statuses whose `kind` is not a cdb id. Measured 2026-08-08 (frida/run_status.py,
# resting dump on a Warrior): every st.skill.ItemStatus reports the literal
# string "ItemStatus", so food and potions are one indistinguishable kind as far
# as the array is concerned. The tracker identifies those from originItem
# instead; this list is what tells it which kinds to stop looking up here.
CLASS_KINDS = ("ItemStatus",)

# The statusType flags column, whose BIT INDICES are read off the column
# definition rather than hardcoded — same reasoning as the unit `Spark` flag
# above: a patch inserting a flag would otherwise silently re-point every one.
STATUS_TYPE_FLAGS = ("DoT", "CrowdControl", "HardCC", "HoT")

# What makes a `skill` row a status. Measured 2026-08-08: filtering on
# props.status finds 250 rows and MISSES 43 real ones — a probe session on a
# Warrior turned up "Surge of Violence", "Resilience of the Unkillable Demon
# King" and PhysicalBlock_Status_WellTimed on the hero, all named, all with
# icons, none carrying props.status. `nature == Status` finds 293 and is a
# strict superset of the props.status set, so props.status is only good for the
# extra columns (maxStacks, types) and not for deciding what counts.
STATUS_NATURE = "Status"


def _status_nature_index(skill_sheet):
    """Which `nature` enum index means Status, read off the column definition
    rather than hardcoded — a patch inserting a nature above it would otherwise
    silently re-point the whole filter, exactly as the heal-effect index and
    the unit Spark bit are guarded."""
    for c in skill_sheet["columns"]:
        if c.get("name") == "nature" and isinstance(c.get("typeStr"), str):
            names = c["typeStr"].split(":", 1)[-1].split(",")
            if STATUS_NATURE in names:
                return names.index(STATUS_NATURE)
    raise ValueError("skill.nature column has no Status entry")


def extract_status_meta(game_dir):
    """Everything the buff tracker needs to NAME and CLASSIFY a status, from
    data.cdb — so the picker can offer every buff in the game rather than only
    the ones you happen to have proc'd, and so a name never costs an HL call.

    A status is a `skill` row whose `nature` is Status (see STATUS_NATURE for
    why that and not props.status); its `kind` at runtime IS that row's id
    (measured: Enchant_Zealot_Status, GA_Demon_Combo_Status). 293 rows in this
    build.

    Per status:
      name        display name, or absent when the cdb never gave it one (the
                  internal plumbing — Dash_Status and friends — mostly has none,
                  which is exactly how the picker knows to hide them)
      desc        tooltip text, for the picker's list
      max         the cdb's maxStacks. ADVISORY ONLY, and specifically NOT a
                  denominator: measured 2026-08-08, GA_Demon_Combo_Status is
                  maxStacks 3 in the cdb and was observed live at 5 stacks —
                  gear and talents raise the ceiling, which is why Status has a
                  computed getMaxStacks() rather than reading this. A tracker
                  that renders "5/3" is worse than one that renders "5".
      dur         the cdb's default duration in seconds. NOT authoritative — the
                  live Status carries its own, which affixes and ranks change —
                  but it is what lets the picker's preview show a plausible
                  sweep before you have ever had the buff.
      types       statusType ids (Buff, Debuff, Bleed, Stun, ...)
      cc/dot/hot  rolled up from those types' flags, for the picker's filters
      color       the first type's colour as #rrggbb, which is what a status
                  with no icon falls back to being drawn as

    Plus a `types` table naming and colouring each category. Icons are NOT here:
    they come off an atlas in res.pak (857MB) and are a committed asset built by
    build_status_icons.py, not something to extract on every self-heal.
    """
    import pak_extract
    data, entries, data_off = pak_extract.load(Path(game_dir) / "res.light.pak")
    e = next(x for x in entries if x.path.endswith("data.cdb"))
    cdb = json.loads(data[data_off + e.pos: data_off + e.pos + e.size])
    sheets = {s["name"]: s for s in cdb["sheets"]}

    def text(node, field):
        """cdb text columns are sometimes a bare string and sometimes {"v": ...}
        — both shapes appear in this one file (the unit sheet's names are bare,
        statusType's are wrapped), so neither may be assumed."""
        v = (node or {}).get(field)
        if isinstance(v, dict):
            v = v.get("v")
        return v if isinstance(v, str) and v else None

    bits = {}
    for c in sheets["statusType"]["columns"]:
        if c["name"] == "flags" and isinstance(c.get("typeStr"), str):
            names = c["typeStr"].split(":", 1)[-1].split(",")
            bits = {n: i for i, n in enumerate(names)}
    missing = [f for f in STATUS_TYPE_FLAGS if f not in bits]
    if missing:
        raise ValueError(f"statusType flags column has no {missing} bit(s)")

    types = {}
    for ln in sheets["statusType"]["lines"]:
        tid = ln.get("id")
        if not isinstance(tid, str):
            continue
        fl = ln.get("flags") if isinstance(ln.get("flags"), int) else 0
        col = ln.get("color")
        entry = {"name": text(ln.get("texts"), "name") or tid}
        if isinstance(col, int):
            entry["color"] = f"#{col & 0xFFFFFF:06x}"
        for f in STATUS_TYPE_FLAGS:
            if (fl >> bits[f]) & 1:
                entry[f] = 1
        types[tid] = entry

    nature_status = _status_nature_index(sheets["skill"])
    out = {}
    for ln in sheets["skill"]["lines"]:
        sid = ln.get("id")
        if not isinstance(sid, str) or ln.get("nature") != nature_status:
            continue
        # Optional: 43 of the 293 statuses have no props.status block at all,
        # and they are ordinary buffs, not leftovers. Everything read out of it
        # is therefore a bonus rather than a requirement.
        props = (ln.get("props") or {}).get("status")
        props = props if isinstance(props, dict) else {}
        tids = [t.get("type") for t in (props.get("types") or [])
                if isinstance(t.get("type"), str)]
        row = {}
        nm = text(ln.get("texts"), "name")
        if nm:
            row["name"] = nm
        ds = text(ln.get("texts"), "desc")
        if ds:
            row["desc"] = ds
        if isinstance(props.get("maxStacks"), int):
            row["max"] = props["maxStacks"]
        if isinstance(ln.get("duration"), (int, float)):
            row["dur"] = ln["duration"]
        if tids:
            row["types"] = tids
            for f, key in (("DoT", "dot"), ("HoT", "hot")):
                if any(types.get(t, {}).get(f) for t in tids):
                    row[key] = 1
            if any(types.get(t, {}).get("CrowdControl")
                   or types.get(t, {}).get("HardCC") for t in tids):
                row["cc"] = 1
            col = next((types[t]["color"] for t in tids
                        if t in types and "color" in types[t]), None)
            if col:
                row["color"] = col
        out[sid] = row

    # Items that grant a status, by name. Needed because every ItemStatus
    # reports the same kind, so "Beggar's Garbure" and "Minor Alchemist
    # Cauldron" are one indistinguishable string until the originItem names
    # them. Scoped to the items that actually grant one rather than reviving
    # the whole item table, which was dropped from the bundle in 2c67b5a for
    # its size — this is a few dozen rows, not a thousand.
    #
    # props.effects is a LIST of effect blocks, each of which may carry its own
    # `status` list. It is emphatically not a dict, and treating it as one
    # silently matches nothing at all.
    items = {}
    for ln in sheets["item"]["lines"]:
        iid = ln.get("id")
        if not isinstance(iid, str):
            continue
        effects = (ln.get("props") or {}).get("effects")
        if not isinstance(effects, list):
            continue
        if not any(isinstance(b, dict) and b.get("status") for b in effects):
            continue
        nm = text(ln.get("texts"), "name")
        if nm:
            items[iid] = nm
    return {"status": out, "types": types, "items": items,
            "classKinds": list(CLASS_KINDS)}


if __name__ == "__main__":
    main()
