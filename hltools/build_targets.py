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


def resolve(code):
    names = code.findex_names()
    payload = {"anchors": anchors(code)}
    for key, wanted, *_ in TARGETS:
        payload[key] = by_name(names, wanted)
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
    print(f"    crowd-control statuses: {len(payload['cc_statuses'])}")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "resolver_data.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report(payload)
    print(f"[written] {out}")


if __name__ == "__main__":
    main()
