"""analysis_out/resolver_data.json: the indices of the game functions the
hook attaches to or calls, found by name in hlboot.dat."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode  # noqa: E402
from gamepath import find_hlboot  # noqa: E402

# Overridable: the installed app runs this from a temporary bundle folder.
OUT = Path(os.environ.get("FAREVER_ANALYSIS_OUT")
           or Path(__file__).resolve().parent.parent / "analysis_out")

# Natives of the game's own libraries, anchors for the hook's search.
HDLL_LIBS = {"ssl", "fmt", "uv", "ui", "sdl", "directx", "dx12", "openal",
             "heaps", "steam", "video", "hlfmod", "mysql", "dlss"}

# (key in the file, functions, label for a missing one)
TARGETS = (
    # the hook's per-frame clock on the game thread (base class: any camera)
    ("cam_targets", ("client.BaseCamera.postUpdate",), "camera target"),
    # hits and heals: the heal itself runs server-side; the client sees its
    # effect (skill, owner) and the health attribute (amount)
    ("combat_targets", ("ent.Unit.onInflictDamage", "ent.Unit.playHitHealFX",
                        "ent.UnitAttributes.set_health",
                        # the heal's number, sent by the server
                        "ent.Unit.rpcDisplayHeal__impl"), "combat target"),
    # the boss bar's own refresh (twice a second): `this` holds the bars
    ("boss_targets", ("ui.hud.BossesInfo.fetchBosses",), "boss target"),
    # elites get a bar too: this tells a boss (Foe.shouldShowBossInfo throws
    # when called with `this` only)
    ("boss_fns", ("ent.Unit.isBoss",), "boss target"),
    # whether a game window frees the mouse (inventory, map...), apart from
    # the player's own FreeCursor key (GameApp.playerRequestedFreeCursor)
    ("cursor_fns", ("GameApp.shouldFreeCursor",), "cursor target",
     "the overlays tell a game window from Alt by the cursor alone"),
    # a status coming to life on a unit (the client's copy of the server's):
    # the journals' crowd controls (stun, root, slow...)
    ("status_fns", ("st.skill.Status.init",), "status target",
     "the journals show no crowd control"),
    # the boss bars' values: their health bar's text shown (the game hides
    # it there; its update writes "value / max" once it is)
    ("bar_fns", ("h2d.Object.set_visible",), "bar target",
     "no health values on the boss bars"),
    # the monsters' nameplates (FoeWidget): its update each frame (the
    # closure initActive binds, found below), the gauge's inset (its frame)
    ("foe_fns", ("ui.hud.FoeWidget.initActive",
                 "ui.comp.BaseGauge.set_barWidth", "ui.comp.BaseGauge.set_barHeight",
                 "ui.comp.BaseGauge.applyColorTo", "h2d.Object.set_filter",
                 "ui.comp.BaseGauge.set_text", "ui.comp.FmtText.set_text",
                 "h2d.$Bitmap.__constructor__", "ui.$BaseUI.loadTextIcon",
                 "h2d.$ScaleGrid.__constructor__", "h2d.ScaleGrid.set_width",
                 "h2d.$Tile.fromColor", "shiro.ui.$DynamicBackground.__constructor__",
                 "ui.notify.NotifyManager.show", "st.player.Whispers.onInviteGroup",
                 "ui.win.BaseDialog.init", "h2d.Text.set_textColor", "ent.Unit.get_maxHealth",
                 "ent.Unit.getShield",
                 "shiro.ui.DynamicBackground.set_background",
                 "shiro.ui.DynamicBackground.set_backgroundAlpha",
                 "shiro.ui.DynamicBackground.sync",
                 "h2d.ScaleGrid.set_height",
                 "h2d.filter.$Glow.__constructor__"),
     "nameplate target", "no targeting options"),
    # the game's menu (Échap): a Farever Book button made in it, as the
    # game makes its own (a Button, its click, its place after Options)
    ("menu_fns", ("ui.win.EscapeMenu.init", "ui.comp.$Button.__constructor__",
                  "ui.UIElement.set_onClick", "h2d.Object.addChildAt",
                  "h2d.Object.getChildIndex", "ui.win.BaseWindow.closeWindow"), "menu target",
     "no Farever Book button in the game's menu"),
    # the character picked on the select screen: its id in the game's
    # database (HeroData is null on the client), two may share a name
    ("select_fns", ("ui.win.CharacterSelectScreen.setCurrentCharacter",),
     "select target", "characters of the same name are told apart by name only"),
)
# The local player, through the game's singletons.
SINGLETON_FNS = ("GameApp.getCameraHero", "ui.Console.getMyHero",
                 "$GameApp.getMyHero", "$GameApp.get")
# StringMap reads go through these natives (they allocate: game thread only).
MAP_NATIVES = ("hbget", "hbkeys")


def anchors(code):
    return [{"lib": n.lib, "name": n.name, "findex": n.findex,
             "symbol": f"{n.lib}_{n.name}", "module": f"{n.lib}.hdll"}
            for n in code.natives if n.lib in HDLL_LIBS][:40]


def by_name(names, wanted):
    """{function name: index} of the wanted functions the build has."""
    return {nm: fi for fi, nm in names.items() if nm in wanted}


# The functions the hook calls to change the game (not only reads): their
# arguments as this build has them must be these, or they are left out (the
# feature then off, the hook told so) — a game update changing one must
# never have the hook call it with the wrong arguments.
SIGNATURES = {
    "ui.comp.$Button.__constructor__": "(ui.comp.Button,String,String,null<bool>,h2d.Object)->void",
    "ui.UIElement.set_onClick": "(ui.UIElement,()->void)->()->void",
    "ui.win.EscapeMenu.init": "(ui.win.EscapeMenu)->void",
    "ui.win.BaseWindow.closeWindow": "(ui.win.BaseWindow,null<bool>)->bool",
    "h2d.Object.set_visible": "(h2d.Object,bool)->bool",
    "ui.comp.BaseGauge.set_barWidth": "(ui.comp.BaseGauge,i32)->i32",
    "ui.comp.BaseGauge.applyColorTo": "(ui.comp.BaseGauge,h2d.Flow,null<i32>)->void",
    "ui.comp.BaseGauge.set_barHeight": "(ui.comp.BaseGauge,i32)->i32",
    "h2d.Object.set_filter": "(h2d.Object,h2d.filter.Filter)->h2d.filter.Filter",
    "ui.comp.BaseGauge.set_text": "(ui.comp.BaseGauge,String)->String",
    "ui.comp.FmtText.set_text": "(ui.comp.FmtText,String)->String",
    "h2d.$Bitmap.__constructor__": "(h2d.Bitmap,h2d.Tile,h2d.Object)->void",
    "ui.$BaseUI.loadTextIcon": "(String)->h2d.Tile",
    "h2d.$ScaleGrid.__constructor__":
        "(h2d.ScaleGrid,h2d.Tile,i32,i32,null<i32>,null<i32>,h2d.Object)->void",
    "h2d.ScaleGrid.set_width": "(h2d.ScaleGrid,f64)->f64",
    "h2d.$Tile.fromColor": "(i32,null<i32>,null<i32>,null<f64>)->h2d.Tile",
    "ui.notify.NotifyManager.show": "(ui.notify.NotifyManager,ui.notify.BaseNotify)->void",
    "st.player.Whispers.onInviteGroup": "(st.player.Whispers,virtual,String,i64,String)->void",
    "ui.win.BaseDialog.init": "(ui.win.BaseDialog)->void",
    "h2d.Text.set_textColor": "(h2d.Text,i32)->i32",
    "ent.Unit.get_maxHealth": "(ent.Unit)->f64",
    "ent.Unit.getShield": "(ent.Unit)->f64",
    "shiro.ui.$DynamicBackground.__constructor__": "(shiro.ui.DynamicBackground,h2d.Tile,h2d.Object)->void",
    "shiro.ui.DynamicBackground.set_background": "(shiro.ui.DynamicBackground,i32)->i32",
    "shiro.ui.DynamicBackground.set_backgroundAlpha": "(shiro.ui.DynamicBackground,f64)->f64",
    "shiro.ui.DynamicBackground.sync": "(shiro.ui.DynamicBackground,h2d.RenderContext)->void",
    "h2d.ScaleGrid.set_height": "(h2d.ScaleGrid,f64)->f64",
    "h2d.filter.$Glow.__constructor__":
        "(h2d.filter.Glow,ref<i32>,ref<f64>,ref<f64>,ref<f64>,ref<f64>,ref<bool>)->void",
}
# a nameplate's update, each frame: the closure FoeWidget.initActive binds
FOE_UPDATE = "(ui.hud.FoeWidget,f64)->void"
# a player's nameplate's update (HeroWidget), each frame: the closure its
# init binds (UIElement.bindUpdate)
HERO_UPDATE = "(ui.hud.HeroWidget,f64)->void"


def resolve(code):
    names = code.findex_names()
    payload = {"anchors": anchors(code)}
    for key, wanted, *_ in TARGETS:
        payload[key] = by_name(names, wanted)
    # those that change the game: kept only with their arguments as expected
    by_fn = {v: k for k, v in names.items()}
    init = by_fn.get("ui.hud.FoeWidget.initActive")
    hero_init = by_fn.get("ui.hud.HeroWidget.init")
    types = code.function_types(closures_of={i for i in (init, hero_init) if i is not None})
    hu = [c for c in (code.closures.get(hero_init) or [])
          if code.type_str(types.get(c, -1)) == HERO_UPDATE]
    if len(hu) == 1:
        payload["hero_update"] = hu[0]
    else:
        print("    [!] the players' nameplates' update not found: no options for the allies")
    upd = (code.closures.get(init) or [None])[0] if init is not None else None
    if upd is not None and code.type_str(types.get(upd, -1)) == FOE_UPDATE:
        payload["foe_update"] = upd
    else:
        print("    [!] the nameplates' update not found: no targeting options")
    bad = []
    for key, *_ in TARGETS:
        for nm, fi in list(payload[key].items()):
            want = SIGNATURES.get(nm)
            got = code.type_str(types[fi]) if fi in types else "?"
            if want is not None and got != want:
                del payload[key][nm]
                bad.append(f"{nm}: {got} (expected {want})")
    for line in bad:
        print(f"    [!] arguments changed, left out: {line}")
    payload["map_natives"] = {n.name: n.findex for n in code.natives
                              if n.lib == "std" and n.name in MAP_NATIVES}
    payload["funcs"] = {nm.lstrip("$"): fi for nm, fi in
                        by_name(names, SINGLETON_FNS).items()}
    return payload


def report(payload):
    """What was found, and each function this build lacks."""
    print(f"anchors={len(payload['anchors'])}")
    for key, wanted, label, *cost in TARGETS:
        for nm in wanted:
            if nm not in payload[key]:
                print(f"    [!] {label} not found in this build: {nm}"
                      + (f" — {cost[0]}" if cost else ""))
            else:
                print(f"    {nm:<45} findex={payload[key][nm]}")


def neutral_units(game_dir):
    """The units that attack no one first (data.cdb unit flags NeutralAggro
    or NoAggro), for the bars' colours: a neutral foe's."""
    try:
        import pak_extract
        cdb = json.loads(pak_extract.read_entry(Path(game_dir)
                                                / "res.light.pak", "data.cdb"))
        sheet = next(s for s in cdb["sheets"] if s["name"] == "unit")
        col = next(c for c in sheet["columns"] if c["name"] == "flags")
        flags = col["typeStr"].split(":", 1)[1].split(",")
        mask = (1 << flags.index("NeutralAggro")) | (1 << flags.index("NoAggro"))
    except Exception as e:  # noqa: BLE001
        print(f"    [!] unit flags unreadable ({e}): no neutral foe told apart")
        return []
    return sorted(ln["id"] for ln in sheet["lines"] if isinstance(ln.get("id"), str)
                  and isinstance(ln.get("flags"), int) and ln["flags"] & mask)


def critter_units(game_dir):
    """The companions' units (data.cdb unit type Critter: every pet of the
    Collection is one), for the minimap: the hook lists those around."""
    try:
        import pak_extract
        cdb = json.loads(pak_extract.read_entry(Path(game_dir)
                                                / "res.light.pak", "data.cdb"))
    except Exception as e:  # noqa: BLE001
        print(f"    [!] data.cdb unreadable ({e}): no companion listed")
        return []
    return sorted(ln["id"] for s in cdb["sheets"] if s["name"] == "unit"
                  for ln in s["lines"] if isinstance(ln.get("id"), str)
                  and ln.get("type") == "Critter")


def cc_statuses(game_dir):
    """{status: its crowd-control type}: the statuses a status type of
    which (or a parent of it) carries the CrowdControl flag (data.cdb's
    statusType, as $HSkill.hasStatusFlag reads it). The type names the ones
    with no name of their own (it is a status too: Slow, Root, Stun...)."""
    try:
        import pak_extract
        cdb = json.loads(pak_extract.read_entry(Path(game_dir)
                                                / "res.light.pak", "data.cdb"))
    except Exception as e:  # noqa: BLE001
        print(f"    [!] data.cdb unreadable ({e}): no crowd control listed")
        return {}
    sh = {s["name"]: s for s in cdb["sheets"]}
    st = sh["statusType"]
    bits = next(c for c in st["columns"]
                if c["name"] == "flags")["typeStr"].split(":")[1].split(",")
    cc_bit = 1 << bits.index("CrowdControl")
    types = {t["id"]: t for t in st["lines"]}

    def flags(tid):
        f, seen = 0, set()
        while tid in types and tid not in seen:
            seen.add(tid)
            f |= types[tid].get("flags") or 0
            tid = types[tid].get("parent")
        return f
    out = {}
    for s in sh["skill"]["lines"]:
        for t in ((s.get("props") or {}).get("status") or {}).get("types") or ():
            if flags(t.get("type")) & cc_bit:
                out[s["id"]] = t["type"]
                break
    return out


def main():
    hlboot = find_hlboot()
    print(f"[*] parsing {hlboot}")
    payload = resolve(HLCode(hlboot).parse())
    payload["cc_statuses"] = cc_statuses(Path(hlboot).parent)
    payload["critters"] = critter_units(Path(hlboot).parent)
    payload["neutral_units"] = neutral_units(Path(hlboot).parent)
    print(f"    companions' units: {len(payload['critters'])}")
    print(f"    crowd-control statuses: {len(payload['cc_statuses'])}")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "resolver_data.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report(payload)
    print(f"[written] {out}")


if __name__ == "__main__":
    main()
