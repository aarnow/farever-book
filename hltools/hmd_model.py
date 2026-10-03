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
import math
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
                     for n in ("item", "model", "gradient", "unit")}
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
            g["format"].append((nm, t & 15, t >> 4))
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
    d["anims"] = []
    for _ in range(r.i32()):
        _props(r, ver)
        a = {"name": r.name(), "frames": r.i32(), "sampling": r.f32(),
             "speed": r.f32()}
        flags = r.u8()
        a["loop"] = bool(flags & 1)
        a["dataPos"] = r.i32()
        a["objects"] = []
        for _ in range(r.i32()):
            o = {"name": r.name(), "flags": r.u8()}
            if o["flags"] & 64:                  # HasProps
                o["props"] = [r.name() for _ in range(r.u8())]
            a["objects"].append(o)
        if flags & 2:                            # events
            for _ in range(r.i32()):
                r.i32()
                r.name()
        d["anims"].append(a)
    return d


def anim_frames(raw, d, a):
    """One animation's joint poses: [{joint name: (pos, quat xyz, scale)}]
    per frame, as h3d's BufferAnimation lays them out — the objects with a
    single frame first, then a stride per frame for the others."""
    def size(o):
        f = o["flags"]
        return (3 * bool(f & 1) + 3 * bool(f & 2) + 3 * bool(f & 4) + 2 * bool(f & 8)
                + bool(f & 16) + (len(o.get("props") or ()) if f & 64 else 0))
    singles = [o for o in a["objects"] if o["flags"] & 32]
    others = [o for o in a["objects"] if not o["flags"] & 32]
    single = sum(size(o) for o in singles)
    stride = sum(size(o) for o in others)
    count = single + stride * a["frames"]
    v = struct.unpack_from(f"<{count}f", raw, d["dataPos"] + a["dataPos"])

    def pose(o, off):
        f, out = o["flags"], {}
        if f & 1:
            out["pos"] = v[off:off + 3]
            off += 3
        if f & 2:
            out["rot"] = v[off:off + 3]
            off += 3
        if f & 4:
            out["scale"] = v[off:off + 3]
        return out
    base, off = {}, 0
    for o in singles:
        base[o["name"]] = pose(o, off)
        off += size(o)
    frames = []
    for fr in range(a["frames"]):
        cur, off = dict(base), single + stride * fr
        for o in others:
            cur[o["name"]] = pose(o, off)
            off += size(o)
        frames.append(cur)
    return frames


# floats per vertex input, by InputFormat (DFloat..DVec4, DBytes4 = one word)
_WORDS = {1: 1, 2: 2, 3: 3, 4: 4, 9: 1}
# its precision (hxd.BufferFormat's InputPrecision): struct code, bytes,
# scale. Below F32 an input is padded to a whole 4-byte word.
_PREC = {0: ("f", 4, 1.0), 1: ("e", 2, 1.0), 2: ("B", 1, 1 / 255), 3: ("b", 1, 1 / 127)}


def mesh(raw, d, geom=0):
    """One geometry's positions, normals, UVs and per-material triangles."""
    g = d["geoms"][geom]
    offs, o, words = {}, 0, 0
    for nm, t, prec in g["format"]:
        k = _WORDS.get(t, t & 7)
        words += k
        if t == 9:                       # four bytes, whatever the precision
            offs[nm] = (o, k, "4s", 1.0)
            o += 4
            continue
        code, size, scale = _PREC.get(prec, _PREC[0])
        offs[nm] = (o, k, code, scale)
        o += (k * size + 3) // 4 * 4
    if words != g["stride"]:
        raise ValueError(f"vertex stride {words} != {g['stride']}")
    n, bstride = g["vertexCount"], o
    base = d["dataPos"] + g["vertexPos"]

    def column(name, k, default):
        if name not in offs:
            return list(default) * n
        off, cnt, code, scale = offs[name]
        cnt = min(cnt, k)
        fmt = struct.Struct(f"<{cnt}{code}")
        out = []
        for i in range(n):
            vals = fmt.unpack_from(raw, base + i * bstride + off)
            out.extend(x * scale for x in vals) if scale != 1.0 else out.extend(vals)
        return out

    pos = column("position", 3, (0.0, 0.0, 0.0))
    nor = column("normal", 3, (0.0, 1.0, 0.0))
    uv = column("uv", 2, (0.0, 0.0))
    uv2 = column("uv2", 2, (0.0, 0.0))
    weights = idx4 = None
    if "weights" in offs and "indexes" in offs:
        w = column("weights", 3, (0.0, 0.0, 0.0))
        weights = [w[i * 3:i * 3 + 3] for i in range(n)]
        xo = offs["indexes"][0]
        idx4 = [raw[base + i * bstride + xo:base + i * bstride + xo + 4]
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


def _mat_post(p):
    """The same, as Position.toMatrix(true) builds a joint's bind matrix
    (transPos): rotated, translated, and only then scaled — the scale
    reaches the translation too. A model in centimetres (the crawlers) has
    its bind matrices scaled, and gets this wrong otherwise."""
    m = _mat(list(p[:6]) + [1.0, 1.0, 1.0])
    s = p[6:9] if len(p) >= 9 else (1.0, 1.0, 1.0)
    return [[row[c] * s[c] for c in range(3)] for row in m]


def _mul(a, b):
    """a then b, both 4x3 row-vector matrices."""
    out = [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
           for i in range(3)]
    out.append([sum(a[3][k] * b[k][j] for k in range(3)) + b[3][j]
                for j in range(3)])
    return out


def rest_pose(m, sub_of_vertex, model, skinned=False):
    """Positions and normals put the right size and way up by the skeleton,
    in place — fully skinned to its default pose when `skinned`. Returns
    the root's matrix it moved them by (None when there is no skin)."""
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
            palette[j["bind"]] = _mul(_mat_post(j["trans"]), absm[i])
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
        return G
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


# ---- the idle animation ----------------------------------------------------
# The viewer is sent the mesh already moved by the root (G), the prefab (N)
# and turned Y-up (S). An animated frame moves a vertex by its joints'
# matrices P instead of G; what the viewer applies on top of what it has is
# therefore C = S^-1 N^-1 G^-1 P N S, per joint, per frame.
_SWAP = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]]
ANIM_FPS = 15


def _inv(m):
    """Inverse of a 4x3 row-vector affine matrix."""
    a, b, c = m[0], m[1], m[2]
    det = (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
           + a[2] * (b[0] * c[1] - b[1] * c[0]))
    if abs(det) < 1e-20:
        return _IDENTITY
    r = [[(b[1] * c[2] - b[2] * c[1]) / det, (a[2] * c[1] - a[1] * c[2]) / det,
          (a[1] * b[2] - a[2] * b[1]) / det],
         [(b[2] * c[0] - b[0] * c[2]) / det, (a[0] * c[2] - a[2] * c[0]) / det,
          (a[2] * b[0] - a[0] * b[2]) / det],
         [(b[0] * c[1] - b[1] * c[0]) / det, (a[1] * c[0] - a[0] * c[1]) / det,
          (a[0] * b[1] - a[1] * b[0]) / det]]
    t = m[3]
    r.append([-(t[0] * r[0][k] + t[1] * r[1][k] + t[2] * r[2][k]) for k in range(3)])
    return r


def _pose_matrix(j, pose, keep_pos=True):
    """A joint's local matrix in one frame: what the animation says of it,
    else its default (h3d: no rotation in the frame is no rotation). Without
    `keep_pos`, the joint keeps its own length: an animation shared by a rig
    (the crawlers') was made for other proportions than every model on it."""
    if pose is None:
        return _mat(j["pos"])
    p = (pose.get("pos") if keep_pos else None) or j["pos"][:3]
    q = pose.get("rot") or (0.0, 0.0, 0.0)
    sc = pose.get("scale") or (1.0, 1.0, 1.0)
    return _mat([p[0], p[1], p[2], q[0], q[1], q[2], sc[0], sc[1], sc[2]])


def skin_animation(model, m, sub_of_vertex, frames, G, N):
    """(per-vertex 4 joint slots + 4 weights, [frame][slot] C matrices) for a
    skinned model and an animation's frames; None if they share no joint."""
    skin = model.get("skin")
    if not skin or m["weights"] is None or G is None:
        return None
    joints = skin["joints"]
    if not any(j["name"] in frames[0] for j in joints):
        return None
    binds = sorted({j["bind"] for j in joints if j["bind"] >= 0})
    slot = {b: k for k, b in enumerate(binds)}
    pre = _mul(_inv(_SWAP), _mul(_inv(N), _inv(G)))
    post = _mul(N, _SWAP)
    mats = []
    for fr in frames:
        absm, row = [None] * len(joints), [None] * len(binds)
        for i, j in enumerate(joints):
            loc = _pose_matrix(j, fr.get(j["name"]))
            absm[i] = loc if j["parent"] < 0 else _mul(loc, absm[j["parent"]])
            if j["bind"] >= 0:
                P = _mul(_mat_post(j["trans"]), absm[i])
                row[slot[j["bind"]]] = _mul(pre, _mul(P, post))
        mats.append(row)
    by_mat = {mat: [joints[k]["bind"] for k in ids] for mat, ids in skin["splits"]}
    jv, wv = bytearray(), bytearray()
    for i in range(m["n"]):
        w3 = m["weights"][i]
        ws = [w3[0], w3[1], w3[2], max(0.0, 1.0 - w3[0] - w3[1] - w3[2])]
        remap = by_mat.get(sub_of_vertex[i]) if by_mat else None
        for k in range(4):
            b = m["idx4"][i][k]
            if remap is not None:
                b = remap[b] if b < len(remap) else -1
            jv.append(slot.get(b, 0))
            wv.append(max(0, min(255, round(ws[k] * 255))) if b in slot else 0)
    return jv, wv, mats


def item_rig(game_dir, item_id):
    """The skeleton a unit's (or a mount's) model is animated with (the
    model sheet's rigName, inherited), or None."""
    sh = _sheets(game_dir)
    unit = sh["unit"].get(item_id)
    # a mount names its model row straight from the item
    ref = ((sh["item"].get(item_id) or {}).get("visuals") or {}).get("modelRef")
    if not unit and not ref:
        return None
    todo, done = ([] if ref else [item_id]), set()
    while todo and not ref:
        uid = todo.pop(0)
        if uid in done:
            continue
        done.add(uid)
        u = sh["unit"].get(uid) or {}
        ref = next((x.get("ref") for x in u.get("models") or () if x.get("ref")), None)
        todo += [i.get("ref") for i in u.get("inherit") or () if i.get("ref")]
    seen = set()
    while ref and ref not in seen:
        seen.add(ref)
        row = sh["model"].get(ref) or {}
        if row.get("rigName"):
            return row["rigName"]
        ref = row.get("inherit")
    return None


def idle_frames(game_dir, rig, names=("Idle",)):
    """The rig's idle animation, sampled at ANIM_FPS: (frames, fps), or None.
    `names`, in order of preference: a mount's own idle (MountIdle) first."""
    if not rig:
        return None
    res = Path(game_dir) / "res.pak"
    _read(res, "")                       # the pak's directory, once
    index = next((v[0] for k, v in _DIRS.items() if k[0] == str(res)), {})
    path = None
    for nm in names:
        # some rigs keep theirs a folder down (Anim/Human/Common/...)
        path = next((p for p in sorted(index) if p.startswith(f"Anim/{rig}/")
                     and p.endswith(f"/Anim_{rig}_Common_{nm}.fbx")), None)
        if path:
            break
    if not path:
        return None
    raw = _read(res, path)
    if not raw or raw[:3] != b"HMD":
        return None
    d = read_hmd(raw)
    if not d["anims"]:
        return None
    a = d["anims"][0]
    frames = anim_frames(raw, d, a)
    step = max(1, round((a["sampling"] or 30) / ANIM_FPS))
    return frames[::step], (a["sampling"] or 30) / step * (a["speed"] or 1)


# ---- prefab, model sheet, palettes ----------------------------------------
def _walk(o, fn):
    if isinstance(o, dict):
        fn(o)
        for v in o.values():
            _walk(v, fn)
    elif isinstance(o, list):
        for v in o:
            _walk(v, fn)


def _gradmat(c):
    # its settings sit beside the slots, or (older prefabs) under props
    props = {k: v for k, v in c.items() if isinstance(v, (int, float))}
    props.update(c.get("props") or {})
    return {"slots": c.get("slots") or [], "props": props,
            "offsets": c.get("slotsOffsets") or [],
            "max": int(c.get("numSlots") or 8),
            "pattern": c.get("patternPath"), "alpha": c.get("patternAlphaPath")}


def _material(o, mats):
    """One prefab material into `mats`, by the name the model knows it by."""
    kids = o.get("children") or ()
    name = o.get("materialName") or o.get("name")
    pbr = (o.get("props") or {}).get("PBR") or {}
    for c in kids:
        if c.get("type") == "gradmat":
            mats[name] = _gradmat(c)
    if name not in mats and isinstance(o.get("color"), list):
        # glass: black underneath, its colour in the Fresnel rim or the toon
        # highlight
        shaders = {str(c.get("source", "")).rsplit("/", 1)[-1]: c.get("props") or {}
                   for c in kids if c.get("type") == "shader"}
        fr = shaders.get("Fresnel.hx") or {}
        color = next((c for c in (o["color"], fr.get("color"),
                                  (shaders.get("ToonSpecular.hx") or {}).get("color"))
                      if isinstance(c, list) and any(c[:3])), o["color"])
        entry = {"color": color}
        if fr:
            # drawn over the body it duplicates: only its rim shows
            entry["glass"] = [float(fr.get(k) or 0) for k in ("bias", "scale", "power")]
        mats[name] = entry
    if name in mats:
        # which faces it shows, and how it lays over what is behind
        mats[name]["cull"] = pbr.get("culling") or "Back"
        mats[name]["blend"] = pbr.get("blend") or "None"


def _local(o):
    """A prefab object's own transform (hrt Object3D: x y z, rotations in
    degrees as h3d's Quat.initRotation takes them, scales) as a matrix."""
    ax, ay, az = (math.radians(float(o.get(k) or 0))
                  for k in ("rotationX", "rotationY", "rotationZ"))
    sx, cx = math.sin(ax / 2), math.cos(ax / 2)
    sy, cy = math.sin(ay / 2), math.cos(ay / 2)
    sz, cz = math.sin(az / 2), math.cos(az / 2)
    qx = sx * cy * cz - cx * sy * sz
    qy = cx * sy * cz + sx * cy * sz
    qz = cx * cy * sz - sx * sy * cz
    qw = cx * cy * cz + sx * sy * sz
    if qw < 0:
        qx, qy, qz = -qx, -qy, -qz
    sc = float(o.get("scale") or 1)
    return _mat([float(o.get("x") or 0), float(o.get("y") or 0), float(o.get("z") or 0),
                 qx, qy, qz,
                 float(o.get("scaleX") or 1) * sc, float(o.get("scaleY") or 1) * sc,
                 float(o.get("scaleZ") or 1) * sc])


_IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]]


def prefab_models(game_dir, prefab, depth=0):
    """Every model a prefab is made of: [{source, mats, matrix}] — a boss is
    a body, an abdomen and its eyes. Each with its own materials and its
    place in the prefab. A model hung on a bone of another (a constraint:
    eyes on a head) is left out, its place being where that bone is."""
    raw = _read(Path(game_dir) / "res.pak", prefab)
    if not raw:
        return []
    tree = hbson.loads(raw)
    hung = set()
    _walk(tree, lambda o: hung.add(o.get("object")) if o.get("type") == "constraint" else None)
    out, refs = [], []

    def walk(o, parent, path):
        t = o.get("type")
        here = ".".join(x for x in (path, o.get("name") or "") if x) if t != "prefab" else ""
        m = _mul(_local(o), parent) if t not in ("prefab", None) else parent
        if here in hung:
            return
        if t == "model" and o.get("source"):
            mats = {}
            for c in o.get("children") or ():
                if c.get("type") == "gradmat":
                    # on the model itself, it colours all its materials
                    mats["*"] = _gradmat(c)
                elif c.get("type") == "material":
                    _material(c, mats)
            out.append({"source": o["source"], "mats": mats, "matrix": m})
        elif t == "reference" and str(o.get("source", "")).endswith(".prefab"):
            refs.append((o["source"], m))
        for c in o.get("children") or ():
            if c.get("type") != "material":
                walk(c, m, here)
    walk(tree, _IDENTITY, "")
    if not out and depth < 3:
        for ref, m in refs:
            got = prefab_models(game_dir, ref, depth + 1)
            if got:
                return [dict(g, matrix=_mul(g["matrix"], m)) for g in got]
    return out


def prefab_parts(game_dir, prefab, depth=0):
    """(first model file, {material name: gradmat}) of a prefab — the
    materials of all its models."""
    models = prefab_models(game_dir, prefab, depth)
    mats = {}
    for mdl in reversed(models):
        mats.update(mdl["mats"])
    return (models[0]["source"] if models else None), mats


def item_prefab(game_dir, item_id):
    """The prefab an item's (or a companion's) model comes from, through
    the model sheet."""
    sh = _sheets(game_dir)
    it = sh["item"].get(item_id) or {}
    vis = it.get("visuals") or {}
    if vis.get("modelPath", "").endswith(".prefab"):
        return vis["modelPath"]
    # gliders (and gear worn on the hero) list their prefabs directly
    for mdl in vis.get("models") or ():
        if str((mdl or {}).get("prefab", "")).endswith(".prefab"):
            return mdl["prefab"]
    ref, seen = vis.get("modelRef"), set()
    if not it:
        # a companion or a monster is a unit, not an item: its first model
        # names the row — its own, or one it inherits
        todo, done = [item_id], set()
        while todo and not ref:
            uid = todo.pop(0)
            if uid in done:
                continue
            done.add(uid)
            unit = sh["unit"].get(uid) or {}
            ref = next((m.get("ref") for m in unit.get("models") or ()
                        if m.get("ref")), None)
            todo += [i.get("ref") for i in unit.get("inherit") or () if i.get("ref")]
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
    if (gradmat or {}).get("color"):
        sh["linesBlend"] = 0.0      # not a gradmat: no hand-drawn lines
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


def _model_parts(game_dir, raw, gradmats, matrix, frames=None):
    """One model file's mesh, in the prefab's space (Z-up), its parts and,
    given an animation's frames, how they move it: (mesh, [(triangles,
    material)], skin_animation or None); None when it has no geometry."""
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
    G = rest_pose(m, sub_of, model)
    anim = (skin_animation(model, m, sub_of, frames, G, matrix)
            if frames else None)
    if matrix is not _IDENTITY:
        M = matrix
        pos, nor = m["pos"], m["nor"]
        for i in range(0, len(pos), 3):
            px, py, pz = pos[i:i + 3]
            nx, ny, nz = nor[i:i + 3]
            pos[i:i + 3] = [px * M[0][c] + py * M[1][c] + pz * M[2][c] + M[3][c]
                            for c in range(3)]
            v = [nx * M[0][c] + ny * M[1][c] + nz * M[2][c] for c in range(3)]
            ln = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5 or 1.0
            nor[i:i + 3] = [v[0] / ln, v[1] / ln, v[2] / ln]
    # a material the prefab names differently from the model (Gradmat1 in
    # the model, Gradmat_12Slots_002 in the prefab) takes the prefab's main
    # gradmat left unclaimed: the one with the most slots
    names = {mm.get("name") for mm in d["mats"]}
    spare = sorted((v for k, v in gradmats.items()
                    if k not in names and k != "*" and v.get("slots")),
                   key=lambda v: -len(v["slots"]))
    parts = []
    for k, tris in enumerate(m["subs"]):
        mi = model["mats"][k] if k < len(model["mats"]) else 0
        mat = d["mats"][mi] if mi < len(d["mats"]) else {}
        if str(mat.get("name", "")).lower() == "outline":
            # the inverted hull that draws the cartoon outline, meant to be
            # seen from inside only: drawn plainly, it hides the object
            continue
        gm = (gradmats.get(mat.get("name")) or gradmats.get("*")
              or (spare[0] if spare else {}))
        parts.append((tris, gm))
    return m, parts, anim


def item_model(game_dir, item_id, anim=False):
    """The viewer's payload for one collectible (or monster), or None when
    it has no model this reader can make sense of. A prefab of several
    models comes as one mesh. With `anim`, a unit's idle animation too."""
    prefab = item_prefab(game_dir, item_id)
    if not prefab:
        return None
    mount = (_sheets(game_dir)["item"].get(item_id) or {}).get("type") == "Mount"
    idle = (idle_frames(game_dir, item_rig(game_dir, item_id),
                        ("MountIdle", "Idle") if mount else ("Idle",))
            if anim else None)
    frames = idle[0] if idle else None
    pos, nor, uv, uv2, groups = [], [], [], [], []
    jv, wv, cols, spans = bytearray(), bytearray(), [], []
    for mdl in prefab_models(game_dir, prefab):
        raw = _read(Path(game_dir) / "res.pak", mdl["source"])
        if not raw or raw[:3] != b"HMD":
            continue
        got = _model_parts(game_dir, raw, mdl["mats"], mdl["matrix"], frames)
        if not got:
            continue
        m, parts, am = got
        if frames:
            # each model's joints after the ones before; a model the
            # animation doesn't move hangs on one still joint
            first = len(cols)
            if am:
                jl, wl, mats = am
                cols.append(mats)
                jv += bytes(first_slot + j for first_slot, j in
                            ((sum(len(c[0]) for c in cols[:-1]), j) for j in jl))
                wv += wl
            else:
                cols.append([[_IDENTITY]] * len(frames))
                base_slot = sum(len(c[0]) for c in cols[:-1])
                jv += bytes([base_slot, 0, 0, 0] * m["n"])
                wv += bytes([255, 0, 0, 0] * m["n"])
            spans.append(first)
        base = len(pos) // 3
        pos += m["pos"]
        nor += m["nor"]
        uv += m["uv"]
        uv2 += m["uv2"]
        groups += [([t + base for t in tris], gm) for tris, gm in parts]
    n = len(pos) // 3
    if not n:
        return None
    # Heaps is Z-up; the viewer, like WebGL's habits, Y-up
    for a in (pos, nor):
        for i in range(0, len(a), 3):
            a[i + 1], a[i + 2] = a[i + 2], -a[i + 1]
    big = n > 0x10000
    out = []
    for tris, gm in groups:
        grad, nslots = gradients_png(game_dir, gm.get("slots"), gm.get("color"))
        offs = [float(x or 0) for x in (gm.get("offsets") or [])][:nslots]
        out.append({"idx": _b64("I" if big else "H", tris),
                    "grad": grad, "slots": nslots,
                    "max": 1 if gm.get("color") else gm.get("max", 8),
                    "offs": offs + [0.0] * (nslots - len(offs)),
                    "pattern": texture_png(game_dir, gm.get("pattern")),
                    "alpha": texture_png(game_dir, gm.get("alpha")),
                    "shade": shading(gm),
                    "glass": gm.get("glass"),
                    "cull": gm.get("cull", "Back"), "blend": gm.get("blend", "None")})
    # normals as signed bytes: a third of the size, and plenty for shading
    nb = [max(-127, min(127, round(x * 127))) for x in nor]
    payload = {"id": item_id, "n": n, "big": big,
               "pos": _b64("f", pos), "nor": _b64("b", nb),
               "uv": _b64("f", uv), "uv2": _b64("f", uv2),
               "lines": lines_png(game_dir), "parts": out}
    if frames and any(any(c is not _IDENTITY for c in col[0]) for col in cols):
        nj = sum(len(c[0]) for c in cols)
        if nj <= 255:
            # [frame][joint] three columns of four: (m0c, m1c, m2c, m3c)
            flat = []
            for f in range(len(frames)):
                for col in cols:
                    for M in col[f]:
                        for c in range(3):
                            flat += (M[0][c], M[1][c], M[2][c], M[3][c])
            allm = [[M for col in cols for M in col[f]] for f in range(len(frames))]
            sample = list(range(0, n, max(1, n // 6000)))

            def skinned(f):
                """Where frame f puts the sampled vertices."""
                out = {}
                for i in sample:
                    P = pos[i * 3:i * 3 + 3]
                    acc, tw = [0.0, 0.0, 0.0], 0.0
                    for k in range(4):
                        w = wv[i * 4 + k] / 255
                        if w:
                            M = allm[f][jv[i * 4 + k]]
                            for c in range(3):
                                acc[c] += w * (P[0] * M[0][c] + P[1] * M[1][c]
                                               + P[2] * M[2][c] + M[3][c])
                            tw += w
                    out[i] = [x / tw for x in acc] if tw else P
                return out
            # where the first frame puts the vertices: the framing follows
            # the pose (a crawler's legs spread wider than they were modelled)
            sk = skinned(0)
            mn = [min(v[c] for v in sk.values()) for c in range(3)]
            mx = [max(v[c] for v in sk.values()) for c in range(3)]
            payload["anim"] = {"fps": idle[1], "frames": len(frames), "joints": nj,
                               "box": mn + mx,
                               "pal": _b64("f", flat),
                               "j": base64.b64encode(bytes(jv)).decode(),
                               "w": base64.b64encode(bytes(wv)).decode()}
    return payload


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
