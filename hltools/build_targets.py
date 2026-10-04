"""analysis_out/resolver_data.json: the game functions the hook attaches to
or calls, found by name in the game's bytecode (hlboot.dat), so a patch that
moves them moves their indices too."""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode, HOBJ, HSTRUCT  # noqa: E402
from gamepath import find_hlboot  # noqa: E402

# The installed app runs this from a bundle folder deleted on exit: it says
# where the result goes.
OUT = Path(os.environ.get("FAREVER_ANALYSIS_OUT")
           or Path(__file__).resolve().parent.parent / "analysis_out")

# Natives of the game's own libraries, anchors for the hook's search.
HDLL_LIBS = {"ssl", "fmt", "uv", "ui", "sdl", "directx", "dx12", "openal",
             "heaps", "steam", "video", "hlfmod", "mysql", "dlss"}

# Combat functions worth counting: the base classes' methods whose name
# says damage, hit, kill or heal.
COMBAT_CLASSES = {
    "ent.Unit", "ent.Hero", "ent.Foe", "ent.GameObject", "ent.Projectile",
    "ent.UnitAttributes",
    "st.skill.Skill", "st.skill.BaseSkill", "st.skill.SkillStep",
    "st.skill.SkillContext", "script.SkillScript", "script.UnitScript",
}
METHOD_RX = re.compile(
    r"(applyDamage|dealDamage|takeDamage|doDamage|onDamage|computeDamage|"
    r"inflict|hurt|onHit|applyHit|dealHit|receiveHit|^hit$|damage|onKill|"
    r"onDeath|die|kill|heal)", re.IGNORECASE)
PRIMARY = ("ent.Unit.applyDamage", "ent.Hero.applyDamage",
           "script.SkillScript.applyDamage", "script.UnitScript.onDamage")

# The combat functions the hook counts (no hot getters among them). Heals:
# the server-side path never runs on the client, what does is the heal's
# effect on the target (the skill and its owner) and the health attribute
# (the amount).
SHORTLIST = (
    "ent.Unit.applyDamage", "ent.Unit.computeDamage", "ent.Unit.receiveDamage",
    "ent.Unit.onInflictDamage", "ent.Unit.onReceiveDamage",
    "ent.Unit.rpcReceiveDamage__impl", "ent.Unit.clientReceiveHit",
    "ent.Unit.playHitDamage", "ent.Unit.recordsDamage", "ent.Unit.doReceiveHit",
    "ent.GameObject.logDamage", "ent.GameObject.clientReceiveHit",
    "ent.GameObject.doReceiveHit", "ent.GameObject.receiveHit",
    "ent.Hero.applyDamage", "ent.Hero.receiveDamage", "ent.Hero.onReceiveDamage",
    "script.SkillScript.onInflictDamage", "script.SkillScript.onDamage",
    "script.SkillScript.onHit", "st.skill.BaseSkill.calcDamageAmount",
    "st.skill.BaseSkill.evalDamage",
    "ent.Unit.playHitHealFX", "ent.UnitAttributes.set_health",
)

# The functions the hook attaches to, by group: (its key in the file, the
# functions, what a missing one costs).
TARGETS = (
    # the camera's per-frame method: the hook's clock on the game's own
    # thread; on the base class, so whichever camera drives the view
    ("cam_targets", ("client.BaseCamera.postUpdate",), "camera target"),
    # the boss bar's own refresh (twice a second): `this` holds the bars
    ("boss_targets", ("ui.hud.BossesInfo.fetchBosses",), "boss target"),
    # a bar is raised for elites too: these tell a boss. Foe.shouldShowBossInfo
    # is not among them: it throws when called with `this` only
    ("boss_fns", ("ent.Unit.isBoss", "ent.Unit.isElite"), "boss target"),
)
# The local player, through the game's singletons.
SINGLETON_FNS = ("GameApp.getCameraHero", "ui.Console.getMyHero",
                 "$GameApp.getMyHero", "$GameApp.get")
# The current map, ()->String, called from the damage hook (zone changes).
MAP_FN = "$Main.getMapId"
# StringMap reads go through these natives (they allocate: game thread only).
MAP_NATIVES = ("hbget", "hbkeys", "hbsize")


def anchors(code):
    return [{"lib": n.lib, "name": n.name, "findex": n.findex,
             "symbol": f"{n.lib}_{n.name}", "module": f"{n.lib}.hdll"}
            for n in code.natives if n.lib in HDLL_LIBS][:40]


def combat_candidates(code, names):
    """Combat methods of the combat classes, the primary ones always."""
    out = {f"{t.name}.{p.name}": p.findex
           for t in code.types
           if t.kind in (HOBJ, HSTRUCT) and t.name in COMBAT_CLASSES
           for p in t.protos if METHOD_RX.search(p.name)}
    out.update({nm: fi for fi, nm in names.items() if nm in PRIMARY})
    return out


def by_name(names, wanted):
    """{function name: index} of the wanted functions the build has."""
    return {nm: fi for fi, nm in names.items() if nm in wanted}


def resolve(code):
    names = code.findex_names()
    candidates = combat_candidates(code, names)
    payload = {
        "nfunctions": code.counts["nfunctions"],
        "nnatives": code.counts["nnatives"],
        "anchors": anchors(code),
        "candidates": candidates,
        "count_targets": {nm: candidates[nm] for nm in SHORTLIST
                          if nm in candidates},
    }
    for key, wanted, *_ in TARGETS:
        payload[key] = by_name(names, wanted)
    payload["map_natives"] = {n.name: n.findex for n in code.natives
                              if n.lib == "std" and n.name in MAP_NATIVES}
    payload["funcs"] = {nm.lstrip("$"): fi for nm, fi in
                        by_name(names, SINGLETON_FNS).items()}
    payload["map_fn"] = next((fi for fi, nm in names.items()
                              if nm == MAP_FN), None)
    return payload


def report(payload):
    """What was found, and each function this build lacks."""
    print(f"anchors={len(payload['anchors'])}  "
          f"candidates={len(payload['candidates'])}")
    for key, wanted, label, *cost in TARGETS:
        for nm in wanted:
            if nm not in payload[key]:
                print(f"    [!] {label} not found in this build: {nm}"
                      + (f" — {cost[0]}" if cost else ""))
    for nm, fi in sorted(payload["candidates"].items()):
        print(f"    {nm:<45} findex={fi}")


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
