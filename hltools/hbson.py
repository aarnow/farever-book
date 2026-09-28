"""Reader for HBSON, the binary JSON Farever's levels (.prefab) are stored in.

Worked out from the files themselves (2026-09-28): this reads all 4,547
.prefab files in res.pak, res.levels.pak and res.map.pak.

    "HBSON" 0x00, then one value.

    value = tag byte, then:
      0x00  the integer 0          0x01  int8          0x02  int32 (LE)
      0x03  float64 (LE)           0x04  true          0x05  false
      0x06  null                   0x07  {} (empty object)
      0x08  object: count, then `count` x (key, value)
      0x09  object, with an int32 count
      0x0a  string                 0x0b  [] (empty array)
      0x0c  array: count, then `count` values
      0x0d  array, with an int32 count
    count = one byte
    key / string = a u32 (LE): top byte 0x40 = new string of `low 24 bits`
      UTF-8 bytes, remembered in a table; 0x80 = new string, not remembered;
      0x00 = the table's entry number `low 24 bits`.

Anything else raises HBSONError, with the offset, rather than guessing."""
import struct


class HBSONError(ValueError):
    pass


def loads(buf: bytes):
    if buf[:6] != b"HBSON\x00":
        raise HBSONError("not an HBSON file")
    table = []
    pos = 6

    def u8():
        nonlocal pos
        v = buf[pos]
        pos += 1
        return v

    def u32():
        nonlocal pos
        v = struct.unpack_from("<I", buf, pos)[0]
        pos += 4
        return v

    def count():
        return u8()

    def string():
        nonlocal pos
        v = u32()
        flag, n = v >> 24, v & 0xFFFFFF
        if flag == 0:
            if n >= len(table):
                raise HBSONError(f"string #{n} unknown at {pos - 4}")
            return table[n]
        if flag not in (0x40, 0x80):
            raise HBSONError(f"string flag 0x{flag:02x} at {pos - 4}")
        s = buf[pos:pos + n].decode("utf-8", "replace")
        pos += n
        if flag == 0x40:
            table.append(s)
        return s

    def value():
        nonlocal pos
        tag = u8()
        if tag == 0x00:
            return 0
        if tag == 0x01:
            return struct.unpack_from("<b", buf, (pos := pos + 1) - 1)[0]
        if tag == 0x02:
            pos += 4
            return struct.unpack_from("<i", buf, pos - 4)[0]
        if tag == 0x03:
            pos += 8
            return struct.unpack_from("<d", buf, pos - 8)[0]
        if tag == 0x04:
            return True
        if tag == 0x05:
            return False
        if tag == 0x06:
            return None
        if tag == 0x07:
            return {}
        if tag == 0x08:
            out = {}
            for _ in range(count()):
                k = string()
                out[k] = value()
            return out
        if tag == 0x09:
            out = {}
            for _ in range(u32()):
                k = string()
                out[k] = value()
            return out
        if tag == 0x0A:
            return string()
        if tag == 0x0B:
            return []
        if tag == 0x0C:
            return [value() for _ in range(count())]
        if tag == 0x0D:
            return [value() for _ in range(u32())]
        raise HBSONError(f"unknown tag 0x{tag:02x} at {pos - 1}")

    return value()
