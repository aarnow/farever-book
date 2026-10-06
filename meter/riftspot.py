"""Where the hourly rift opens: the same for every server, worked out from
its opening time. The game seeds Heaps' hxd.Rand with the opening hour in
Unix seconds (UTC) and draws one of the spots (matches the schedule players
see; a patch adding a spot would change both the list and the draws)."""

# the rift's instance: its entrances (bestiary.json) are the spots
RIFT_ACT = "POI_Rift_01"

# the zones a rift opens in, in the game's order
SPOTS = ("Z2_Krisomal_North",     # Outer Ruins of Tiocha
         "Z1_Enripit_Falls")      # Talitha Falls

_M = 0xFFFFFFFF


def _i32(x):
    x &= _M
    return x - (1 << 32) if x & 0x80000000 else x


def _mul(a, b):
    return _i32((a & _M) * (b & _M))


def _rotl(x, r):
    x &= _M
    return _i32((x << r) | (x >> (32 - r)))


def _hash(n):
    """hxd.Rand.hash: MurmurHash3's mix of one int, seed 5381."""
    n = _rotl(_mul(n, 0xCC9E2D51), 15)
    h = _rotl(_i32(5381 ^ _mul(n, 0x1B873593)), 13)
    h = _i32(_mul(h, 5) + 0xE6546B64)
    h = _mul(_i32(h ^ (h >> 16)), 0x85EBCA6B)
    h = _mul(_i32(h ^ (h >> 13)), 0xC2B2AE35)
    return _i32(h ^ (h >> 16))


def _random(seed, n):
    """new hxd.Rand(seed).random(n)."""
    s1, s2 = seed or 1, _hash(seed) or 1
    s1 = _i32(36969 * (s1 & 65535) + (s1 >> 16))
    s2 = _i32(18000 * (s2 & 65535) + (s2 >> 16))
    return (_i32((s1 << 16) + s2) & 0x3FFFFFFF) % n


def rift_zone(open_ts):
    """The zone id of the rift opening at `open_ts` (Unix seconds, on the
    hour)."""
    return SPOTS[_random(_i32(int(open_ts) // 3600 * 3600), len(SPOTS))]
