"""STOP/Djvu photo recovery engine.

Facts measured from verified original/encrypted file pairs (see docs/djvu-format.md):
  * bytes [5, 153605) are encrypted (Salsa20, per-file key wrapped with RSA-2048),
  * a 334-byte trailer (256-byte key blob + 40-char ID + marker) is appended,
  * every other byte is the untouched original.

Recovery paths, best first (see docs/methods.md):
  copy     - an untouched copy of the same file exists (100%)
  header   - the image's own JPEG header lies past the encrypted area (near 100%)
  restart  - restart markers pin every surviving block exactly (exact position + colours)
  align    - no restart markers: borrow a same-camera header, realign, estimate colours
  infer    - no same-camera header: infer width and quant tables from the data itself
"""
import collections, io, json, os, re
import numpy as np
from PIL import Image, ImageFile
from . import fastjpeg as fj
from . import colour

ImageFile.LOAD_TRUNCATED_IMAGES = True
# Bump when results change, so the app redoes photos repaired by an older engine (CHANGELOG.md).

def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


ENGINE_VERSION = "3.3"
REDO_METHODS = ("align", "infer", "restart", None)  # copy/header results are exact and never redone
Image.MAX_IMAGE_PIXELS = None
MARKER = b"{36A698B9-D67C-4E07-BE82-0EC5B14B4DF5}"  # one variant's filemarker; see has_marker()
MARKER_LEN = len(MARKER)
# STOP/Djvu variants append a filemarker that is a GUID in braces; only the GUID differs per
# variant, so match the shape instead of one hard-coded value.
MARKER_RE = re.compile(rb"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}")
TRAILER = 334


def has_marker(tail):
    """True if `tail` (the last MARKER_LEN bytes of a file) is any STOP/Djvu filemarker."""
    return MARKER_RE.fullmatch(tail) is not None
ENC_END = 153605
EOI = bytes((0xFF, 0xD9))
RST = re.compile(rb"\xff[\xd0-\xd7]")
JPEG_EXT = (".jpg", ".jpeg", ".jfif")


# ---------------------------------------------------------------- donor library
def layout_key(D):
    blocks = tuple(D.comps[cid]["H"] * D.comps[cid]["V"] for cid, _, _ in D.scan)
    return (D.tables_id, blocks, tuple((td, ta) for _, td, ta in D.scan))


def read_header(path, limit=128 << 10):
    """Parse only the start of a JPEG (headers are almost always within 128 KB); read more
    only if needed. Keeps whole-drive learning light on disks."""
    with open(path, "rb") as fh:
        data = fh.read(limit)
        try:
            return fj.Donor(data, path)
        except Exception:
            if len(data) < limit:  # that was the whole file: not a usable JPEG
                raise
        data += fh.read()
    return fj.Donor(data, path)


# Folder names never worth reading when learning from a whole drive: system files, program
# files and app caches (web thumbnails, icons) are not camera photos.
SYSTEM_DIR_NAMES = {"windows", "windows.old", "program files", "program files (x86)", "programdata",
                    "appdata", "$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws",
                    "recovery", "perflogs", "msocache", "node_modules", ".git", "site-packages"}
MIN_DONOR_PIXELS = 640 * 480  # smaller images are web thumbnails, not useful camera headers


def library_key(D):
    return (repr(layout_key(D)), D.sampling, D.dri, D.w, D.h)


def scan_library(roots, skip=(), progress=None, should_stop=None, skip_names=SYSTEM_DIR_NAMES):
    """Find one healthy baseline JPEG per (tables, layout, restart interval, size).
    Only headers are read and kept (no image data). `skip`: path substrings to avoid;
    `skip_names`: folder names to avoid anywhere; `should_stop()`: return True to stop early."""
    seen, counts, n = {}, collections.Counter(), 0
    for root in roots:
        for dp, dns, fs in os.walk(root):
            low = dp.lower()
            if any(s in low for s in skip):
                dns[:] = []; continue
            dns[:] = [d for d in dns if d.lower() not in skip_names]
            for f in fs:
                if not f.lower().endswith(JPEG_EXT): continue
                if should_stop and should_stop():
                    return _entries(seen, counts)
                p = os.path.join(dp, f); n += 1
                if progress and n % 200 == 0: progress(n)
                try:
                    D = read_header(p)
                except Exception:
                    continue
                if len(D.scan) not in (1, 3) or D.w * D.h < MIN_DONOR_PIXELS: continue
                lk = layout_key(D); counts[repr(lk)] += 1
                seen.setdefault(library_key(D), (p, D.segs.hex()))
    if progress: progress(n)
    return _entries(seen, counts)


def _entries(seen, counts):
    return [dict(path=p, segs=s, layout=k[0], count=counts[k[0]]) for k, (p, s) in seen.items()]


def merge_library(old, new):
    """Add newly learned camera types to an existing library. Returns (merged, added)."""
    def key(e):
        try:
            return library_key(fj.Donor(bytes.fromhex(e["segs"])))
        except Exception:
            return None
    merged = {key(e): e for e in old}
    merged.pop(None, None)
    pop = collections.Counter()
    for e in old: pop[e["layout"]] = max(pop[e["layout"]], e.get("count", 1))
    added = 0
    newpop = collections.Counter()
    for e in new: newpop[e["layout"]] = max(newpop[e["layout"]], e.get("count", 1))
    for e in new:
        k = key(e)
        if k is not None and k not in merged:
            merged[k] = e; added += 1
    for layout, c in newpop.items(): pop[layout] += c  # popularity orders which cameras are tried first
    out = list(merged.values())
    for e in out: e["count"] = pop[e["layout"]]
    return out, added


class Library:
    MAX_GROUPS = 300  # align path tries at most this many table sets, most common first

    def __init__(self, entries):
        self.groups = collections.defaultdict(list)  # layout repr -> [Donor]
        self.pop = collections.Counter()
        for e in entries:
            try:
                D = fj.Donor(bytes.fromhex(e["segs"]), e["path"]) if "segs" in e else read_header(e["path"])
            except Exception:
                continue
            self.groups[repr(layout_key(D))].append(D)
            self.pop[repr(layout_key(D))] = max(self.pop[repr(layout_key(D))], e.get("count", 1))
        self.order = [k for k, _ in self.pop.most_common()]
        self.hint = {}  # folder -> what worked last (tables layout, width, sampling, quant tables)
        self.misses = {}  # folder -> photos for which no camera tables matched
        self.qbounds = {}  # camera key -> per-photo upper bounds on the luma DC step
        self.chroma_ratio = {}  # camera key -> chroma/luma DC step ratio decided from a folder survey
        self.dqts = collections.defaultdict(collections.Counter)
        self.sizes = []
        for ds in self.groups.values():
            for D in ds:
                self.dqts[D.tables_id][dqt_of(D)] += self.pop[repr(layout_key(D))]
                self.sizes.append((D.w, D.h))


# ---------------------------------------------------------------- stream building
def bits_of(code, ln):
    return format(code, "0%db" % ln) if ln else ""


def blank_mcu(D, diffs=None):
    """Bit string for one MCU of flat blocks; diffs adds DC steps to each component's first block."""
    out = []
    for ci, (cid, td, ta) in enumerate(D.scan):
        c = D.comps[cid]
        for b in range(c["H"] * c["V"]):
            diff = diffs[ci] if (diffs and b == 0) else 0
            cat = abs(diff).bit_length()
            code, ln = D.dht[(0, td)][cat]
            out.append(bits_of(code, ln))
            if cat:
                out.append(format(diff if diff > 0 else diff + (1 << cat) - 1, "0%db" % cat))
            code, ln = D.dht[(1, ta)][0x00]
            out.append(bits_of(code, ln))
    return "".join(out)


def correction_mcus(D, diffs):
    """One or more MCUs that step the DC predictors by `diffs`, using only categories the tables have."""
    dc_cats = [max(D.dht[(0, td)]) for _, td, _ in D.scan]
    rem, out = list(diffs), []
    while any(rem):
        step = []
        for ci, r in enumerate(rem):
            lim = (1 << dc_cats[ci]) - 1
            s = max(-lim, min(lim, r)); step.append(s); rem[ci] -= s
        out.append(blank_mcu(D, step))
    return out


def assemble(D, prefix_bits, buf, start_bit):
    """Donor header + prefix bits + surviving entropy bits from start_bit, byte-stuffed."""
    tail_len = len(buf) * 8 - start_bit
    tail = int.from_bytes(buf, "big") & ((1 << tail_len) - 1)
    total = len(prefix_bits) + tail_len
    val = ((int(prefix_bits, 2) if prefix_bits else 0) << tail_len) | tail
    pad = -total % 8
    val = (val << pad) | ((1 << pad) - 1)
    return D.segs + fj.stuff(val.to_bytes((total + pad) // 8, "big")) + b"\xff\xd9"


def quality(jpg, top_rows, D):
    """Block-seam ratio below the lost area: ~1-1.8 for a real picture, larger for garbage."""
    im = Image.open(io.BytesIO(jpg))
    scale = 1
    if D.w * D.h > 4_000_000:
        im.draft("L", (D.w // 2, D.h // 2)); scale = 2
    img = np.asarray(im.convert("L"), np.float32)
    bw, bh = D.bw // scale, D.bh // scale
    img = img[(top_rows // scale) + bh:]
    if img.shape[0] < 4 * bh or img.shape[1] < 4 * bw:
        return float("inf")
    dy = np.abs(np.diff(img, axis=0)); dx = np.abs(np.diff(img, axis=1))
    ry = (np.arange(dy.shape[0]) + 1) % bh == 0; rx = (np.arange(dx.shape[1]) + 1) % bw == 0
    inner = dy[~ry].mean() + dx[:, ~rx].mean()
    if inner < 0.5:
        return float("inf")
    return float((dy[ry].mean() + dx[:, rx].mean()) / inner)


# ---------------------------------------------------------------- recovery paths
def split_file(e):
    """-> (original_length, plaintext region, offset of scan end within it or None)."""
    if not has_marker(e[-MARKER_LEN:]):
        raise ValueError("not a STOP/Djvu file")
    olen = len(e) - TRAILER
    if olen <= ENC_END + 64:
        raise ValueError("whole file is inside the encrypted 150 KB")
    return olen, e[ENC_END:olen]


def first_marker(plain, start=0):
    """First real JPEG marker (not stuffing, not RST) - normally the main image's EOI."""
    i = start
    while True:
        i = plain.find(b"\xff", i)
        if i < 0 or i + 1 >= len(plain): return None
        b = plain[i + 1]
        if b != 0x00 and b != 0xFF and not (0xD0 <= b <= 0xD7):
            return i
        i += 1


def try_header_path(plain):
    """If a full DQT/DHT/SOF/SOS header sits in the plaintext, rebuild the whole image."""
    best = None
    for m in re.finditer(rb"\xff[\xdb\xc4\xc0\xc1\xdd]", plain[:4 << 20]):
        i, seen = m.start(), set()
        try:
            while True:
                if plain[i] != 0xFF: break
                mk = plain[i + 1]
                L = int.from_bytes(plain[i + 2:i + 4], "big")
                if L < 2: break
                if mk in (0xDB, 0xC4, 0xC0, 0xC1, 0xDD) or 0xE0 <= mk <= 0xEF or mk == 0xFE:
                    seen.add(mk); i += 2 + L; continue
                if mk == 0xDA and {0xDB, 0xC4} <= seen and (0xC0 in seen or 0xC1 in seen):
                    jpg = b"\xff\xd8" + plain[m.start():]
                    D = fj.Donor(jpg)
                    if best is None or D.w * D.h > best[0]:
                        end = first_marker(plain, m.start() + (i - m.start()) + 2 + L)
                        body = plain[m.start():end] if end else plain[m.start():]
                        best = (D.w * D.h, b"\xff\xd8" + body + b"\xff\xd9", D)
                break
        except Exception:
            continue
    if best and best[0] >= 640 * 480:
        return dict(jpg=best[1], frac=1.0, q=1.0, method="header", dims=f"{best[2].w}x{best[2].h}")
    return None


def restart_path(plain, scan_end, lib, folder, crop=False):
    data = plain[:scan_end]
    ms = [(m.start(), data[m.start() + 1] - 0xD0) for m in RST.finditer(data)]
    if len(ms) < 3: return None
    run = [ms[-1]]
    for pos, num in reversed(ms[:-1]):
        if (num + 1) % 8 == run[-1][1]: run.append((pos, num))
        else: break
    run.reverse()
    if len(run) < 3: return None
    first, num0 = run[0]; K = len(run)
    pos = [p for p, _ in run]
    trailing = scan_end - pos[-1] <= 6
    last_seg = data[pos[-2] + 2:pos[-1]] if trailing else data[pos[-1] + 2:]
    full = K - 1 if trailing else K
    mid_seg = data[pos[0] + 2:pos[1]]
    segs = [data[pos[i] + 2:pos[i + 1]] for i in range(len(pos) - 1)] + ([] if trailing else [last_seg])
    best, counted, absdc = None, {}, {}
    for D in (D for g in lib.groups.values() for D in g if D.dri):
        lk = repr(layout_key(D))
        if lk not in counted:
            lay = D.layout(); n = len(D.scan)
            mid = fj.decode(fj.destuff(mid_seg), lay, n, stats=False)
            last = fj.decode(fj.destuff(last_seg), lay, n, stats=False)
            counted[lk] = (mid[0] if mid[1] == "eof" else -1, last[0] if last[1] == "eof" else -1)
        mid_n, last_n = counted[lk]
        total, dri = D.total, D.dri
        n_int = -(-total // dri); lost = n_int - full
        if mid_n != dri or last_n != total - dri * (n_int - 1): continue
        if lost < 1 or (lost - 1) % 8 != num0: continue
        if lk not in absdc:  # every segment restarts its DC predictors: these values are absolute
            acc = [[] for _ in D.scan]
            for sg in segs:
                _, _, _, (dc, _) = fj.decode(fj.destuff(sg), D.layout(), len(D.scan))
                for c in range(len(D.scan)): acc[c] += dc[c]
            absdc[lk] = acc
        bounds = camera_bounds(lib, (folder, D.tables_id, D.w, D.h), absdc[lk], True)
        bl = blank_mcu(D) * dri
        bl += "1" * (-len(bl) % 8)
        blank = fj.stuff(int(bl, 2).to_bytes(len(bl) // 8, "big"))
        body = bytearray()
        for i in range(lost):
            body += blank
            if i < lost - 1: body += bytes((0xFF, 0xD0 + i % 8))
        body += data[first:] + EOI
        for dqt in tables_for(lib, D, bounds, True):
            hdr = make_header(D, D.w, D.h, None, dqt, keep_dri=True)
            jpg = hdr + bytes(body)
            q = quality(jpg, lost * dri // D.mw * D.bh, D)
            r = dict(jpg=jpg, frac=full * dri / total, q=q, method="restart", dims=f"{D.w}x{D.h}",
                     _rc=(D, dqt, lost, dri))
            if best is None or q < best["q"]: best = r
    if best is not None:
        D, dqt, lost, dri = best.pop("_rc")
        if crop:
            best["jpg_cropped"] = crop_restart(D, dqt, lost, dri, data, pos)
    return best


def render_full(D, w_px, h_px, sampling, dqt, buf, start, mcus, dcs, flats):
    hdr = make_header(D, w_px, h_px, sampling, dqt)
    Dm = fj.Donor(hdr)
    lost = Dm.total - mcus
    corr = colour.estimate_v2(Dm, dcs, flats)
    try:
        cm = correction_mcus(Dm, corr)
    except KeyError:
        return None
    if lost < len(cm): return None
    prefix = blank_mcu(Dm) * (lost - len(cm)) + "".join(cm)
    jpg = assemble(Dm, prefix, buf, start)
    q = quality(jpg, (lost // Dm.mw) * Dm.bh, Dm)
    return dict(jpg=jpg, frac=mcus / Dm.total, q=q, dims=f"{w_px}x{h_px}",
                _args=(D, w_px, h_px, sampling, dqt if dqt is not None else dqt_of(D)), _Dm=Dm, _corr=corr)


def align_path(plain, scan_end, lib, folder, crop=False):
    """No restart markers: find the camera's tables, infer width from the data, pick quant tables."""
    buf = fj.destuff(plain[:scan_end])
    fb = len(buf) / (len(plain) + ENC_END)
    hint = lib.hint.get(folder)
    order = list(lib.order[:lib.MAX_GROUPS])
    if hint and hint["lk"] in lib.groups:
        if hint["lk"] in order: order.remove(hint["lk"])
        order.insert(0, hint["lk"])
    # deep search: if the common table sets fail, try every other camera in the library
    # (memory-safe now; skipped for a folder after two photos in it found nothing)
    if lib.misses.get(folder, 0) < 2:
        order += lib.order[lib.MAX_GROUPS:]
    decoded, tables = [], None
    for lk in order:
        donors = lib.groups[lk]
        D0 = donors[0]
        if len(D0.scan) != 3: continue
        if tables and D0.tables_id != tables: continue
        lay = D0.layout()
        probe = fj.sync(buf, lay, 3, probe_bits=min(len(buf) * 8, 300_000))
        if not probe: continue
        start = probe[0]
        mcus, status, _, (dcs, flats) = fj.decode(buf, lay, 3, start)
        if status != "eof" or mcus < 64: continue
        nY = D0.comps[D0.scan[0][0]]["H"] * D0.comps[D0.scan[0][0]]["V"]
        ws = width_scores(mcu_luma(dcs, nY))
        if not ws: continue
        decoded.append(dict(score=ws[0][0], lk=lk, start=start, mcus=mcus, dcs=dcs, flats=flats,
                            widths=[mw for _, mw in ws[:3]], donors=donors, nY=nY))
        tables = D0.tables_id  # only try sibling layouts of the same camera tables from here on
    if not decoded:
        lib.misses[folder] = lib.misses.get(folder, 0) + 1
        return None
    dec = min(decoded, key=lambda d: d["score"])
    lk, start, mcus, dcs, flats, donors = dec["lk"], dec["start"], dec["mcus"], dec["dcs"], dec["flats"], dec["donors"]
    results = []
    # 1) a healthy photo from the same camera with the inferred width: use its exact header
    exact = []
    for D in donors:
        if D.mw in dec["widths"][:2] and D.total > mcus:
            fm = mcus / D.total
            if 0.5 * fb - 0.05 <= fm <= min(1.0, 1.6 * fb + 0.12):
                exact.append((abs(fm - 1.05 * fb), D))
    bounds = camera_bounds(lib, (folder, lib.groups[lk][0].tables_id, dec["nY"], dec["widths"][0]), dcs, False)
    for _, D in sorted(exact, key=lambda t: t[0])[:3]:
        for dqt in tables_for(lib, D, bounds, False)[:3]:
            r = render_full(D, D.w, D.h, None, dqt, buf, start, mcus, dcs, flats)
            if r: r["method"] = "align"; results.append(r)
    # 2) no such photo (or poor fit): infer size and search quant tables
    if not results or min(r["q"] for r in results) >= 2.0:
        D0 = donors[0]
        for mw in dec["widths"][:2]:
            for samp in arrangements(dec["nY"]):
                bw, bh = 8 * samp[0], 8 * samp[1]
                w_px = mw * bw
                snap = [ww for ww, _ in lib.sizes if -(-ww // bw) == mw]
                if snap: w_px = max(set(snap), key=snap.count)
                cands = tables_for(lib, D0, bounds, False)
                if hint and hint.get("mw") == mw and hint.get("samp") == samp and hint.get("dqt") in cands:
                    dqt = hint["dqt"]
                else:
                    scored = []
                    for d in cands:
                        try:
                            scored.append((mini_render_quality(D0, w_px, mw, bh, d, [samp, (1, 1), (1, 1)], buf, start,
                                                               colour.estimate_v2, mcus, dcs, flats), d))
                        except Exception:
                            continue
                    if not scored: continue
                    dqt = min(scored, key=lambda t: t[0])[1]
                h_px = pick_height(w_px, bh, mw, mcus, fb, lib.sizes)
                r = render_full(D0, w_px, h_px, [samp, (1, 1), (1, 1)], dqt, buf, start, mcus, dcs, flats)
                if r:
                    r.update(method="infer", _hint=dict(lk=lk, mw=mw, samp=samp, dqt=dqt)); results.append(r)
    if not results: return None
    best = min(results, key=lambda r: r["q"])
    lib.hint[folder] = best.pop("_hint", None) or dict(lk=lk)
    best = apply_chroma_ratio(best, lib, folder, lib.groups[lk][0], dec["nY"], buf, start, mcus, dcs, flats)
    if crop:
        best["jpg_cropped"] = crop_align(best, buf, start, mcus)
    for r in results + [best]:
        for k in ("_hint", "_args", "_Dm", "_corr"): r.pop(k, None)
    return best


def recover(path, lib, twin=None, crop=False):
    """Return dict(status, method, jpg|copy_from, frac, q, dims) or raise ValueError.
    With crop=True, results that have a grey (lost) area also get "jpg_cropped": the same
    photo without the grey rows, built losslessly (None if it can't be cropped)."""
    if twin:
        return dict(status="ok", method="copy", copy_from=twin, frac=1.0, q=1.0, dims="")
    e = _read(path)
    olen, plain = split_file(e)
    r = try_header_path(plain)
    if r:
        r["status"] = "ok"; return r
    scan_end = first_marker(plain)
    if scan_end is None: scan_end = len(plain)
    r = restart_path(plain, scan_end, lib, os.path.dirname(path), crop)
    if r is None:
        r = align_path(plain, scan_end, lib, os.path.dirname(path), crop)
    if r is None:
        raise ValueError("no matching camera found among healthy photos")
    r["status"] = "ok" if r["method"] == "restart" or r["q"] < 2.0 else "review"
    return r


# ---------------------------------------------------------------- donor-free geometry (v3.1)
ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14,
          21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60,
          61, 54, 47, 55, 62, 63]
IJG_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29,
            51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121,
            120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
IJG_CHROMA = [17, 18, 24, 47] + [99] * 4 + [18, 21, 26, 66] + [99] * 4 + [24, 26, 56] + [99] * 5 + [47, 66] + [99] * 6 + [99] * 32


def ijg_dqt(quality):
    s = 5000 // quality if quality < 50 else 200 - 2 * quality
    seg = b""
    for tid, base in ((0, IJG_LUMA), (1, IJG_CHROMA)):
        q = [min(255, max(1, (base[ZIGZAG[k]] * s + 50) // 100)) for k in range(64)]
        seg += b"\xff\xdb\x00\x43" + bytes([tid]) + bytes(q)
    return seg


def segments(segs):
    i, out = 2, []
    while i < len(segs):
        L = int.from_bytes(segs[i + 2:i + 4], "big"); out.append((segs[i + 1], segs[i:i + 2 + L])); i += 2 + L
    return out


def dqt_of(D):
    return b"".join(s for m, s in segments(D.segs) if m == 0xDB)


def make_header(D, w, h, sampling=None, dqt=None, keep_dri=False):
    """Donor header with new size, optional new chroma sampling arrangement and quant tables (DRI dropped unless kept)."""
    out, put_dqt = bytearray(b"\xff\xd8"), False
    for m, s in segments(D.segs):
        if m == 0xDB:
            if dqt is None: out += s
            elif not put_dqt: out += dqt; put_dqt = True
        elif m in (0xC0, 0xC1):
            s = bytearray(s); s[5:7] = h.to_bytes(2, "big"); s[7:9] = w.to_bytes(2, "big")
            if sampling:
                for k, (H, V) in enumerate(sampling):
                    s[11 + 3 * k] = (H << 4) | V
            out += s
        elif m == 0xDD and not keep_dri:
            continue
        else:
            out += s
    return bytes(out)


def mcu_luma(dcs, nY):
    y = np.asarray(dcs[0], np.float64)
    n = len(y) // nY
    return y[:n * nY].reshape(n, nY).mean(1)


def width_scores(ym, lo=8, hi=800):
    """DC-thumbnail coherence per MCU-row width: vertical/horizontal neighbour difference (lower is better)."""
    n = len(ym); res = []
    for mw in range(lo, min(hi, n // 4) + 1):
        col0 = (-n) % mw
        a = np.concatenate([np.full(col0, np.nan), ym]).reshape(-1, mw)
        v = np.nanmean(np.abs(a[1:] - a[:-1])); hz = np.nanmean(np.abs(np.diff(a, axis=1)))
        res.append((v / (hz + 1e-6), mw))
    res.sort()
    return res


def arrangements(blocks_y):
    if blocks_y == 4: return [(2, 2)]
    if blocks_y == 2: return [(2, 1), (1, 2)]
    if blocks_y == 1: return [(1, 1)]
    return []


def pick_height(w_px, bh, mw, mcus, fb, sizes):
    """Most plausible full-frame height for this width; else just the recovered rows."""
    cands = {round(w_px * r) for r in (3 / 4, 4 / 3, 9 / 16, 16 / 9, 1.0, 2 / 3, 3 / 2)}
    cands |= {hh for ww, hh in sizes if abs(ww - w_px) < 16}
    best = None
    for h_px in cands:
        mh = -(-h_px // bh)
        if mh * mw <= mcus: continue
        fm = mcus / (mh * mw)
        if 0.5 * fb - 0.05 <= fm <= min(1.0, 1.6 * fb + 0.12):
            score = abs(fm - 1.05 * fb)
            if best is None or score < best[0]: best = (score, h_px)
    if best: return best[1]
    return -(-mcus // mw) * bh  # recovered area only


def mini_render_quality(D, w_px, mw, bh_rows, dqt, sampling, buf, start, corr_fn, mcus, dcs, flats):
    """Render ~24 MCU rows with a candidate quant table; return seam quality."""
    rows = min(24, max(6, mcus // mw - 1))
    hdr = make_header(D, w_px, rows * bh_rows, sampling, dqt)
    Dm = fj.Donor(hdr)
    corr = corr_fn(Dm, dcs, flats)
    cm = correction_mcus(Dm, corr)
    col0 = (-mcus) % mw
    prefix = blank_mcu(Dm) * ((col0 - len(cm)) % mw) + "".join(cm)
    need_bits = int(len(buf) * 8 * min(1.0, (rows + 2) * mw / mcus)) + start
    nbytes = min(len(buf), need_bits // 8 + 64)
    jpg = assemble(Dm, prefix, buf[:nbytes], start)
    return quality(jpg, bh_rows, Dm)




# ---------------------------------------------------------------- quant-table choice (v3.2)
# A wrong quant table rescales every block: too coarse -> crushed blacks and garish colours.
# Real pixels live in 0..255, so a table is only possible if every visible block stays in range.
# Tested on 150 healthy photos: the tightest allowed DC step was within 1.25x of the true step
# for 82% (spread rule) / 97% (absolute rule), and never below it.
def dqt_q0(dqt):
    """{table id: DC step} from DQT segment bytes."""
    out, i = {}, 0
    while i + 4 <= len(dqt) and dqt[i] == 0xFF and dqt[i + 1] == 0xDB:
        L = int.from_bytes(dqt[i + 2:i + 4], "big"); p = dqt[i + 4:i + 2 + L]; j = 0
        while j < len(p):
            pq, tq = p[j] >> 4, p[j] & 15; n = 128 if pq else 64
            out[tq] = int.from_bytes(p[j + 1:j + 3], "big") if pq else p[j + 1]
            j += 1 + n
        i += 2 + L
    return out


def _bound(vals, absolute):
    v = np.asarray(vals, np.float64)
    if len(v) < 50: return None
    lo, hi = np.percentile(v, 0.1), np.percentile(v, 99.9)
    if absolute: return 128 * 8 / max(abs(lo), abs(hi), 1.0)
    return 255 * 8 / max(hi - lo, 1.0)


def camera_bounds(lib, key, dcs, absolute):
    """Largest DC steps (luma, chroma) that keep this photo's blocks inside 0..255, tightened by
    the other photos of the same camera (all photos of a camera share one table)."""
    by = _bound(dcs[0], absolute)
    if by is None: return None
    bc = min([b for b in (_bound(dcs[c], absolute) for c in range(1, len(dcs))) if b] or [999.0])
    seen = lib.qbounds.setdefault(key, [])
    seen.append(by)
    if len(seen) >= 3:
        by = min(by, sorted(seen)[1])  # tightest bound, ignoring one outlier
    return by, bc


def tables_for(lib, D, bounds, absolute):
    """Candidate quant tables (up to 6) whose DC steps fit the bounds, nearest the likely true step."""
    own = dqt_of(D)
    if not bounds: return [own]
    by, bc = bounds
    ty = D.comps[D.scan[0][0]]["tq"]
    tc = D.comps[D.scan[1][0]]["tq"] if len(D.scan) > 1 else None
    target = by / (1.02 if absolute else 1.07)
    cands, have = [], set()
    for d in [own] + [x for x, _ in lib.dqts[D.tables_id].most_common(80)] + [ijg_dqt(q) for q in range(30, 101)]:
        if d in have: continue
        have.add(d)
        q0 = dqt_q0(d); sy, sc = q0.get(ty), q0.get(tc, 1)
        if sy and sy <= by * 1.03 and sc <= bc * 1.03:
            cands.append((sy, d))
    if not cands:
        return [ijg_dqt(100)]
    best = min({sy for sy, _ in cands}, key=lambda v: abs(np.log(v / target)))
    return [d for sy, d in cands if sy == best][:6]


# ---------------------------------------------------------------- chroma step (v3.3)
# Every camera in the library uses chroma DC step == luma DC step, but some (e.g. Canon
# compacts) store colour more coarsely. Assuming 1:1 for those gives ashy, faded colours.
CHROMA_TARGET = 70.0  # median max(Cb, Cr) spread of real photos, pixel levels (measured on 200 photos)


def _gamut_ceiling(Y, Cb, Cr, keep=0.995):
    """Largest chroma scale keeping R, G, B of block means inside 0..255 for `keep` of blocks."""
    def ok(s):
        R = Y + 1.402 * s * Cr; G = Y - 0.344136 * s * Cb - 0.714136 * s * Cr; B = Y + 1.772 * s * Cb
        return ((R < -3) | (R > 258) | (G < -3) | (G > 258) | (B < -3) | (B > 258)).mean() <= 1 - keep
    lo, hi = 0.05, 20.0
    if ok(hi): return hi
    for _ in range(28):
        mid = (lo * hi) ** 0.5
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def chroma_ratio_evidence(D, dcs, flats, corr=None):
    """(spread-based estimate, gamut ceiling) of chroma step / luma step, assuming luma step = D's."""
    nY = D.comps[D.scan[0][0]]["H"] * D.comps[D.scan[0][0]]["V"]
    m = min(len(dcs[0]) // nY, len(dcs[1]), len(dcs[2]))
    if m < 100: return (1.0, 1.0)
    qy = D.dc_quant(0)
    if corr is None:
        corr = colour.estimate_v2(D, dcs, flats)
    Y = (np.asarray(dcs[0][:m * nY], float).reshape(m, nY).mean(1) + corr[0]) * qy / 8 + 128
    Cb = (np.asarray(dcs[1][:m], float) + corr[1]) * qy / 8
    Cr = (np.asarray(dcs[2][:m], float) + corr[2]) * qy / 8
    spread = max(np.percentile(Cb, 99.9) - np.percentile(Cb, 0.1), np.percentile(Cr, 99.9) - np.percentile(Cr, 0.1), 1.0)
    return (CHROMA_TARGET / spread, _gamut_ceiling(Y, Cb, Cr))


def chroma_ratio_decide(spread_est, ceiling):
    """1 unless both signals say colour is stored at least ~1.7x coarser; then the nearest of 2 or 3."""
    s, g = np.asarray(spread_est, float), np.asarray(ceiling, float)
    coarse = (s >= 1.7) & (g >= 1.7)
    pick = np.where(coarse, np.where(np.minimum(s, g) >= 2.6, 3, 2), 1)
    return pick


def camera_chroma_ratio(evidence):
    """Chroma/luma step ratio for a camera from per-photo (spread estimate, gamut ceiling) pairs.
    Pooled medians are far steadier than one photo; the gamut ceiling vetoes too-coarse choices."""
    e = np.asarray(evidence, float).reshape(-1, 2)
    if len(e) < 3: return 1
    s, g = float(np.median(e[:, 0])), float(np.median(e[:, 1]))
    # conservative: never switch a normal (1:1) camera by mistake (100% on 8-photo pools);
    # borderline 2:1 cameras may stay 1:1 (slightly pale) rather than risk garish colours
    if s >= 3.0 and g >= 3.2: return 3
    if s >= 1.7 and g >= 2.0: return 2
    return 1


def scale_chroma(dqt, D, ratio):
    """Same quant tables with the chroma table's steps multiplied by `ratio`."""
    tc = D.comps[D.scan[1][0]]["tq"]
    out, i = bytearray(), 0
    while i < len(dqt):
        L = int.from_bytes(dqt[i + 2:i + 4], "big"); p = dqt[i + 4:i + 2 + L]; j = 0
        while j < len(p):
            pq, tq = p[j] >> 4, p[j] & 15; n = 128 if pq else 64
            vals = p[j + 1:j + 1 + n]
            if tq == tc:
                if pq:
                    q = [min(65535, int.from_bytes(vals[2 * k:2 * k + 2], "big") * ratio) for k in range(64)]
                    vals = b"".join(v.to_bytes(2, "big") for v in q)
                else:
                    vals = bytes(min(255, v * ratio) for v in vals)
            out += b"\xff\xdb" + (3 + len(vals)).to_bytes(2, "big") + bytes([p[j]]) + vals
            j += 1 + n
        i += 2 + L
    return bytes(out)


def survey_chroma(lib, key, folder, D0, Dm, nY, mw):
    """Decide a camera's chroma/luma step ratio from up to 8 of its photos in this folder.
    The sample is deterministic, so every worker reaches the same decision."""
    if key in lib.chroma_ratio:
        return lib.chroma_ratio[key]
    try:
        names = sorted(f for f in os.listdir(folder) if f.lower().endswith((".jpg.coot", ".jpeg.coot")))
    except OSError:
        names = []
    files = [os.path.join(folder, f) for f in names]
    files = [f for f in files if os.path.getsize(f) > ENC_END + 200_000]
    step = max(1, len(files) // 12)
    ev, lay = [], D0.layout()
    for f in files[::step][:12]:
        try:
            e = _read(f); _, plain = split_file(e)
            b2 = fj.destuff(plain[:first_marker(plain) or len(plain)])
            pr = fj.sync(b2, lay, 3, probe_bits=min(len(b2) * 8, 300_000))
            if not pr: continue
            m, st, _, (dc2, fl2) = fj.decode(b2, lay, 3, pr[0])
            if st != "eof" or m < 1000: continue
            ws = width_scores(mcu_luma(dc2, nY))
            if not ws or ws[0][1] != mw: continue  # a different camera / image size
            ev.append(chroma_ratio_evidence(Dm, dc2, fl2))
        except Exception:
            continue
        if len(ev) >= 8: break
    ratio = camera_chroma_ratio(ev)
    lib.chroma_ratio[key] = ratio
    return ratio


def apply_chroma_ratio(best, lib, folder, D0, nY, buf, start, mcus, dcs, flats):
    """If this camera stores colour more coarsely than brightness, re-render with a scaled chroma table."""
    args, Dm = best.get("_args"), best.get("_Dm")
    if not args or Dm is None:
        return best
    D, w_px, h_px, sampling, dqt = args
    q0 = dqt_q0(dqt)
    ty, tc = Dm.comps[Dm.scan[0][0]]["tq"], Dm.comps[Dm.scan[1][0]]["tq"]
    if q0.get(tc, 1) > 1.25 * q0.get(ty, 1):
        return best  # the chosen table already stores colour more coarsely
    ratio = survey_chroma(lib, (folder, D0.tables_id, nY, Dm.mw), folder, D0, Dm, nY, Dm.mw)
    if ratio <= 1:
        return best
    r = render_full(D, w_px, h_px, sampling, scale_chroma(dqt, Dm, ratio), buf, start, mcus, dcs, flats)
    if not r:
        return best
    r["method"] = best["method"]; r["chroma_ratio"] = ratio
    return r


# ---------------------------------------------------------------- lossless crop of the grey area
# The grey area is the encrypted top of the photo. To drop it we don't decode and re-compress:
# the new file simply starts at the first complete row of surviving blocks, so the picture
# data is copied bit for bit (only the first block row's DC codes are re-encoded).
def _bits(B, total_bits, a, b):
    return format((B >> (total_bits - b)) & ((1 << (b - a)) - 1), "0%db" % (b - a)) if b > a else ""


def crop_align(best, buf, start, mcus):
    args, Dm, corr = best.get("_args"), best.get("_Dm"), best.get("_corr")
    if not args or Dm is None or corr is None:
        return None
    D, w_px, h_px, sampling, dqt = args
    mw, lost = Dm.mw, Dm.total - mcus
    g0 = -(-lost // mw) * mw          # first complete block row after the lost area
    skip, rows = g0 - lost, g0 // mw
    if rows <= 0 or skip >= mcus - mw:
        return None
    lay, n = Dm.layout(), len(Dm.scan)
    _, st, bit, (dc0, _) = fj.decode(buf, lay, n, start, stats=True, stop_mcu=skip)
    if st != "stop":
        return None
    pred = [dc0[c][-1] if dc0[c] else 0 for c in range(n)]  # DC predictors entering that block
    Dc = fj.Donor(make_header(D, w_px, h_px - rows * Dm.bh, sampling, dqt))
    B, tb = int.from_bytes(buf, "big"), len(buf) * 8
    head, cur = [], bit
    try:
        spans = fj.dc_spans(buf, lay, bit)
    except (LookupError, IndexError):
        return None
    for b0, b1, ci, diff in spans:
        head.append(_bits(B, tb, cur, b0))
        new = pred[ci] + diff + corr[ci]  # absolute DC incl. colour correction; decoder starts at 0
        cat = abs(new).bit_length()
        table = Dc.dht[(0, Dc.scan[ci][1])]
        if cat not in table:
            return None
        code, ln = table[cat]
        head.append(bits_of(code, ln))
        if cat:
            head.append(format(new if new > 0 else new + (1 << cat) - 1, "0%db" % cat))
        cur = b1
    return assemble(Dc, "".join(head), buf, cur)


def crop_restart(D, dqt, lost, dri, data, pos):
    """Start at the first restart interval that begins a block row; renumber the markers."""
    mw, total = D.mw, D.total
    j = lost
    while j * dri < total and (j * dri) % mw:
        j += 1
    k = j - lost  # interval j's data follows marker pos[k]
    if j * dri >= total - mw or k >= len(pos):
        return None
    kept = RST.sub(lambda m: bytes((0xFF, 0xD0 + ((m.group()[1] - 0xD0 - j) % 8))), data[pos[k] + 2:])
    rows = j * dri // mw
    return make_header(D, D.w, D.h - rows * D.bh, None, dqt, keep_dri=True) + kept + EOI
