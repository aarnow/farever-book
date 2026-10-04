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
                        "ent.UnitAttributes.set_health"), "combat target"),
    # the boss bar's own refresh (twice a second): `this` holds the bars
    ("boss_targets", ("ui.hud.BossesInfo.fetchBosses",), "boss target"),
    # elites get a bar too: this tells a boss (Foe.shouldShowBossInfo throws
    # when called with `this` only)
    ("boss_fns", ("ent.Unit.isBoss",), "boss target"),
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


def main():
    hlboot = find_hlboot()
    print(f"[*] parsing {hlboot}")
    payload = resolve(HLCode(hlboot).parse())
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "resolver_data.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report(payload)
    print(f"[written] {out}")


if __name__ == "__main__":
    main()
