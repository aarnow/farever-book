"""Talent trees and skill icons, for the Character tab.

From data.cdb: each class unit's talentTrees (a root skill, then talents by
tier 1-4 and branch Left/Center/Right, the Root being tier 0) and each talent
skill's props.talent.maxPoints; and every skill's and every skill mastery
("rune") icon — gfx {file, size, x, y, width?, height?}: a tile of
size*width x size*height at column x, row y of `file` in res.pak."""
import io
import json
import struct
from pathlib import Path

import imgcache
import pak_extract

CLASSES = ("Warrior", "Mage", "Rogue", "Priest")
BRANCHES = ("Root", "Left", "Center", "Right")
IMG_PX = 56


def build(game_dir, img_dir=None):
    game_dir = Path(game_dir)
    cdb = json.loads(pak_extract.read_entry(game_dir / "res.light.pak",
                                            "data.cdb"))
    sheets = {s["name"]: s for s in cdb["sheets"]}
    units = {ln["id"]: ln for ln in sheets["unit"]["lines"]
             if isinstance(ln.get("id"), str)}
    skills = {ln["id"]: ln for ln in sheets["skill"]["lines"]
              if isinstance(ln.get("id"), str)}

    trees = {}
    for cls in CLASSES:
        for tree in (units.get(cls) or {}).get("talentTrees") or ():
            talents = []
            for t in tree.get("talents") or ():
                sid = t.get("skill")
                props = (skills.get(sid) or {}).get("props") or {}
                talents.append({
                    "s": sid, "tier": int(t.get("tier") or 0),
                    "branch": BRANCHES[int(t.get("branch") or 0)],
                    "max": int((props.get("talent") or {}).get("maxPoints")
                               or 1)})
            trees[cls] = {"root": tree.get("root"), "talents": talents}

    # rune (mastery) -> the skill it sits on, and every icon to extract
    runes, gfx = {}, {}
    for sid, row in skills.items():
        if isinstance(row.get("gfx"), dict):
            gfx[sid] = row["gfx"]
        for m in row.get("mastery") or ():
            if isinstance(m, dict) and m.get("id"):
                runes[m["id"]] = sid
                if isinstance(m.get("gfx"), dict):
                    gfx[m["id"]] = m["gfx"]
    if img_dir is not None:
        _icons(game_dir, img_dir, gfx)
    return {"trees": trees, "runes": runes}


def _icons(game_dir, out_dir, gfx):
    from PIL import Image
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pak = game_dir / "res.pak"
    with open(pak, "rb") as f:
        header = struct.unpack_from("<i", f.read(12), 4)[0]
        f.seek(0)
        entries, off = pak_extract.read_tree(f.read(header), pak.name)
        by_path = {e.path: e for e in entries}
        sheets = {}
        for sid, g in gfx.items():
            e = by_path.get(g.get("file"))
            if e is None:
                continue
            if e.path not in sheets:
                f.seek(off + e.pos)
                try:
                    sheets[e.path] = Image.open(
                        io.BytesIO(f.read(e.size))).convert("RGBA")
                except Exception:
                    sheets[e.path] = None
            img = sheets[e.path]
            if img is None:
                continue
            n = int(g.get("size") or img.width)
            w, h = int(g.get("width") or 1) * n, int(g.get("height") or 1) * n
            x, y = int(g.get("x") or 0) * n, int(g.get("y") or 0) * n
            if x + w > img.width or y + h > img.height:
                continue
            imgcache.save(img.crop((x, y, x + w, y + h)).resize(
                (IMG_PX, IMG_PX), Image.LANCZOS),
                out_dir / f"{sid}.webp", quality=82, method=6)
