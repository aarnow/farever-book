"""The achievements, for the app's Succès tab — from data.cdb (res.light.pak).

The ach sheet holds three kinds of rows: categories (type Category, with a
parent category for the regional sub-categories), achievements, and their
tiers — an achievement whose `parent` is the previous tier ("Level 10" ->
"Level 20" -> "Level 30"). Each has points, an optional item reward, and
objectives: {ref (CounterValue, Collect, ElementCompleted,
AchievementCompleted, FoeKilledOnce, ActivityCompleted...), targets, value}.

What a character has completed is read in game (Progress.achievements /
AccountProgress.achievements); the objectives are kept as they are so the app
can measure progress on the ones it has data for. The names and
descriptions are the game's French text, looked up by the app."""
import json
from pathlib import Path

import pak_extract
from collection_data import _images

CATEGORY, ACHIEVEMENT = 1, 0


def build(game_dir, img_dir=None):
    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}
    consts = {ln["id"]: ln for ln in sheets["constant"]["lines"]
              if isinstance(ln.get("id"), str)}

    def const_value(cid):
        v = (consts.get(cid) or {}).get("v") or {}
        for k in ("int", "float"):
            if isinstance(v.get(k), (int, float)):
                return v[k]
        return None

    cats, achs, icons = [], [], {}
    for ln in sheets["ach"]["lines"]:
        aid = ln.get("id")
        if not isinstance(aid, str):
            continue
        if ln.get("type") == CATEGORY:
            cats.append({"id": aid, "parent": ln.get("category")})
            if ln.get("gfx"):
                icons["achcat_" + aid] = ln["gfx"]
            continue
        objs = []
        for o in ln.get("objectives") or ():
            val = o.get("value") or {}
            v = val.get("v")
            if v is None and val.get("refConst"):
                v = const_value(val["refConst"])
            objs.append({"ref": o.get("ref"), "v": v,
                         "t": [t.get("ref") for t in o.get("targets") or ()],
                         "all": bool(val.get("all"))})
        props = ln.get("props") or {}
        achs.append({"id": aid, "cat": ln.get("category"),
                     "parent": ln.get("parent"),
                     "points": ln.get("points") or 0,
                     "obj": objs,
                     "reward": [r.get("item") for r in
                                (ln.get("reward") or {}).get("items") or ()
                                if r.get("item")],
                     "copyDesc": props.get("copyDesc"),
                     "consts": {c["id"]: (c.get("v") or {}).get("v")
                                for c in props.get("consts") or ()
                                if c.get("id")}})
    if img_dir is not None:
        _images(game_dir, img_dir, icons)
    return {"categories": cats, "achievements": achs}
