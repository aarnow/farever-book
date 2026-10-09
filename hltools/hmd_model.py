"""The game's 3D models, for the Collection's viewer.

An item's visuals.modelRef -> the `model` sheet (row or inherited row) ->
a prefab (HBSON), which names the model file and each material's gradients.
The model file is HMD (Heaps' compiled format, v6 here) despite its .fbx
name; layout as hxd.fmt.hmd.Reader reads it in the game's bytecode.

Colours follow the game's GradMat shader (prefab.GradMatShader): up to 8
gradients per gradmat, U picks one (floor(u * 8)), V a row of it. A gradient
is 6 columns: albedo, shadow, terminator, specular, emissive, outline; the
light picks between them (cel-shaded), plus a line texture on the second UV.

The output is compact JSON for a small WebGL viewer: positions, normals, two
UV sets, per-material triangles, gradients as a PNG (6 px per slot) and the
shading values with the game's defaults filled in.

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
    """One file out of a pak, its directory cached per pak (res.pak's alone
    is 700 KB, and a model reads it twenty-odd times)."""
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
                     for n in ("item", "model", "gradient", "unit", "itemType",
                               "bodyPart")}
    return _CDB[key]


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


# Heaps' matrices take row vectors (v * M): a joint's absolute matrix is its
# own times its parent's; a vertex moves by transPos (inverse bind) times
# that. Some models (the crawlers) are in centimetres and Z-up: only their
# skeleton makes them the right size and way up.
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
    (transPos): scaled last, so the scale reaches the translation too (the
    crawlers' bind matrices are scaled)."""
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


# The viewer gets the mesh already moved by the root (G), the prefab (N) and
# turned Y-up (S); a frame moves it by the joints' P instead of G, so the
# viewer applies C = S^-1 N^-1 G^-1 P N S, per joint, per frame.
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
    return anim_file_frames(game_dir, path) if path else None


def anim_file_frames(game_dir, path):
    """One animation file sampled at ANIM_FPS: (frames, fps), or None."""
    raw = _read(Path(game_dir) / "res.pak", path)
    if not raw or raw[:3] != b"HMD":
        return None
    d = read_hmd(raw)
    if not d["anims"]:
        return None
    a = d["anims"][0]
    frames = anim_frames(raw, d, a)
    step = max(1, round((a["sampling"] or 30) / ANIM_FPS))
    return frames[::step], (a["sampling"] or 30) / step * (a["speed"] or 1)


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
    """Every model a prefab is made of: [{source, mats, matrix}]. A model
    hung on another's bone (a constraint) is left out."""
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
    sub_mats = [model["mats"][k] if k < len(model["mats"]) else 0
                for k in range(len(m["subs"]))]
    if not model.get("skin"):
        # a rigid object: placed by its node as Heaps places it (its
        # transform and its parents': the Nibsham daggers are modelled ten
        # times too big and scaled down there)
        _place(m, _node_abs(d, d["models"].index(model)))
        if not frames:
            # in several pieces (a sword's blade, guard and handle): every
            # geometry, each by its own node
            _merge_geoms(raw, d, model, m, sub_mats)
    sub_of = [0] * m["n"]
    for k, tris in enumerate(m["subs"]):
        for t in tris:
            sub_of[t] = sub_mats[k]
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
    # a material the prefab names differently from the model takes the
    # unclaimed gradmat with the most slots
    names = {mm.get("name") for mm in d["mats"]}
    spare = sorted((v for k, v in gradmats.items()
                    if k not in names and k != "*" and v.get("slots")),
                   key=lambda v: -len(v["slots"]))
    parts = []
    for k, tris in enumerate(m["subs"]):
        mi = sub_mats[k]
        mat = d["mats"][mi] if mi < len(d["mats"]) else {}
        if str(mat.get("name", "")).lower() == "outline":
            # the outline's inverted hull: drawn plainly, it hides the object
            continue
        gm = (gradmats.get(mat.get("name")) or gradmats.get("*")
              or (spare[0] if spare else {}))
        parts.append((tris, gm))
    return m, parts, anim


def _node_abs(d, i):
    """A model node's matrix in the file's space: its own times its
    parents'."""
    M, seen = _IDENTITY, set()
    while 0 <= i < len(d["models"]) and i not in seen:
        seen.add(i)
        node = d["models"][i]
        M = _mul(M, _mat(node.get("pos") or [0, 0, 0, 0, 0, 0, 1, 1, 1]))
        i = node.get("parent", -1)
    return M


def _place(m, M):
    """A mesh's positions and normals moved by M (in place)."""
    pos, nor = m["pos"], m["nor"]
    for j in range(0, len(pos), 3):
        px, py, pz = pos[j:j + 3]
        nx, ny, nz = nor[j:j + 3]
        pos[j:j + 3] = [px * M[0][c] + py * M[1][c] + pz * M[2][c] + M[3][c]
                        for c in range(3)]
        v = [nx * M[0][c] + ny * M[1][c] + nz * M[2][c] for c in range(3)]
        ln = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5 or 1.0
        nor[j:j + 3] = [v[0] / ln, v[1] / ln, v[2] / ln]


def _merge_geoms(raw, d, first, m, sub_mats):
    """The file's other unskinned geometries into `m` (in place), each
    placed by its own node, as the first."""
    i0 = d["models"].index(first)
    for i, node in enumerate(d["models"]):
        if i == i0 or node["geom"] < 0 or node.get("skin"):
            continue
        g = mesh(raw, d, node["geom"])
        M = _node_abs(d, i)
        base = m["n"]
        pos, nor = list(g["pos"]), list(g["nor"])
        for j in range(0, len(pos), 3):
            px, py, pz = pos[j:j + 3]
            nx, ny, nz = nor[j:j + 3]
            pos[j:j + 3] = [px * M[0][c] + py * M[1][c] + pz * M[2][c] + M[3][c]
                            for c in range(3)]
            v = [nx * M[0][c] + ny * M[1][c] + nz * M[2][c] for c in range(3)]
            ln = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5 or 1.0
            nor[j:j + 3] = [v[0] / ln, v[1] / ln, v[2] / ln]
        m["pos"] += pos
        m["nor"] += nor
        m["uv"] = list(m["uv"]) + list(g["uv"])
        m["uv2"] = list(m["uv2"]) + list(g["uv2"])
        for k, tris in enumerate(g["subs"]):
            m["subs"] = list(m["subs"]) + [tuple(t + base for t in tris)]
            sub_mats.append(node["mats"][k] if k < len(node["mats"]) else 0)
        m["n"] += g["n"]
    m["big"] = m["n"] > 0x10000


# the hero as the game dresses it: a body in parts, each worn piece in place
# of the bare part it covers; the face the game gives a new character
HERO_BODY = "Character/Hero/Body/"
HERO_BARE = {"Chest": "Chest_Naked", "Legs": "Legs_Naked",
             "Hands": "Hands_Naked", "Feet": "Feet_Naked"}
HERO_FACE = ("MainHead_Naked", "Eyes/Eyes_01_A", "Eyebrows/Eyebrows_01_A")


def hero_models(game_dir, gear, face=HERO_FACE):
    """The models of a hero wearing `gear` ({slot: item id}), for item_model:
    the armour on the body, the weapons holstered, the set in use (Weapon1,
    OffhandWeapon) and the other (Weapon2); the jewels are not seen."""
    items = _sheets(game_dir)["item"]
    prefabs, covered = [], set()
    worn = [items.get(i) or {} for i in gear.values() if i]
    if any((it.get("visuals") or {}).get("hideLegs") for it in worn):
        # a long robe: no legs under it
        covered.add("Legs")
        worn = [it for it in worn if it.get("type") != "Legs"]
    for it in worn:
        path = (it.get("visuals") or {}).get("modelPath") or ""
        if it.get("type") in HERO_BARE or it.get("type") in (
                "Head", "Shoulders", "Back", "Waist"):
            if path.endswith(".prefab"):
                prefabs.append(path)
                covered.add(it.get("type"))
    prefabs += [HERO_BODY + p + ".prefab" for p in face]
    prefabs += [HERO_BODY + bare + ".prefab" for t, bare in HERO_BARE.items()
                if t not in covered]
    out = _on_body(game_dir, [m for p in prefabs
                              for m in prefab_models(game_dir, p)])
    for slot, which in (("Weapon1", "primary"), ("OffhandWeapon", "primary"),
                        ("Weapon2", "secondary")):
        if gear.get(slot):
            out += _holstered_models(game_dir, items.get(gear[slot]) or {}, which)
    return out


# A weapon's type's holster (itemType slot.holster, inherited) names a
# Body.prefab socket per model, "primary" for the set in use, "secondary" for
# the other. The body parts' prefabs turn the hero a quarter (their ROOT).
HERO_SKELETON = "Character/Hero/Body/Sources/Hero_N.fbx"
HERO_ROOT = _local({"rotationZ": 90})
HERO_IDLE = "Anim/Human/Common/Anim_Human_Common_Idle.fbx"
_SKEL, _SOCKETS = {}, {}


def _hero_skeleton(game_dir):
    """(joints, root matrix G as rest_pose finds it) of the hero's skeleton,
    or None."""
    key = str(game_dir)
    if key not in _SKEL:
        _SKEL[key] = None
        raw = _read(Path(game_dir) / "res.pak", HERO_SKELETON)
        model = next((m for m in read_hmd(raw)["models"] if m.get("skin")), None) if raw else None
        if model:
            joints = model["skin"]["joints"]
            absm, depth = [], []
            for j in joints:
                loc = _mat(j["pos"])
                absm.append(loc if j["parent"] < 0 else _mul(loc, absm[j["parent"]]))
                depth.append(0 if j["parent"] < 0 else depth[j["parent"]] + 1)
            bound = [i for i, j in enumerate(joints) if j["bind"] >= 0]
            if bound:
                root = min(bound, key=lambda i: depth[i])
                _SKEL[key] = (joints, _mul(_mat_post(joints[root]["trans"]), absm[root]))
    return _SKEL[key]


def _joint(game_dir, name):
    """(joints, G, index) of one of the hero's bound joints, or None."""
    sk = _hero_skeleton(game_dir)
    i = next((n for n, j in enumerate(sk[0]) if j["name"] == name
              and j["bind"] >= 0), None) if sk else None
    return None if i is None else (sk[0], sk[1], i)


def _follow_column(game_dir, name, frames):
    """How the hero's joint `name` moves what it carries, frame by frame, as
    skin_animation's columns: what hangs on the chest moves with it."""
    got = _joint(game_dir, name) if name else None
    if not got:
        return None
    joints, G, i = got
    chain = []
    while i >= 0:
        chain.append(i)
        i = joints[i]["parent"]
    pre = _mul(_inv(_SWAP), _mul(_inv(HERO_ROOT), _inv(G)))
    post = _mul(HERO_ROOT, _SWAP)
    col = []
    for fr in frames:
        absm = _IDENTITY
        for k in reversed(chain):
            absm = _mul(_pose_matrix(joints[k], fr.get(joints[k]["name"])), absm)
        P = _mul(_mat_post(joints[chain[0]]["trans"]), absm)
        col.append(_mul(pre, _mul(P, post)))
    return col


def _sockets(game_dir):
    """{socket name: (its matrix, the joint it rides)} of the hero's body."""
    key = str(game_dir)
    if key not in _SOCKETS:
        out = {}
        raw = _read(Path(game_dir) / "res.pak", HERO_BODY + "Body.prefab")
        if raw:
            tree = hbson.loads(raw)
            rides = {}
            _walk(tree, lambda o: rides.__setitem__(
                str(o.get("object")), str(o.get("target", "")).split(".")[-1])
                if o.get("type") == "constraint" else None)
            for group in tree.get("children") or ():
                joint = rides.get(group.get("name"))
                for c in (group.get("children") or ()) if joint else ():
                    out[c.get("name")] = (_local(c), joint)
        _SOCKETS[key] = out
    return _SOCKETS[key]


def _holstered_models(game_dir, it, which):
    """A weapon's models on its holster's sockets (prefab_models' form, each
    following its joint), `which` set: "primary" or "secondary"."""
    vis = it.get("visuals") or {}
    prefabs = [str((m or {}).get("prefab", "")) for m in vis.get("models") or ()]
    types = _sheets(game_dir)["itemType"]
    t, holes = types.get(it.get("type")), []
    for _ in range(8):
        if not t or holes:
            break
        holes = ((t.get("slot") or {}).get("holster") or {}).get(which) or []
        t = types.get(t.get("inherit"))
    sockets = _sockets(game_dir)
    out = []
    for pf, hole in zip(prefabs, holes):
        sock = sockets.get((hole or {}).get("socket"))
        got = _joint(game_dir, sock[1]) if sock else None
        if not pf.endswith(".prefab") or not got:
            continue
        joints, G, i = got
        # the joint as the mesh has it: where its bind puts it
        place = _mul(sock[0], _mul(_mul(_inv(_mat_post(joints[i]["trans"])), G), HERO_ROOT))
        out += [dict(m, matrix=_mul(m["matrix"], place), follow=sock[1])
                for m in prefab_models(game_dir, pf)]
    return out


def hero_model(game_dir, gear, anim=True):
    """The viewer's payload for a hero wearing `gear` ({slot: item id}), in
    its idle."""
    idle = anim_file_frames(game_dir, HERO_IDLE) if anim else None
    if idle:
        # a slow breath: every other frame is enough, the viewer blends them
        # (the body's 117 joints make a palette as heavy as its mesh)
        idle = (idle[0][::2], idle[1] / 2)
    return item_model(game_dir, "hero", models=hero_models(game_dir, gear), idle=idle)


# a character's clothes (Element.props.npc.npcGear) to the hero's slots;
# "Gold" fills a slot left empty
NPC_GEAR_SLOTS = {"helmet": "Head", "shoulders": "Shoulders",
                  "bodyArmor": "Chest", "gloves": "Hands", "belt": "Waist",
                  "pants": "Legs", "boots": "Feet", "back": "Back",
                  "weapon": "Weapon1", "offhandWeapon": "OffhandWeapon",
                  "weapon2": "Weapon2"}


def _on_body(game_dir, models):
    """Pieces worn on the hero's body as client.UnitView links them
    (linkSubSkin): a skinned one moved by the body's skeleton, turned as the
    body is (HERO_ROOT), its own node's turn dropped. The eyebrows' prefabs
    don't turn theirs: kept, it put them on the side of the face."""
    out = []
    for m in models:
        raw = _read(Path(game_dir) / "res.pak", m["source"])
        skinned = bool(raw) and raw[:3] == b"HMD" and any(
            x.get("skin") for x in read_hmd(raw)["models"] if x["geom"] >= 0)
        out.append(dict(m, matrix=HERO_ROOT) if skinned else m)
    return out


def _gear_props(game_dir, it):
    """The prefab.GearProps of a piece's model (client.UnitView.
    loadGearProps): {hairMode, facialHairMode, ...}, or None."""
    vis = it.get("visuals") or {}
    path = vis.get("modelPath") or next(
        (str((x or {}).get("prefab") or "") for x in vis.get("models") or ()), "")
    raw = _read(Path(game_dir) / "res.pak", path) if path.endswith(".prefab") \
        else None
    if not raw:
        return None
    found = []
    _walk(hbson.loads(raw), lambda o: found.append(o)
          if str(o.get("type", "")) == "gearProps" else None)
    return found[0] if found else None


def npc_hair(game_dir, skin, gear):
    """(hair, beard) body parts a character shows under its head piece, as
    the game picks them (client.UnitView.applyItemGearProps, getCurrentHair,
    getCurrentFacialHair): with something on the head (an item, even the
    "Gold" filling an empty slot, but not Hide_Gear), the hair "LowHair"
    unless the piece's GearProps says otherwise, the beard as GearProps says
    ("AllHair" by default). LowHair: the hair's hairUnderGear, else the
    UnderGear hair of its length."""
    sh = _sheets(game_dir)
    parts, items = sh["bodyPart"], sh["item"]
    head = items.get((gear or {}).get("helmet") or "")
    hair_mode = facial_mode = "AllHair"
    if head and head.get("id") != "Hide_Gear":
        hair_mode = "LowHair"
        props = _gear_props(game_dir, head) or {}
        hair_mode = props.get("hairMode") or hair_mode
        facial_mode = props.get("facialHairMode") or facial_mode
    hair = skin.get("hair")
    if hair_mode != "AllHair":
        row = (parts.get(hair) or {}) if hair_mode == "LowHair" and hair else {}
        p = row.get("props") or {}
        hair = p.get("hairUnderGear") or {
            1: "Hair_Medium_UnderGear", 2: "Hair_Long_UnderGear"}.get(
            p.get("hairLength"), "Hair_Short_UnderGear")
    beard = skin.get("facialHair") if facial_mode == "AllHair" else "Beard_None"
    return hair, beard


def _recolor(models, slots):
    """The models' gradients with some slots replaced ({index: gradient id}):
    the hero's colours as client.UnitView sets them (setGradSlot)."""
    out = []
    for m in models:
        mats = {}
        for k, gm in (m.get("mats") or {}).items():
            gm = dict(gm)
            if gm.get("slots"):
                sl = list(gm["slots"])
                for i, gid in slots.items():
                    if gid and i < len(sl):
                        sl[i] = gid
                gm["slots"] = sl
            mats[k] = gm
        out.append(dict(m, mats=mats))
    return out


def npc_models(game_dir, skin, gear):
    """A character built on the hero's body (BaseHero), as the game dresses
    it (client.UnitView): its head, eyes, eyebrows, hair and beard (the
    bodyPart sheet's prefabs), its clothes and weapons, each coloured: skin
    in slot 0 everywhere, hair colours in 1 and 2 of the hair, beard and
    eyebrows, eye colour in 1 and 2 of the eyes, a piece's own gradients
    (visuals.gradMat)."""
    sh = _sheets(game_dir)
    parts, items = sh["bodyPart"], sh["item"]
    skin = skin or {}
    worn = {slot: gear.get(k) for k, slot in NPC_GEAR_SLOTS.items()
            if gear.get(k) and gear.get(k) != "Gold" and gear.get(k) in items}
    skin0 = {0: skin.get("skinColor")}
    hair = {0: skin.get("skinColor"), 1: skin.get("hairColor"),
            2: skin.get("hairColorSecondary") or skin.get("hairColor")}
    eyes = {0: skin.get("skinColor"), 1: skin.get("eyeColor"),
            2: skin.get("eyeColor")}

    def part(pid, kind):
        """A body part's prefab, if it is of the kind its place takes (the
        bodyPart sheet's type: 0 hair, 1 eyes, 2 eyebrows, 3 beard)."""
        row = parts.get(pid) or {}
        return row.get("prefab") if row.get("type") == kind and str(
            row.get("prefab", "")).endswith(".prefab") else None
    def body(pf):
        return _on_body(game_dir, prefab_models(game_dir, pf))
    out = []
    # the clothes, each with the skin and its own gradients (gradMat:
    # {gradient, index}); a long robe hides the legs
    pieces = [items[i] for i in worn.values()]
    covered = set()
    if any((it.get("visuals") or {}).get("hideLegs") for it in pieces):
        covered.add("Legs")
    for slot, iid in worn.items():
        it = items[iid]
        if slot in covered or slot.startswith(("Weapon", "Offhand")):
            continue
        path = (it.get("visuals") or {}).get("modelPath") or ""
        if not path.endswith(".prefab"):
            continue
        own = dict(skin0)
        for g in (it.get("visuals") or {}).get("gradMat") or ():
            if isinstance(g, dict) and g.get("gradient") and                     isinstance(g.get("index"), int):
                own[g["index"]] = g["gradient"]
        out += _recolor(body(path), own)
        covered.add(slot)
    # the bare body where nothing covers it
    for t, bare in HERO_BARE.items():
        if t not in covered:
            out += _recolor(body(HERO_BODY + bare + ".prefab"), skin0)
    # the weapons, holstered: the set in use, the other
    for slot, which in (("Weapon1", "primary"), ("OffhandWeapon", "primary"),
                        ("Weapon2", "secondary")):
        if worn.get(slot):
            out += _holstered_models(game_dir, items[worn[slot]], which)
    # the face
    out += _recolor(body(HERO_BODY + "MainHead_Naked.prefab"), skin0)
    shown = dict(skin)
    shown["hair"], shown["facialHair"] = npc_hair(game_dir, skin, gear)
    for key, kind, colours in (("eyes", 1, eyes), ("eyebrows", 2, hair),
                               ("hair", 0, hair), ("facialHair", 3, hair)):
        pf = part(shown.get(key), kind)
        if pf:
            out += _recolor(body(pf), colours)
    return out


def npc_model(game_dir, skin, gear, anim=True):
    """The viewer's payload for such a character, in the hero's idle."""
    idle = anim_file_frames(game_dir, HERO_IDLE) if anim else None
    if idle:
        idle = (idle[0][::2], idle[1] / 2)
    return item_model(game_dir, "npc", models=npc_models(game_dir, skin, gear),
                      idle=idle)


def item_model(game_dir, item_id, anim=False, models=None, idle=None):
    """The viewer's payload for one collectible (or monster), as one mesh,
    or None. With `anim`, its idle animation too. `models`/`idle` replace
    the item's (a dressed hero)."""
    prefab = item_prefab(game_dir, item_id) if models is None else None
    if not prefab and models is None:
        return None
    if idle is None and anim:
        mount = (_sheets(game_dir)["item"].get(item_id) or {}).get("type") == "Mount"
        idle = idle_frames(game_dir, item_rig(game_dir, item_id),
                           ("MountIdle", "Idle") if mount else ("Idle",))
    frames = idle[0] if idle else None
    pos, nor, uv, uv2, groups = [], [], [], [], []
    # cols: each joint's matrices over the frames; jv: four per vertex
    jv, wv, cols = [], bytearray(), []
    for mdl in prefab_models(game_dir, prefab) if models is None else models:
        raw = _read(Path(game_dir) / "res.pak", mdl["source"])
        if not raw or raw[:3] != b"HMD":
            continue
        got = _model_parts(game_dir, raw, mdl["mats"], mdl["matrix"], frames)
        if not got:
            continue
        m, parts, am = got
        if frames:
            # a model the animation doesn't move hangs on one still joint,
            # or on the hero's joint it follows (a holstered weapon)
            first = len(cols)
            if am:
                jl, wl, mats = am
                cols += [[row[s] for row in mats] for s in range(len(mats[0]))]
                jv += [first + j for j in jl]
                wv += wl
            else:
                cols.append(_follow_column(game_dir, mdl.get("follow"), frames)
                            or [_IDENTITY] * len(frames))
                jv += [first] * (4 * m["n"])
                wv += bytes([255, 0, 0, 0] * m["n"])
        base = len(pos) // 3
        pos += m["pos"]
        nor += m["nor"]
        uv += m["uv"]
        uv2 += m["uv2"]
        groups += [([t + base for t in tris], gm) for tris, gm in parts]
    n = len(pos) // 3
    if not n:
        return None
    # Heaps is Z-up, the viewer Y-up
    for a in (pos, nor):
        for i in range(0, len(a), 3):
            a[i + 1], a[i + 2] = a[i + 2], -a[i + 1]
    big = n > 0x10000
    # a piece of armour shown on its own: its thin parts (a brim, a feather,
    # a cape) seen from behind too, which on the hero nobody does (the
    # game culls their back faces); the viewer lights a back face by its
    # flipped normal
    worn = bool(prefab) and prefab.startswith("Character/Hero/")
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
                    "cull": ("None" if worn and gm.get("cull", "Back") == "Back"
                             and not gm.get("glass") else gm.get("cull", "Back")),
                    "blend": gm.get("blend", "None")})
    # normals as signed bytes: a third of the size, and plenty for shading
    nb = [max(-127, min(127, round(x * 127))) for x in nor]
    payload = {"id": item_id, "n": n, "big": big,
               "pos": _b64("f", pos), "nor": _b64("b", nb),
               "uv": _b64("f", uv), "uv2": _b64("f", uv2),
               "lines": lines_png(game_dir), "parts": out}
    if frames and any(any(M is not _IDENTITY for M in col) for col in cols):
        # the parts of a body share its joints: one column for each
        uniq, remap, kept = {}, [], []
        for col in cols:
            key = tuple(round(x, 4) for M in col for r in M for x in r)
            if key not in uniq:
                uniq[key] = len(kept)
                kept.append(col)
            remap.append(uniq[key])
        nj = len(kept)
        if nj <= 255:
            jv = [remap[j] for j in jv]
            cols = kept
            # [frame][joint] three columns of four: (m0c, m1c, m2c, m3c)
            flat = []
            for f in range(len(frames)):
                for col in cols:
                    M = col[f]
                    for c in range(3):
                        flat += (M[0][c], M[1][c], M[2][c], M[3][c])
            allm = [[col[f] for col in cols] for f in range(len(frames))]
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
            # frame from the first pose (a crawler's legs spread wider than
            # modelled)
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
