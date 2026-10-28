"""Fast baseline-JPEG entropy decoding for partial-file repair (pure Python, table driven).

decode() walks the Huffman stream with 16-bit lookahead tables, counting MCUs and
recording each block's absolute DC value and whether it is flat (no AC terms).
"""
import collections, hashlib

MASK = [(1 << i) - 1 for i in range(33)]
FAST = 9  # first-level lookup width: 512-entry tables (~4 KB) instead of 65536 entries (~2 MB)
_LUT_CACHE = collections.OrderedDict()  # (tables_id, class, id) -> lut; bounded so memory stays small
_LUT_CACHE_MAX = 256


class Donor:
    """Everything needed from a healthy JPEG's header."""

    def __init__(self, data, path=""):
        self.path, self.dri = path, 0
        self.dqt, self.dht, self.comps, self.scan = {}, {}, {}, []
        segs = bytearray(b"\xff\xd8")
        i = 2
        while True:
            if data[i] != 0xFF: raise ValueError("bad marker")
            m = data[i + 1]
            if m == 0xFF: i += 1; continue
            L = int.from_bytes(data[i + 2:i + 4], "big"); p = data[i + 4:i + 2 + L]
            if not (0xE0 <= m <= 0xEF or m == 0xFE):
                segs += data[i:i + 2 + L]
            if m == 0xDB:
                j = 0
                while j < len(p):
                    pq, tq = p[j] >> 4, p[j] & 15; n = 128 if pq else 64
                    self.dqt[tq] = int.from_bytes(p[j + 1:j + 3], "big") if pq else p[j + 1]
                    j += 1 + n
            elif m == 0xC4:
                j = 0
                while j < len(p):
                    tc, th = p[j] >> 4, p[j] & 15; counts = p[j + 1:j + 17]; n = sum(counts)
                    syms = p[j + 17:j + 17 + n]; codes, code, k = {}, 0, 0
                    for ln in range(1, 17):
                        for _ in range(counts[ln - 1]):
                            codes[syms[k]] = (code, ln); k += 1; code += 1
                        code <<= 1
                    self.dht[(tc, th)] = codes; j += 17 + n
            elif m == 0xDD:
                self.dri = int.from_bytes(p[:2], "big")
            elif m in (0xC2, 0xC6, 0xCA):
                raise ValueError("progressive JPEG")
            elif m in (0xC0, 0xC1):
                self.h = int.from_bytes(p[1:3], "big"); self.w = int.from_bytes(p[3:5], "big")
                for k in range(p[5]):
                    self.comps[p[6 + 3 * k]] = dict(H=p[7 + 3 * k] >> 4, V=p[7 + 3 * k] & 15, tq=p[8 + 3 * k])
            elif m == 0xDA:
                self.scan = [(p[1 + 2 * k], p[2 + 2 * k] >> 4, p[2 + 2 * k] & 15) for k in range(p[0])]
                self.scan_start = i + 2 + L
                break
            i += 2 + L
        self.segs = bytes(segs)
        self.hmax = max(c["H"] for c in self.comps.values()); self.vmax = max(c["V"] for c in self.comps.values())
        self.bw, self.bh = 8 * self.hmax, 8 * self.vmax
        self.mw, self.mh = -(-self.w // self.bw), -(-self.h // self.bh)
        self.total = self.mw * self.mh
        self.sampling = tuple((self.comps[c]["H"], self.comps[c]["V"]) for c, _, _ in self.scan)
        self.tables_id = hashlib.md5(repr(sorted((k, sorted(v.items())) for k, v in self.dht.items())).encode()).hexdigest()[:10]
        self.decode_key = (self.tables_id, self.sampling, tuple((td, ta) for _, td, ta in self.scan))

    def lut(self, key):
        ck = (self.tables_id,) + key
        lut = _LUT_CACHE.get(ck)
        if lut is None:
            lut = _LUT_CACHE[ck] = build_lut(self.dht[key])
            if len(_LUT_CACHE) > _LUT_CACHE_MAX:
                _LUT_CACHE.popitem(last=False)
        else:
            _LUT_CACHE.move_to_end(ck)
        return lut

    def layout(self):
        """Per block of an MCU: (dc_fast, dc_slow, ac_fast, ac_slow, component index)."""
        out = []
        for ci, (cid, td, ta) in enumerate(self.scan):
            c = self.comps[cid]
            dfast, dslow = self.lut((0, td)); afast, aslow = self.lut((1, ta))
            out += [(dfast, dslow, afast, aslow, ci)] * (c["H"] * c["V"])
        return out

    def dc_quant(self, ci):
        return self.dqt[self.comps[self.scan[ci][0]]["tq"]]


def build_lut(codes):
    """Two-level Huffman table: 9-bit direct lookup plus a dict for the rare longer codes."""
    fast, slow = [0] * (1 << FAST), {}
    for sym, (code, ln) in codes.items():
        v = (ln << 8) | sym
        if ln <= FAST:
            start = code << (FAST - ln)
            for k in range(start, start + (1 << (FAST - ln))):
                fast[k] = v
        else:
            slow[(ln << 16) | code] = v
    return fast, slow


def _slow(acc, nb, slow):
    for ln in range(FAST + 1, 17):
        v = slow.get((ln << 16) | ((acc >> (nb - ln)) & MASK[ln]))
        if v: return v
    return 0


def destuff(b):
    return b.replace(b"\xff\x00", b"\xff")


def stuff(b):
    return b.replace(b"\xff", b"\xff\x00")


def decode(buf, layout, ncomp, start_bit=0, stats=True, max_bits=None, stop_mcu=None):
    """Decode destuffed entropy data from start_bit.

    Returns (mcus, status, bit_position, dcs, flats) where status is 'eof' (ran cleanly
    to the end), 'limit' (reached max_bits cleanly), 'stop' (reached MCU number stop_mcu;
    bit_position is where it starts) or 'error'.
    """
    total_bits = len(buf) * 8
    if start_bit >= total_bits:
        return 0, "eof", total_bits, ([[] for _ in range(ncomp)], [[] for _ in range(ncomp)])
    limit = total_bits if max_bits is None else min(total_bits, start_bit + max_bits)
    data = buf + b"\xff" * 16
    pos = start_bit >> 3
    nb = 8 - (start_bit & 7)
    acc = data[pos] & MASK[nb]
    pos += 1
    pred = [0] * ncomp
    dcs = [[] for _ in range(ncomp)]; flats = [[] for _ in range(ncomp)]
    mcus = 0
    M = MASK
    try:
        while True:
            if mcus == stop_mcu:  # caller wants the bit position where this MCU starts
                return mcus, "stop", pos * 8 - nb, (dcs, flats)
            for dl, dsl, al, asl, ci in layout:
                if nb < 27:
                    acc = ((acc & M[nb]) << 24) | (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
                    pos += 3; nb += 24
                v = dl[(acc >> (nb - FAST)) & 0x1FF]
                if not v:
                    v = _slow(acc, nb, dsl)
                    if not v: raise LookupError
                nb -= v >> 8
                s = v & 0xFF
                if s:
                    if s > 11: raise LookupError
                    x = (acc >> (nb - s)) & M[s]; nb -= s
                    if x < (1 << (s - 1)): x -= M[s]
                    pred[ci] += x
                k = 1; nz = 0
                while k < 64:
                    if nb < 27:
                        acc = ((acc & M[nb]) << 24) | (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
                        pos += 3; nb += 24
                    v = al[(acc >> (nb - FAST)) & 0x1FF]
                    if not v:
                        v = _slow(acc, nb, asl)
                        if not v: raise LookupError
                    nb -= v >> 8
                    rs = v & 0xFF
                    s = rs & 15
                    if s == 0:
                        if rs == 0xF0: k += 16; continue
                        break
                    k += (rs >> 4) + 1; nb -= s; nz += 1
                if k > 64:
                    raise LookupError
                if stats:
                    dcs[ci].append(pred[ci]); flats[ci].append(nz == 0)
            used = pos * 8 - nb
            if used > total_bits:  # last MCU ran into padding: drop it
                return mcus, "eof", used, _trim(dcs, flats, layout, mcus)
            mcus += 1
            if used >= limit:
                return mcus, ("eof" if limit >= total_bits - 8 else "limit"), used, (dcs, flats)
    except (LookupError, IndexError):
        used = pos * 8 - nb
        if used >= total_bits - 24:
            return mcus, "eof", used, _trim(dcs, flats, layout, mcus)
        return mcus, "error", used, (dcs, flats)


def dc_spans(buf, layout, bit):
    """Decode the single MCU starting at `bit`; return [(start, end, component, diff)] for the
    DC code of each component's first block, so it can be re-encoded in place."""
    def read(n):
        nonlocal bit
        v = 0
        for _ in range(n):
            v = (v << 1) | ((buf[bit >> 3] >> (7 - (bit & 7))) & 1); bit += 1
        return v

    def sym(fast, slow):
        nonlocal bit
        for ln in range(1, 17):
            code = 0
            b = bit
            for _ in range(ln):
                code = (code << 1) | ((buf[b >> 3] >> (7 - (b & 7))) & 1); b += 1
            if ln <= FAST:
                v = fast[code << (FAST - ln)]
                if v and (v >> 8) == ln:
                    bit = b; return v & 0xFF
            else:
                v = slow.get((ln << 16) | code)
                if v:
                    bit = b; return v & 0xFF
        raise LookupError("bad Huffman code")

    out, seen = [], set()
    for dl, dsl, al, asl, ci in layout:
        b0 = bit
        s = sym(dl, dsl)
        diff = 0
        if s:
            x = read(s); diff = x if x >= (1 << (s - 1)) else x - (1 << s) + 1
        if ci not in seen:
            seen.add(ci); out.append((b0, bit, ci, diff))
        k = 1
        while k < 64:
            rs = sym(al, asl); s = rs & 15
            if s == 0:
                if rs == 0xF0: k += 16; continue
                break
            k += (rs >> 4) + 1; read(s)
    return out


def _trim(dcs, flats, layout, mcus):
    per = {}
    for *_, ci in layout: per[ci] = per.get(ci, 0) + 1
    for ci, n in per.items():
        del dcs[ci][mcus * n:]; del flats[ci][mcus * n:]
    return dcs, flats


def sync(buf, layout, ncomp, max_skip_bytes=1024, probe_bits=None):
    """Find the first bit offset from which the stream decodes without errors.

    With probe_bits, only that many bits are checked (fast table-match test).
    Returns (start_bit, mcus, status, dcs, flats) or None.
    """
    start = 0
    while start < min(max_skip_bytes, len(buf)) * 8:
        mcus, status, used, (dcs, flats) = decode(buf, layout, ncomp, start, stats=probe_bits is None,
                                                  max_bits=probe_bits)
        if status != "error" and mcus > 8:
            return start, mcus, status, dcs, flats
        start = used + 1
    return None
