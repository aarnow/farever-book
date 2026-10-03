"""The game's 3D models, for the Collection's viewer.

A collectible names its model (item visuals.modelRef -> the `model` sheet,
whose row — or the row it inherits from — names a prefab). The prefab
(HBSON) names the model file and, material by material, the gradients that
colour it. The model file is HMD (Heaps' compiled model format, version 6 in
this build) even though it is named .fbx; its layout here is the one
hxd.fmt.hmd.Reader reads in the game's own bytecode.

The colours are the game's, as its GradMat shader (prefab.GradMatShader,
decoded from the HXSL in hlboot.dat) makes them: the prefab's gradmat lists
up to 8 gradients (the `gradient` sheet), U picks one (floor(u * 8)), V a
row of it. A gradient is 6 columns: albedo, shadow, terminator, specular,
emissive, outline. The light chooses between them — a cel-shaded ramp, plus
a hand-drawn line texture over the second UV set.

What leaves here is compact and JSON-ready, for a small WebGL viewer rather
than a full glTF stack: the vertices' positions, normals and two UV sets,
each material's triangles, its gradients side by side (6 px per slot) as a
PNG, and its shading values with the game's defaults filled in.

    py hltools/hmd_model.py Mount_Wolf_01      # writes analysis_out/models/
"""
import base64
import io
import json
import struct
from pathlib import Path

import hbson
import pak_extract

_CDB = {}
_DIRS = {}


def _read(pak, name):
    """One file out of a pak, its directory read once per pak (and again
    if the pak changes): a model takes twenty-odd reads of res.pak, whose
    directory alone is 700 KB."""
    pak = Path(pak)
    st = pak.stat()
    key = (str(pak), st.st_size, st.st_mtime)
    if key not in _DIRS:
        import contextlib
        with open(pak, "rb") as f:
            size = struct.unpack_from("<i", f.read(12), 4)[0]
            f.seek(0)
            with contextlib.redirect_stderr(io.StringIO()):
                entries, data_off = pak_extract.read_tree(f.read(size), pak.name)
        _DIRS[key] = ({e.path: (e.pos, e.size) for e in entries}, data_off)
    index, data_off = _DIRS[key]
    hit = index.get(name)
    if hit is None:
        return None
    with open(pak, "rb") as f:
        f.seek(data_off + hit[0])
        return f.read(hit[1])


def _sheets(game_dir):
    key = str(game_dir)
    if key not in _CDB:
        cdb = json.loads(_read(Path(game_dir) / "res.light.pak",
                                                "data.cdb"))
        sh = {s["name"]: s for s in cdb["sheets"]}
        _CDB[key] = {n: {r["id"]: r for r in sh[n].get("lines") or ()
                         if isinstance(r.get("id"), str)}
                     for n in ("item", "model", "gradient")}
    return _CDB[key]


# ---- HMD ------------------------------------------------------------------
class _R:
    def __init__(self, b):
        self.b, self.p = b, 0

    def u8(self):
        self.p += 1
        return self.b[self.p - 1]

    def u16(self):
        self.p += 2
        return struct.unpack_from("<H", self.b, self.p - 2)[0]

    def i32(self):
        self.p += 4
        return struct.unpack_from("<i", self.b, self.p - 4)[0]

    def f32(self):
        self.p += 4
        return struct.unpack_from("<f", self.b, self.p - 4)[0]

    def name(self):
        n = self.u8()
        if n == 255:
            return None
        self.p += n
        return self.b[self.p - n:self.p].decode("utf-8", "replace")


def _props(r, ver):
    if ver == 1:
        return []
    out = []
    for _ in range(r.u8()):
        k = r.u8()
        if k == 0:              # CameraFOVY(f)
            r.f32()
        elif k == 1:
            raise ValueError("obsolete HMD property HasMaterialFlags")
        out.append(k)
    return out


def _pos(r, scaled=True):
    v = [r.f32() for _ in range(6)]
    if scaled:
        v += [r.f32() for _ in range(3)]
    return v


def read_hmd(raw):
    """The header: geometries, materials, models (skins read past)."""
    r = _R(raw)
    if raw[:3] != b"HMD":
        raise ValueError("not an HMD file")
    r.p = 3
    ver = r.u8()
    if ver > 6:
        raise ValueError(f"HMD v{ver} is newer than this reader")
    d = {"version": ver, "dataPos": r.i32(), "geoms": [], "mats": [], "models": []}
    _props(r, ver)
    for _ in range(r.i32()):
        _props(r, ver)
        g = {"vertexCount": r.i32(), "stride": r.u8(), "format": []}
        for _ in range(r.u8()):
            nm = r.name()
            t = r.u8()
            g["format"].append((nm, t & 15))
        g["vertexPos"] = r.i32()
        n = r.u8()
        if n == 255:
            n = r.i32()
        g["indexCounts"] = [r.i32() for _ in range(n)]
        g["indexPos"] = r.i32()
        g["bounds"] = [r.f32() for _ in range(6)]
        d["geoms"].append(g)
    for _ in range(r.i32()):
        props = _props(r, ver)
        m = {"name": r.name(), "diffuse": r.name()}
        r.u8()                  # blend mode
        r.u8()
        r.f32()
        if 2 in props:          # HasExtraTextures
            r.name()
            r.name()
        d["mats"].append(m)
    for _ in range(r.i32()):
        props = _props(r, ver)
        m = {"name": r.name(), "parent": r.i32() - 1}
        r.name()                # follow
        m["pos"] = _pos(r)      # x y z, qx qy qz, sx sy sz
        m["geom"] = r.i32() - 1
        d["models"].append(m)
        if m["geom"] < 0:
            continue
        n = r.u8()
        if n == 255:
            n = r.i32()
        m["mats"] = [r.i32() for _ in range(n)]
        if r.name() is not None:            # the skin: its rest pose
            _props(r, ver)
            joints = []
            for _ in range(r.u16()):
                _props(r, ver)
                j = {"name": r.name()}
                p = r.u16()
                scaled = bool(p & 0x8000)
                j["parent"] = (p & 0x7FFF) - 1
                j["pos"] = _pos(r, scaled)
                j["bind"] = r.u16() - 1
                if j["bind"] >= 0:
                    j["trans"] = _pos(r, scaled)
                joints.append(j)
            splits = []
            for _ in range(r.u8()):
                mat = r.u8()
                splits.append((mat, [r.u16() for _ in range(r.u8())]))
            m["skin"] = {"joints": joints, "splits": splits}
        if 4 in props:
            for _ in range(r.i32()):
                r.i32()
        if 5 in props:
            r.i32()
        if 6 in props:
            for _ in range(r.i32()):
                r.i32()
    return d


# floats per vertex input, by InputFormat (DFloat..DVec4, DBytes4 = one word)
_WORDS = {1: 1, 2: 2, 3: 3, 4: 4, 9: 1}


def mesh(raw, d, geom=0):
    """One geometry's positions, normals, UVs and per-material triangles."""
    g = d["geoms"][geom]
    offs, o = {}, 0
    for nm, t in g["format"]:
        offs[nm] = o
        o += _WORDS.get(t, t & 7)
    if o != g["stride"]:
        raise ValueError(f"vertex stride {o} != {g['stride']}")
    n, st = g["vertexCount"], g["stride"]
    v = struct.unpack_from(f"<{n * st}f", raw, d["dataPos"] + g["vertexPos"])
    po, no, uo = offs["position"], offs.get("normal"), offs.get("uv")
    u2 = offs.get("uv2")
    pos = [v[i * st + po + k] for i in range(n) for k in range(3)]
    nor = ([v[i * st + no + k] for i in range(n) for k in range(3)]
           if no is not None else [0.0, 1.0, 0.0] * n)
    uv = ([v[i * st + uo + k] for i in range(n) for k in range(2)]
          if uo is not None else [0.0, 0.0] * n)
    uv2 = ([v[i * st + u2 + k] for i in range(n) for k in range(2)]
           if u2 is not None else [0.0, 0.0] * n)
    wo, xo = offs.get("weights"), offs.get("indexes")
    weights = idx4 = None
    if wo is not None and xo is not None:
        base = d["dataPos"] + g["vertexPos"]
        weights = [v[i * st + wo:i * st + wo + 3] for i in range(n)]
        idx4 = [raw[base + (i * st + xo) * 4:base + (i * st + xo) * 4 + 4]
                for i in range(n)]
    big = n > 0x10000
    ip, subs = d["dataPos"] + g["indexPos"], []
    for cnt in g["indexCounts"]:
        subs.append(struct.unpack_from(f"<{cnt}{'I' if big else 'H'}", raw, ip))
        ip += cnt * (4 if big else 2)
    return {"n": n, "pos": pos, "nor": nor, "uv": uv, "uv2": uv2, "subs": subs,
            "big": big, "weights": weights, "idx4": idx4,
            "bounds": g["bounds"]}


# ---- the skeleton's rest pose ---------------------------------------------
# Heaps' matrices take row vectors (v * M): a joint's absolute matrix is its
# own times its parent's, and what moves a vertex is transPos (the inverse
# bind) times that. Some models (the crawlers) are stored in centimetres and
# Z-up, and only their skeleton makes them the right size and way up.
def _mat(p):
    """A Position (x y z, qx qy qz, sx sy sz) as a 4x3 row-vector matrix."""
    x, y, z, qx, qy, qz = p[:6]
    sx, sy, sz = p[6:9] if len(p) >= 9 else (1.0, 1.0, 1.0)
    qw = 1.0 - (qx * qx + qy * qy + qz * qz)
    qw = qw ** 0.5 if qw > 0 else 0.0
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    r = [[1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy)],
         [2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx)],
         [2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy)]]
    return [[c * sx for c in r[0]], [c * sy for c in r[1]],
            [c * sz for c in r[2]], [x, y, z]]


def _mul(a, b):
    """a then b, both 4x3 row-vector matrices."""
    out = [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
           for i in range(3)]
    out.append([sum(a[3][k] * b[k][j] for k in range(3)) + b[3][j]
                for j in range(3)])
    return out


def rest_pose(m, sub_of_vertex, model, skinned=False):
    """Positions and normals put the right size and way up by the skeleton,
    in place — fully skinned to its default pose when `skinned`."""
    skin = model.get("skin")
    if not skin or m["weights"] is None:
        return
    joints = skin["joints"]
    absm = [None] * len(joints)
    palette = {}
    for i, j in enumerate(joints):
        loc = _mat(j["pos"])
        absm[i] = loc if j["parent"] < 0 else _mul(loc, absm[j["parent"]])
        if j["bind"] >= 0:
            palette[j["bind"]] = _mul(_mat(j["trans"]), absm[i])
    if not skinned:
        # the mesh as modelled, moved as a whole by the skeleton's root: the
        # default pose stretches limbs (a crab's eye on its stalk, its claws)
        depth = [0] * len(joints)
        for i, j in enumerate(joints):
            depth[i] = 0 if j["parent"] < 0 else depth[j["parent"]] + 1
        bound = [i for i, j in enumerate(joints) if j["bind"] >= 0]
        if not bound:
            return
        G = palette[joints[min(bound, key=lambda i: depth[i])]["bind"]]
        pos, nor = m["pos"], m["nor"]
        for i in range(0, len(pos), 3):
            px, py, pz = pos[i:i + 3]
            nx, ny, nz = nor[i:i + 3]
            pos[i:i + 3] = [px * G[0][c] + py * G[1][c] + pz * G[2][c] + G[3][c]
                            for c in range(3)]
            a = [nx * G[0][c] + ny * G[1][c] + nz * G[2][c] for c in range(3)]
            ln = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) ** 0.5 or 1.0
            nor[i:i + 3] = [a[0] / ln, a[1] / ln, a[2] / ln]
        return
    # with splits, a vertex's indexes count in its material's joint list
    by_mat = {mat: [joints[k]["bind"] for k in ids] for mat, ids in skin["splits"]}
    pos, nor = m["pos"], m["nor"]
    for i in range(m["n"]):
        w3 = m["weights"][i]
        ws = (w3[0], w3[1], w3[2], max(0.0, 1.0 - w3[0] - w3[1] - w3[2]))
        ids = m["idx4"][i]
        remap = by_mat.get(sub_of_vertex[i]) if by_mat else None
        px, py, pz = pos[i * 3:i * 3 + 3]
        nx, ny, nz = nor[i * 3:i * 3 + 3]
        ox = oy = oz = ax = ay = az = 0.0
        for k in range(4):
            w = ws[k]
            if w <= 0:
                continue
            b = ids[k]
            if remap is not None:
                b = remap[b] if b < len(remap) else -1
            M = palette.get(b)
            if M is None:
                continue
            ox += w * (px * M[0][0] + py * M[1][0] + pz * M[2][0] + M[3][0])
            oy += w * (px * M[0][1] + py * M[1][1] + pz * M[2][1] + M[3][1])
            oz += w * (px * M[0][2] + py * M[1][2] + pz * M[2][2] + M[3][2])
            ax += w * (nx * M[0][0] + ny * M[1][0] + nz * M[2][0])
            ay += w * (nx * M[0][1] + ny * M[1][1] + nz * M[2][1])
            az += w * (nx * M[0][2] + ny * M[1][2] + nz * M[2][2])
        if ox or oy or oz:
            pos[i * 3:i * 3 + 3] = (ox, oy, oz)
        ln = (ax * ax + ay * ay + az * az) ** 0.5
        if ln > 1e-9:
            nor[i * 3:i * 3 + 3] = (ax / ln, ay / ln, az / ln)


# ---- prefab, model sheet, palettes ----------------------------------------
def _walk(o, fn):
    if isinstance(o, dict):
        fn(o)
        for v in o.values():
            _walk(v, fn)
    elif isinstance(o, list):
        for v in o:
            _walk(v, fn)


def prefab_parts(game_dir, prefab, depth=0):
    """(model file, {material name: gradmat {slots, props}}) of a prefab,
    following a reference to another prefab when the model sits there."""
    raw = _read(Path(game_dir) / "res.pak", prefab)
    if not raw:
        return None, {}
    tree = hbson.loads(raw)
    found = {"model": None, "mats": {}, "refs": []}

    def gradmat(c):
        # its settings sit beside the slots, or (older prefabs) under props
        props = {k: v for k, v in c.items() if isinstance(v, (int, float))}
        props.update(c.get("props") or {})
        return {"slots": c.get("slots") or [], "props": props,
                "offsets": c.get("slotsOffsets") or [],
                "max": int(c.get("numSlots") or 8),
                "pattern": c.get("patternPath"), "alpha": c.get("patternAlphaPath")}

    def visit(o):
        t = o.get("type")
        kids = o.get("children") or ()
        if t == "model" and o.get("source") and not found["model"]:
            found["model"] = o["source"]
            # a gradmat on the model itself colours all its materials
            for c in kids:
                if c.get("type") == "gradmat":
                    found["mats"]["*"] = gradmat(c)
        elif t == "material":
            name = o.get("materialName") or o.get("name")
            for c in kids:
                if c.get("type") == "gradmat":
                    found["mats"][name] = gradmat(c)
            if name not in found["mats"] and isinstance(o.get("color"), list):
                found["mats"][name] = {"color": o["color"]}
        elif t == "reference" and str(o.get("source", "")).endswith(".prefab"):
            found["refs"].append(o["source"])
    _walk(tree, visit)
    if not found["model"] and depth < 3:
        for ref in found["refs"]:
            m, mats = prefab_parts(game_dir, ref, depth + 1)
            if m:
                return m, {**mats, **found["mats"]}
    return found["model"], found["mats"]


def item_prefab(game_dir, item_id):
    """The prefab an item's model comes from, through the model sheet."""
    sh = _sheets(game_dir)
    it = sh["item"].get(item_id) or {}
    vis = it.get("visuals") or {}
    if vis.get("modelPath", "").endswith(".prefab"):
        return vis["modelPath"]
    ref, seen = vis.get("modelRef"), set()
    while ref and ref not in seen:
        seen.add(ref)
        row = sh["model"].get(ref) or {}
        if row.get("prefab"):
            return row["prefab"]
        ref = row.get("inherit")
    return None


# prefab.GradMat's own defaults (its constructor), for what a prefab leaves out
GRADMAT_DEFAULTS = {
    "linesBlend": 1.0, "shadowBias": 0.0, "shadowSmooth": 0.002,
    "lightSmooth": 0.1, "terminatorSize": 0.1, "specSize": 0.03,
    "specInsideSize": 0.35, "specInsideIntensity": 0.0, "specSmooth": 0.002,
    "specAlpha": 1.0, "emissivePow": 0.0, "rimLightAngle": 30.0,
    "rimLightWidth": 2.0, "rimLightSize": 0.1, "rimLightSmooth": 0.1,
    "rimLightMin": 0.5, "rimLightSpecMultiplier": 1.0, "ambientIntensity": 1.0,
}
LINES_TEX = "Character/Common/Texture/UV_Lines.png"


def shading(gradmat):
    """The shader's values, as syncShaderVars hands them over."""
    sh = dict(GRADMAT_DEFAULTS)
    sh.update({k: v for k, v in ((gradmat or {}).get("props") or {}).items()
               if k in GRADMAT_DEFAULTS and isinstance(v, (int, float))})
    sh["rimLightSize"] *= 0.1
    sh["rimLightSmooth"] *= 0.1
    return sh


def _png(im):
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def gradients_png(game_dir, slots, color=None):
    """A material's gradients side by side: slot i's six columns (each 8 px
    wide in the game's 48 x 256 file) at x = 6i .. 6i + 5. A material with
    a plain colour instead gets one slot of it."""
    from PIL import Image
    res = Path(game_dir) / "res.pak"
    grads = _sheets(game_dir)["gradient"]
    slots = list(slots or [])[:16] or [None]
    out = Image.new("RGB", (6 * len(slots), 256), (150, 150, 150))
    if color and not any(slots):
        rgb = tuple(max(0, min(255, round(float(x) * 255))) for x in color[:3])
        for c, col in enumerate((rgb, (190, 190, 190), (128, 128, 128),
                                 (0, 0, 0), (0, 0, 0), (128, 128, 128))):
            out.paste(col, (c, 0, c + 1, 256))
        return _png(out), 1
    for i, gid in enumerate(slots):
        g = grads.get(gid) if gid else None
        raw = _read(res, g["ref"]["file"]) if g else None
        if not raw:
            continue
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.size != (48, 256):
            im = im.resize((48, 256))
        for c in range(6):
            out.paste(im.crop((c * 8 + 4, 0, c * 8 + 5, 256)), (i * 6 + c, 0))
    return _png(out), len(slots)


def texture_png(game_dir, path):
    """Any game texture (PNG or DDS), as a PNG data URI for the browser."""
    from PIL import Image
    raw = _read(Path(game_dir) / "res.pak", path) if path else None
    if not raw:
        return None
    try:
        return _png(Image.open(io.BytesIO(raw)).convert("RGBA"))
    except Exception:
        return None


def lines_png(game_dir):
    """The hand-drawn lines laid over every gradmat (a DDS, despite the
    name: re-encoded, for the browser)."""
    return texture_png(game_dir, LINES_TEX)


def _b64(fmt, values):
    return base64.b64encode(struct.pack(f"<{len(values)}{fmt}", *values)).decode()


def item_model(game_dir, item_id):
    """The viewer's payload for one collectible, or None when it has no
    model this reader can make sense of."""
    prefab = item_prefab(game_dir, item_id)
    if not prefab:
        return None
    src, gradmats = prefab_parts(game_dir, prefab)
    if not src:
        return None
    raw = _read(Path(game_dir) / "res.pak", src)
    if not raw or raw[:3] != b"HMD":
        return None
    d = read_hmd(raw)
    model = next((m for m in d["models"] if m["geom"] >= 0), None)
    if model is None:
        return None
    m = mesh(raw, d, model["geom"])
    m["pos"], m["nor"] = list(m["pos"]), list(m["nor"])
    sub_of = [0] * m["n"]
    for k, tris in enumerate(m["subs"]):
        mi = model["mats"][k] if k < len(model["mats"]) else 0
        for t in tris:
            sub_of[t] = mi
    rest_pose(m, sub_of, model)
    # Heaps is Z-up; the viewer, like WebGL's habits, Y-up
    for a in (m["pos"], m["nor"]):
        for i in range(0, len(a), 3):
            a[i + 1], a[i + 2] = a[i + 2], -a[i + 1]
    parts = []
    for k, tris in enumerate(m["subs"]):
        mi = model["mats"][k] if k < len(model["mats"]) else 0
        mat = d["mats"][mi] if mi < len(d["mats"]) else {}
        gm = gradmats.get(mat.get("name")) or gradmats.get("*") or {}
        grad, nslots = gradients_png(game_dir, gm.get("slots"), gm.get("color"))
        offs = [float(x or 0) for x in (gm.get("offsets") or [])][:nslots]
        parts.append({"idx": _b64("I" if m["big"] else "H", tris),
                      "grad": grad, "slots": nslots,
                      "max": 1 if gm.get("color") else gm.get("max", 8),
                      "offs": offs + [0.0] * (nslots - len(offs)),
                      "pattern": texture_png(game_dir, gm.get("pattern")),
                      "alpha": texture_png(game_dir, gm.get("alpha")),
                      "shade": shading(gm)})
    # normals as signed bytes: a third of the size, and plenty for shading
    nor = [max(-127, min(127, round(x * 127))) for x in m["nor"]]
    return {"id": item_id, "n": m["n"], "big": m["big"],
            "pos": _b64("f", m["pos"]), "nor": _b64("b", nor),
            "uv": _b64("f", m["uv"]), "uv2": _b64("f", m["uv2"]),
            "lines": lines_png(game_dir), "parts": parts}


if __name__ == "__main__":
    import os
    import sys
    from gamepath import find_hlboot
    ids = [a for a in sys.argv[1:] if not a.endswith(".dat")]
    sys.argv = sys.argv[:1]
    game = Path(find_hlboot()).parent
    out = Path(os.environ.get("FAREVER_ANALYSIS_OUT")
               or Path(__file__).resolve().parent.parent / "analysis_out") / "models"
    out.mkdir(parents=True, exist_ok=True)
    for iid in ids:
        p = item_model(game, iid)
        if p:
            (out / f"{iid}.json").write_text(json.dumps(p), encoding="utf-8")
        print(iid, "ok" if p else "no model")
