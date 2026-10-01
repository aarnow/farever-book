"""HashLink function bodies: decode and disassemble the bytecode of
hlboot.dat, to read game logic the data files don't describe (how a loot
roll is computed, for instance).

hlbc_parser.HLCode stops after the types and natives; this module reads the
functions that follow (hashlink src/code.c: hl_read_function,
hl_read_opcode, hl_read_debug_infos) and prints one function as readable
pseudo-assembly, with every index resolved — function names, fields,
globals, constants, strings — and jumps as absolute targets."""
from dataclasses import dataclass, field

from hlbc_parser import HLCode, HLReader, HOBJ, HSTRUCT

# (name, number of arguments; -1 = variable) in opcode order — hashlink
# src/opcodes.h.
OPCODES = [
    ("Mov", 2), ("Int", 2), ("Float", 2), ("Bool", 2), ("Bytes", 2),
    ("String", 2), ("Null", 1),
    ("Add", 3), ("Sub", 3), ("Mul", 3), ("SDiv", 3), ("UDiv", 3),
    ("SMod", 3), ("UMod", 3), ("Shl", 3), ("SShr", 3), ("UShr", 3),
    ("And", 3), ("Or", 3), ("Xor", 3),
    ("Neg", 2), ("Not", 2), ("Incr", 1), ("Decr", 1),
    ("Call0", 2), ("Call1", 3), ("Call2", 4), ("Call3", 5), ("Call4", 6),
    ("CallN", -1), ("CallMethod", -1), ("CallThis", -1), ("CallClosure", -1),
    ("StaticClosure", 2), ("InstanceClosure", 3), ("VirtualClosure", 3),
    ("GetGlobal", 2), ("SetGlobal", 2), ("Field", 3), ("SetField", 3),
    ("GetThis", 2), ("SetThis", 2), ("DynGet", 3), ("DynSet", 3),
    ("JTrue", 2), ("JFalse", 2), ("JNull", 2), ("JNotNull", 2),
    ("JSLt", 3), ("JSGte", 3), ("JSGt", 3), ("JSLte", 3), ("JULt", 3),
    ("JUGte", 3), ("JNotLt", 3), ("JNotGte", 3), ("JEq", 3), ("JNotEq", 3),
    ("JAlways", 1),
    ("ToDyn", 2), ("ToSFloat", 2), ("ToUFloat", 2), ("ToInt", 2),
    ("SafeCast", 2), ("UnsafeCast", 2), ("ToVirtual", 2),
    ("Label", 0), ("Ret", 1), ("Throw", 1), ("Rethrow", 1), ("Switch", -1),
    ("NullCheck", 1), ("Trap", 2), ("EndTrap", 1),
    ("GetI8", 3), ("GetI16", 3), ("GetMem", 3), ("GetArray", 3),
    ("SetI8", 3), ("SetI16", 3), ("SetMem", 3), ("SetArray", 3),
    ("New", 1), ("ArraySize", 2), ("Type", 2), ("GetType", 2), ("GetTID", 2),
    ("Ref", 2), ("Unref", 2), ("Setref", 2),
    ("MakeEnum", -1), ("EnumAlloc", 2), ("EnumIndex", 2), ("EnumField", 4),
    ("SetEnumField", 3),
    ("Assert", 0), ("RefData", 2), ("RefOffset", 3), ("Nop", 0),
    ("Prefetch", 3), ("Asm", 3),
    # HashLink 1.15 (bytecode version 6): a catch block's marker. Its
    # argument count found by decoding Farever's whole file (2026-10-01).
    ("Catch", 1),
]
OP = {name: i for i, (name, _n) in enumerate(OPCODES)}
JUMPS = {"JTrue", "JFalse", "JNull", "JNotNull", "JAlways", "JSLt", "JSGte",
         "JSGt", "JSLte", "JULt", "JUGte", "JNotLt", "JNotGte", "JEq",
         "JNotEq", "Trap"}


@dataclass
class Op:
    op: int
    p: list
    extra: list = field(default_factory=list)


@dataclass
class Function:
    type: int
    findex: int
    regs: list
    ops: list
    lines: list = None          # (file index, line) per op, with debug info


def read_functions(code: HLCode):
    """{findex: Function} for every function of the file."""
    r = HLReader(code.data)
    r.p = code.functions_offset
    out = {}
    for _ in range(code.counts["nfunctions"]):
        ftype = r.index()
        findex = r.index()
        nregs = r.index()
        nops = r.index()
        regs = [r.index() for _ in range(nregs)]
        ops = []
        for _ in range(nops):
            op = r.byte()
            name, n = OPCODES[op]
            if n == -1:
                if name == "Switch":
                    p1 = r.index(); cnt = r.index()
                    extra = [r.index() for _ in range(cnt)]
                    ops.append(Op(op, [p1, cnt, r.index()], extra))
                else:
                    p1 = r.index(); p2 = r.index(); cnt = r.byte()
                    ops.append(Op(op, [p1, p2, cnt],
                                  [r.index() for _ in range(cnt)]))
            elif n <= 3:
                ops.append(Op(op, [r.index() for _ in range(n)]))
            else:
                ps = [r.index() for _ in range(3)]
                ops.append(Op(op, ps, [r.index() for _ in range(n - 3)]))
        f = Function(ftype, findex, regs, ops)
        if code.has_debug:
            f.lines = _debug_infos(r, nops)
            if code.version >= 3:
                # local variable names: (name, op) — and since bytecode
                # version 6 a third value (measured on Farever, 2026-10-01)
                per = 3 if code.version >= 6 else 2
                for _ in range(r.index()):
                    for _k in range(per):
                        r.index()
        out[findex] = f
    # what follows is the constants table, then the end of the file: a
    # misread anywhere above would not land there
    code.constants = {}
    for _ in range(code.counts["nconstants"]):
        g = r.index()
        code.constants[g] = [r.index() for _k in range(r.index())]
    if r.p != len(code.data):
        raise ValueError(f"bytecode misread: ended at {r.p}, "
                         f"file is {len(code.data)} bytes")
    return out


def _debug_infos(r, nops):
    cur_file, cur_line, out = -1, 0, []
    while len(out) < nops:
        c = r.byte()
        if c & 1:
            cur_file = ((c >> 1) << 8) | r.byte()
        elif c & 2:
            delta, count = c >> 6, (c >> 2) & 15
            out += [(cur_file, cur_line)] * count
            cur_line += delta
        elif c & 4:
            cur_line += c >> 3
            out.append((cur_file, cur_line))
        else:
            b2 = r.byte(); b3 = r.byte()
            cur_line = (c >> 3) | (b2 << 5) | (b3 << 13)
            out.append((cur_file, cur_line))
    return out


class Disasm:
    """Readable listing of functions, every index resolved."""

    def __init__(self, code: HLCode, functions=None):
        self.code = code
        self.fns = functions if functions is not None else read_functions(code)
        self.names = code.findex_names()
        # A binding's field index counts the parents' fields too (the
        # static classes' $Math extends a base with five fields): name the
        # bound functions from the whole chain, or every static is shifted.
        for t in code.types:
            if t.kind not in (HOBJ, HSTRUCT) or not t.bindings:
                continue
            fields = [f.name for x in code._super_chain(t.index)
                      if x.kind in (HOBJ, HSTRUCT) for f in x.fields]
            for fid, findex in t.bindings:
                if 0 <= fid < len(fields):
                    self.names[findex] = f"{t.name}.{fields[fid]}"

    def fname(self, findex):
        return self.names.get(findex, f"fn{findex}")

    def field_name(self, tindex, fi):
        """The fi-th field of an object type, counting its parents'."""
        fields = []
        for t in self.code._super_chain(tindex):
            if t.kind in (HOBJ, HSTRUCT):
                fields += [f.name for f in t.fields]
        if 0 <= fi < len(fields):
            return fields[fi]
        t = self.code.types[tindex]
        vf = getattr(t, "vfields", None) or []
        if 0 <= fi < len(vf):
            return vf[fi].name
        return f"field{fi}"

    def proto_name(self, tindex, pi):
        for t in reversed(self.code._super_chain(tindex)):
            for p in getattr(t, "protos", ()):
                if getattr(p, "pindex", None) == pi:
                    return p.name
        return f"method{pi}"

    def listing(self, findex):
        f = self.fns[findex]
        c = self.code
        t = c.types
        R = lambda i: f"r{i}"
        rt = lambda i: c.type_str(f.regs[i]) if 0 <= i < len(f.regs) else "?"
        lines = [f"function {self.fname(findex)} (findex {findex}) "
                 f"{c.type_str(f.type)}"]
        lines += [f"  ; r{i}: {c.type_str(ti)}" for i, ti in enumerate(f.regs)]
        for i, o in enumerate(f.ops):
            name = OPCODES[o.op][0]
            p = o.p
            s = None
            if name == "Int":
                s = f"{R(p[0])} = {c.ints[p[1]]}"
            elif name == "Float":
                s = f"{R(p[0])} = {c.floats[p[1]]}"
            elif name == "String":
                s = f"{R(p[0])} = {c.strings[p[1]]!r}"
            elif name == "Bool":
                s = f"{R(p[0])} = {bool(p[1])}"
            elif name == "Null":
                s = f"{R(p[0])} = null"
            elif name == "Mov":
                s = f"{R(p[0])} = {R(p[1])}"
            elif name in ("Add", "Sub", "Mul", "SDiv", "UDiv", "SMod",
                          "UMod", "Shl", "SShr", "UShr", "And", "Or",
                          "Xor"):
                sym = {"Add": "+", "Sub": "-", "Mul": "*", "SDiv": "/",
                       "UDiv": "/u", "SMod": "%", "UMod": "%u", "Shl": "<<",
                       "SShr": ">>", "UShr": ">>>", "And": "&", "Or": "|",
                       "Xor": "^"}[name]
                s = f"{R(p[0])} = {R(p[1])} {sym} {R(p[2])}"
            elif name.startswith("Call") and name[4:].isdigit():
                args = ([p[2]] if len(p) > 2 else []) + o.extra
                args = [R(a) for a in (p[2:] + o.extra)]
                s = f"{R(p[0])} = {self.fname(p[1])}({', '.join(args)})"
            elif name in ("CallN", "CallThis"):
                fn = self.fname(p[1]) if name == "CallN" else \
                    f"this.{self.proto_name(f.regs[0], p[1])}"
                s = f"{R(p[0])} = {fn}({', '.join(R(a) for a in o.extra)})"
            elif name == "CallMethod":
                obj = o.extra[0] if o.extra else None
                mname = (self.proto_name(f.regs[obj], p[1]) if obj is not None
                         else f"method{p[1]}")
                s = (f"{R(p[0])} = {R(obj)}.{mname}("
                     f"{', '.join(R(a) for a in o.extra[1:])})")
            elif name == "CallClosure":
                s = (f"{R(p[0])} = {R(p[1])}("
                     f"{', '.join(R(a) for a in o.extra)})")
            elif name == "GetGlobal":
                s = f"{R(p[0])} = global{p[1]} <{c.type_str(c.globals[p[1]])}>"
                k = (getattr(c, "constants", None) or {}).get(p[1])
                if k and c.type_str(c.globals[p[1]]) == "String":
                    s = f"{R(p[0])} = {c.strings[k[0]]!r}  (global{p[1]})"
            elif name == "SetGlobal":
                s = f"global{p[0]} <{c.type_str(c.globals[p[0]])}> = {R(p[1])}"
            elif name == "Field":
                s = f"{R(p[0])} = {R(p[1])}.{self.field_name(f.regs[p[1]], p[2])}"
            elif name == "SetField":
                s = f"{R(p[0])}.{self.field_name(f.regs[p[0]], p[1])} = {R(p[2])}"
            elif name == "GetThis":
                s = f"{R(p[0])} = this.{self.field_name(f.regs[0], p[1])}"
            elif name == "SetThis":
                s = f"this.{self.field_name(f.regs[0], p[0])} = {R(p[1])}"
            elif name == "DynGet":
                s = f"{R(p[0])} = {R(p[1])}.{c.strings[p[2]]}"
            elif name == "DynSet":
                s = f"{R(p[0])}.{c.strings[p[1]]} = {R(p[2])}"
            elif name in JUMPS:
                tgt = i + 1 + p[-1]
                cond = {"JTrue": f"if {R(p[0])}", "JFalse": f"if !{R(p[0])}",
                        "JNull": f"if {R(p[0])} == null",
                        "JNotNull": f"if {R(p[0])} != null",
                        "JAlways": "", "Trap": f"trap {R(p[0])}"}.get(name)
                if cond is None:
                    sym = {"JSLt": "<", "JSGte": ">=", "JSGt": ">",
                           "JSLte": "<=", "JULt": "<u", "JUGte": ">=u",
                           "JNotLt": "!<", "JNotGte": "!>=", "JEq": "==",
                           "JNotEq": "!="}[name]
                    cond = f"if {R(p[0])} {sym} {R(p[1])}"
                s = f"{cond} goto @{tgt}".strip()
            elif name == "Switch":
                s = (f"switch {R(p[0])} -> "
                     + ", ".join(f"{k}:@{i + 1 + d}"
                                 for k, d in enumerate(o.extra))
                     + f" default @{i + 1 + p[2]}")
            elif name == "Ret":
                s = f"return {R(p[0])}"
            elif name == "New":
                s = f"{R(p[0])} = new {rt(p[0])}"
            elif name in ("ToSFloat", "ToUFloat", "ToInt", "ToDyn",
                          "SafeCast", "UnsafeCast", "ToVirtual"):
                s = f"{R(p[0])} = ({name[2:] if name.startswith('To') else rt(p[0])}) {R(p[1])}"
            elif name == "StaticClosure":
                s = f"{R(p[0])} = &{self.fname(p[1])}"
            elif name == "InstanceClosure":
                s = f"{R(p[0])} = &{R(p[2])}.{self.fname(p[1])}"
            elif name == "EnumIndex":
                s = f"{R(p[0])} = enumIndex({R(p[1])})"
            elif name == "EnumField":
                s = f"{R(p[0])} = {R(p[1])}.enumField(c{p[2]}, {o.extra[0]})"
            elif name == "GetArray":
                s = f"{R(p[0])} = {R(p[1])}[{R(p[2])}]"
            elif name == "SetArray":
                s = f"{R(p[0])}[{R(p[1])}] = {R(p[2])}"
            elif name == "ArraySize":
                s = f"{R(p[0])} = len({R(p[1])})"
            if s is None:
                s = f"{name} " + " ".join(str(x) for x in p + o.extra)
            ln = f.lines[i][1] if f.lines else ""
            lines.append(f"@{i:<4} {s:<70} ; L{ln}")
        return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from gamepath import find_hlboot
    code = HLCode(find_hlboot()).parse()
    d = Disasm(code)
    names = {v: k for k, v in d.names.items()}
    for want in sys.argv[1:]:
        fi = names.get(want) if not want.isdigit() else int(want)
        print(d.listing(fi) if fi in d.fns else f"no function {want}")
        print()
