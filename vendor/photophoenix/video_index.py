"""Rebuild the index of videos whose index (moov) was encrypted: picture, and sound when possible.

Phone and app videos that put the index at the START ("fast start") lose it to the encryption,
but the video data after it usually survives. H.264 video in MP4 is stored as length-prefixed
NAL units, so the frames can be found by walking the data; audio chunks in between are skipped.
The codec settings (resolution, profile: the `stsd` box) were in the lost index, so they come from
a healthy reference video made by the same phone/app. Which reference fits is decided by trying
each on a short test clip and letting ffmpeg count decode errors (the right one gives ~0).

Sound: AAC frames sit in the gaps between video frames with no markers or lengths; their sizes
were only in the lost index. A frame's length is found by asking FFmpeg's AAC decoder (via PyAV,
optional): the shortest prefix that decodes cleanly is exactly one frame (even 1 byte short fails).
Measured against intact files: exact for every frame, ~3 ms per frame. Without PyAV: picture only.

The file is read through mmap (the OS page cache), so multi-GB videos don't fill RAM.
"""
import json, mmap, os, re, shutil, struct, subprocess, tempfile

from .engine import ENC_END, TRAILER, MARKER, MARKER_LEN, has_marker
from .video import _boxes, VIDEO_EXT

TEST_SAMPLES = 150          # frames in the test clip used to pick a reference (from the first keyframe)
MAX_ERROR_RATE = 0.02       # decode errors per test frame: at or below = "ok"
REVIEW_ERROR_RATE = 0.10    # up to this, if clearly better than any other reference = "review"
NAL_TYPES = (1, 5, 6, 7, 8, 9)
SOLO_MAX = 1536 << 10      # largest frame accepted without a chain of NALs to confirm it
# Windows: start ffmpeg without a console window (otherwise one flashes up per test decode)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- reference library
def _child(buf, s, e, path):
    for typ, b, c, d in _boxes(buf, s, e):
        if typ == path[0]:
            return (c, d) if len(path) == 1 else _child(buf, c, d, path[1:])
    return None


def ref_from_moov(moov, path=""):
    """H.264 video track settings from a moov box: stsd bytes, NAL length size, timescale, frame delta, size."""
    for t2, b2, c2, d2 in _boxes(moov, 8, len(moov)):
        if t2 != b"trak": continue
        hd = _child(moov, c2, d2, [b"mdia", b"hdlr"])
        if not hd or moov[hd[0] + 8:hd[0] + 12] != b"vide": continue
        mdhd = _child(moov, c2, d2, [b"mdia", b"mdhd"])
        stbl = _child(moov, c2, d2, [b"mdia", b"minf", b"stbl"])
        tkhd = _child(moov, c2, d2, [b"tkhd"])
        if not (mdhd and stbl and tkhd): return None
        v = moov[mdhd[0]]
        ts = struct.unpack(">I", moov[mdhd[0] + (20 if v else 12):mdhd[0] + (24 if v else 16)])[0]
        stsd = next((moov[bb:dd] for tt, bb, cc, dd in _boxes(moov, *stbl) if tt == b"stsd"), None)
        stts = _child(moov, stbl[0], stbl[1], [b"stts"])
        if not stsd or not stts or b"avcC" not in stsd: return None
        i = stsd.find(b"avcC")
        delta = _typical_delta(moov, stts)
        w = struct.unpack(">I", moov[tkhd[1] - 8:tkhd[1] - 4])[0] >> 16
        h = struct.unpack(">I", moov[tkhd[1] - 4:tkhd[1]])[0] >> 16
        return dict(stsd=stsd.hex(), nal_len=(stsd[i + 8] & 3) + 1, ts=ts, delta=delta, w=w, h=h, path=path,
                    audio=audio_from_moov(moov))
    return None


def audio_from_moov(moov):
    """AAC audio track settings: stsd box bytes, AudioSpecificConfig, timescale, frame delta."""
    for t2, b2, c2, d2 in _boxes(moov, 8, len(moov)):
        if t2 != b"trak": continue
        hd = _child(moov, c2, d2, [b"mdia", b"hdlr"])
        if not hd or moov[hd[0] + 8:hd[0] + 12] != b"soun": continue
        mdhd = _child(moov, c2, d2, [b"mdia", b"mdhd"])
        stbl = _child(moov, c2, d2, [b"mdia", b"minf", b"stbl"])
        if not (mdhd and stbl): return None
        stsd = next((moov[bb:dd] for tt, bb, cc, dd in _boxes(moov, *stbl) if tt == b"stsd"), None)
        stts = _child(moov, stbl[0], stbl[1], [b"stts"])
        if not stsd or b"mp4a" not in stsd or not stts: return None
        i = stsd.find(b"esds")
        j = stsd.find(b"\x05", i + 12)                    # DecoderSpecificInfo tag
        if i < 0 or j < 0: return None
        k = j + 1; ln = 0
        while True:                                       # expandable length
            ln = (ln << 7) | (stsd[k] & 0x7F); k += 1
            if not stsd[k - 1] & 0x80: break
        asc = stsd[k:k + ln]
        if len(asc) < 2 or (asc[0] >> 3) != 2: return None  # AAC-LC only
        v = moov[mdhd[0]]
        ts = struct.unpack(">I", moov[mdhd[0] + (20 if v else 12):mdhd[0] + (24 if v else 16)])[0]
        return dict(stsd=stsd.hex(), asc=asc.hex(), ts=ts, delta=aac_frame_delta(asc.hex(), ts))
    return None


AAC_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def aac_frame_delta(asc_hex, ts):
    """Duration of one AAC frame in timescale units: always 1024 samples (960 if the config's
    frameLengthFlag is set). Never taken from the reference's index, whose first entry can be odd
    (e.g. 1105), which made players insert a gap after every frame (choppy sound)."""
    asc = bytes.fromhex(asc_hex)
    sfi = ((asc[0] & 7) << 1) | (asc[1] >> 7)
    rate = AAC_RATES[sfi] if sfi < len(AAC_RATES) else ts
    samples = 960 if (asc[1] >> 2) & 1 else 1024
    return max(1, round(samples * ts / rate))


def _typical_delta(moov, stts):
    """Most common sample duration in an stts box (the first entry can be an odd one)."""
    n = struct.unpack(">I", moov[stts[0] + 4:stts[0] + 8])[0]
    best = (0, 0)
    for k in range(min(n, 4096)):
        cnt, d = struct.unpack(">II", moov[stts[0] + 8 + 8 * k:stts[0] + 16 + 8 * k])
        if cnt > best[0]: best = (cnt, d)
    return best[1] or 1


def _read_moov(path):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(min(size, 4 << 20))
        for typ, b, c, d in _boxes(head, 0, len(head)):
            if typ == b"moov":
                if d <= len(head): return head[b:d]
                fh.seek(b); return fh.read(d - b)
        fh.seek(max(0, size - (16 << 20))); tail = fh.read()
        i = tail.rfind(b"moov")
        if i >= 4:
            n = struct.unpack(">I", tail[i - 4:i])[0]
            if i - 4 + n == len(tail): return tail[i - 4:]
    return None


def scan_refs(roots, skip=(), should_stop=None, skip_names=()):
    """One reference per distinct H.264 configuration among healthy videos under roots."""
    seen, out = set(), []
    for root in roots:
        for dp, dns, fs in os.walk(root):
            if any(s in dp.lower() for s in skip):
                dns[:] = []; continue
            dns[:] = [d for d in dns if d.lower() not in skip_names]
            for f in fs:
                if not f.lower().endswith(VIDEO_EXT): continue
                if should_stop and should_stop(): return out
                p = os.path.join(dp, f)
                try:
                    moov = _read_moov(p)
                    r = ref_from_moov(moov, p) if moov else None
                except Exception:
                    r = None
                if r and r["stsd"] not in seen:
                    seen.add(r["stsd"]); out.append(r)
    return out


def merge_refs(old, new):
    have = {r["stsd"] for r in old}
    added = [r for r in new if r["stsd"] not in have]
    return old + added, len(added)


def update_ref_library(path, roots, skip=(), should_stop=None, skip_names=()):
    """Learn reference videos under roots and add them to the library file. Returns (added, total)."""
    merged, added = merge_refs(load_refs(path), scan_refs(roots, skip, should_stop, skip_names))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh)
    os.replace(tmp, path)
    return added, len(merged)


def load_refs(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


# ---------------------------------------------------------------- finding frames
def find_media_start(mm, olen):
    """Start of video data: after a surviving mdat header, else right after the encrypted part."""
    head = mm[ENC_END:min(olen, ENC_END + (4 << 20))]
    for x in re.finditer(b"mdat", head):
        i = x.start()
        if i < 4: continue
        sz = struct.unpack(">I", head[i - 4:i])[0]
        at = ENC_END + i - 4
        if sz == 1: return at + 16
        if sz and at + sz == olen: return at + 8
    return ENC_END


def walk_frames(mm, start, end, nal_len=4, progress=None, end_ok=None):
    """[(offset, size, keyframe)] of H.264 access units between start and end; audio is skipped.
    end_ok(pos): optional check that real (non-video) data such as an AAC frame starts at pos; used
    to confirm a lone frame start really ends where audio begins."""
    fmt = {4: ">I", 2: ">H", 1: ">B"}[nal_len]

    def nal_at(p):
        if p + nal_len + 2 > end: return None
        n = struct.unpack(fmt, mm[p:p + nal_len])[0]
        if n < 2 or n > (8 << 20) or p + nal_len + n > end: return None
        hb = mm[p + nal_len]
        t = hb & 0x1F
        if hb & 0x80 or t not in NAL_TYPES or (t == 5 and not hb & 0x60): return None
        return n, t, mm[p + nal_len + 1]

    def chain_ok(p, k=3):
        for _ in range(k):
            r = nal_at(p)
            if not r: return False
            p += nal_len + r[0]
        return True

    def ue(bits, i):
        z = 0
        while i + z < len(bits) and bits[i + z] == "0": z += 1
        if z > 7 or i + 2 * z + 1 > len(bits): return None, i
        return (1 << z) - 1 + (int(bits[i + z + 1:i + 2 * z + 1], 2) if z else 0), i + 2 * z + 1

    sizes = []                            # recent confirmed frame sizes (typical size)

    def strong_slice(p):
        """A lone frame start (no chain of NALs to confirm it): first slice of a picture with a valid
        header, a plausible size, and a clean ending (another NAL, audio, or the end). Audio bytes that
        merely look like a frame start fail these: they have random lengths and end mid-data."""
        r = nal_at(p)
        if not r or r[1] not in (1, 5) or not r[2] & 0x80 or not (16 <= r[0] <= SOLO_MAX): return False
        if not header_ok(p, r[1]): return False
        if len(sizes) >= 8:
            typical = sorted(sizes[-64:])[len(sizes[-64:]) // 2]
            if r[0] > typical * (8 if r[1] == 5 else 4): return False
        e = p + nal_len + r[0]
        return e >= end - 16 or nal_at(e) is not None or bool(end_ok and end_ok(e))

    def header_ok(p, t):
        """Slice header of a picture's first slice: valid slice type and PPS id 0 (what cameras use).
        Rejects audio bytes that happen to look like a frame start."""
        bits = "".join(format(b, "08b") for b in mm[p + nal_len + 1:p + nal_len + 5])
        st, i = ue(bits, 1)               # bit 0 is first_mb_in_slice == 0
        if st is None or st > 9: return False
        if t == 5 and st % 5 not in (2, 4): return False      # IDR frames are I (or SI) slices
        pps, _ = ue(bits, i)
        return pps == 0

    def start_ok(p):
        return chain_ok(p) or strong_slice(p)

    frames, cur, pos, steps, span = [], None, start, 0, max(1, end - start)
    while pos < end:
        steps += 1
        if progress and steps % 4096 == 0: progress((pos - start) / span)
        r = nal_at(pos)
        if r is None or (cur is None and not start_ok(pos)):
            if cur: frames.append(cur); sizes.append(cur[1]); cur = None
            q = pos + 1
            while q < end - 16 and not start_ok(q):
                q += 1
                if progress and q % 262144 == 0: progress((q - start) / span)
            pos = q if q < end - 16 else end
            continue
        n, t, b1 = r
        if t in (1, 5) and b1 & 0x80 and not header_ok(pos, t):   # looks like a frame start but isn't
            if cur: frames.append(cur); cur = None
            pos += 1
            continue
        if t in (1, 5) and b1 & 0x80:            # first slice of a new picture (first_mb_in_slice == 0)
            if cur and cur[3]: frames.append(cur); sizes.append(cur[1]); cur = None
            if cur is None: cur = [pos, 0, False, False]
        elif t in (6, 7, 8, 9):                 # SEI / SPS / PPS / AUD begin a new access unit
            if cur and cur[3]: frames.append(cur); cur = None
            if cur is None: cur = [pos, 0, False, False]
        elif cur is None:
            cur = [pos, 0, False, False]
        if t in (1, 5): cur[3] = True; cur[2] = cur[2] or t == 5
        cur[1] = pos + nal_len + n - cur[0]
        pos += nal_len + n
    if cur: frames.append(cur)
    return [(o, s, k) for o, s, k, sl in frames if sl]


# ---------------------------------------------------------------- sound
def _adts_maker(asc):
    obj = asc[0] >> 3; sfi = ((asc[0] & 7) << 1) | (asc[1] >> 7); ch = (asc[1] >> 3) & 15

    def adts(payload):
        n = len(payload) + 7
        return bytes([0xFF, 0xF1, ((obj - 1) << 6) | (sfi << 2) | (ch >> 2), ((ch & 3) << 6) | (n >> 11),
                      (n >> 3) & 0xFF, ((n & 7) << 5) | 0x1F, 0xFC]) + payload
    return adts, max(1, ch)


def split_aac(mm, gaps, asc, progress=None):
    """Exact AAC frames inside the gaps between video frames: [(offset, size)]. Needs PyAV."""
    try:
        import av
    except ImportError:
        return None
    adts, ch = _adts_maker(asc)
    biggest = 768 * ch + 64                       # AAC frames are at most 6144 bits per channel

    def decodes(data):
        ctx = av.CodecContext.create("aac", "r")
        try:
            ctx.decode(av.Packet(adts(data))); return True
        except av.error.FFmpegError:
            return False

    out = []
    for gi, (a, b) in enumerate(gaps):
        if progress and gi % 20 == 0: progress(gi / max(1, len(gaps)))
        pos = a
        while pos < b:
            hi = min(b - pos, biggest)
            if hi < 4 or not decodes(mm[pos:pos + hi]):
                break                             # not (or no longer) AAC: skip the rest of this gap
            lo = 1
            while lo < hi:                        # shortest prefix that decodes = one whole frame
                mid = (lo + hi) // 2
                if decodes(mm[pos:pos + mid]): hi = mid
                else: lo = mid + 1
            out.append((pos, lo)); pos += lo
    return out


def video_deltas(frames, gaps, afr, ref, audio, anchor_every=1.0):
    """Per-frame durations (video timescale) that follow the sound: phones record at a variable
    frame rate, but every AAC frame is exactly `delta` samples, so the audio is a precise clock.
    A run of video frames covers the same time as the audio chunk after it; anchors about every
    second, with frames spread evenly between them, keep lip-sync without jerky motion."""
    if not afr:
        return None
    a_dt = audio["delta"] / audio["ts"]
    gap_idx, counts, j = [], [0] * len(gaps), 0
    for off, _ in afr:                      # audio frames per gap (both lists are in file order)
        while j < len(gaps) and off >= gaps[j][1]: j += 1
        if j < len(gaps) and gaps[j][0] <= off: counts[j] += 1
    ends = {g[0]: i for i, g in enumerate(gaps)}
    anchors, t = [(0, 0.0)], 0.0            # (frame index, time in seconds) at the start of each run
    for i, (o, sz, _) in enumerate(frames):
        g = ends.get(o + sz)
        if g is not None and counts[g]:
            t += counts[g] * a_dt
            if i + 1 < len(frames) and t - anchors[-1][1] >= anchor_every:
                anchors.append((i + 1, t))
    total = len(frames)
    if len(anchors) < 2:
        return None
    vts, out, carry = ref["ts"], [], 0.0
    for (fa, ta), (fb, tb) in zip(anchors, anchors[1:] + [(total, None)]):
        if tb is None:                      # after the last anchor: keep the last rate
            per = (anchors[-1][1] - anchors[-2][1]) / max(1, anchors[-1][0] - anchors[-2][0]) if len(anchors) > 1 else ref["delta"] / vts
        else:
            per = (tb - ta) / max(1, fb - fa)
        for _ in range(fb - fa):
            exact = per * vts + carry
            d = max(1, int(round(exact))); carry = exact - d
            out.append(d)
    return out


def aac_checker(asc):
    """end_ok for walk_frames: does a clean AAC frame start at pos? (needs PyAV)"""
    try:
        import av
    except ImportError:
        return None
    adts, ch = _adts_maker(asc)
    biggest = 768 * ch + 64

    def ok(mm, pos):
        ctx = av.CodecContext.create("aac", "r")
        try:
            ctx.decode(av.Packet(adts(mm[pos:pos + biggest]))); return True
        except av.error.FFmpegError:
            return False
    return ok


_AUDIO_CACHE = {}


def audio_settings(ref):
    a = ref.get("audio")
    if a is None and ref.get("path"):                # library made before sound support: read once
        p = ref["path"]
        if p not in _AUDIO_CACHE:
            moov = _read_moov(p) if os.path.exists(p) else None
            _AUDIO_CACHE[p] = audio_from_moov(moov) if moov else None
        a = _AUDIO_CACHE[p]
    if a:  # libraries saved before this fix stored the reference's first (possibly odd) duration
        a = dict(a, delta=aac_frame_delta(a["asc"], a["ts"]))
    return a


# ---------------------------------------------------------------- writing a file
def _box(t, p): return struct.pack(">I4s", 8 + len(p), t) + p
def _full(t, p, v=0, f=0): return _box(t, struct.pack(">I", (v << 24) | f) + p)


def _audio_trak(audio, afr, base, movie_ms):
    n = len(afr)
    stbl = _box(b"stbl", bytes.fromhex(audio["stsd"])
                + _full(b"stts", struct.pack(">III", 1, n, audio["delta"]))
                + _full(b"stsc", struct.pack(">IIII", 1, 1, 1, 1))
                + _full(b"stsz", struct.pack(">II", 0, n) + struct.pack(">%dI" % n, *[s for _, s in afr]))
                + _full(b"co64", struct.pack(">I", n) + struct.pack(">%dQ" % n, *[o + base for o, _ in afr])))
    dinf = _box(b"dinf", _full(b"dref", struct.pack(">I", 1) + _full(b"url ", b"", 0, 1)))
    minf = _box(b"minf", _full(b"smhd", b"\0" * 4) + dinf + stbl)
    hdlr = _full(b"hdlr", b"\0" * 4 + b"soun" + b"\0" * 12 + b"SoundHandler\0")
    dur = n * audio["delta"]
    mdhd = _full(b"mdhd", struct.pack(">IIII", 0, 0, audio["ts"], dur) + b"\x55\xc4\0\0")
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    tkhd = _full(b"tkhd", struct.pack(">IIIII", 0, 0, 2, 0, dur * 1000 // audio["ts"]) + b"\0" * 8
                 + struct.pack(">HHHH", 0, 1, 0x100, 0) + matrix + b"\0" * 8, 0, 3)
    return _box(b"trak", tkhd + _box(b"mdia", mdhd + hdlr + minf))


def _stts(deltas):
    runs = []
    for d in deltas:
        if runs and runs[-1][1] == d: runs[-1][0] += 1
        else: runs.append([1, d])
    return _full(b"stts", struct.pack(">I", len(runs)) + b"".join(struct.pack(">II", c, d) for c, d in runs))


def build_moov(ref, frames, base, audio=None, afr=None, deltas=None):
    """moov with a video track (and an AAC track if given); offsets shifted by `base`.
    deltas: per-frame durations (default: the reference's constant frame duration)."""
    n = len(frames)
    deltas = deltas or [ref["delta"]] * n
    dur = sum(deltas)
    keys = [i + 1 for i, (_, _, k) in enumerate(frames) if k]
    stbl = _box(b"stbl", bytes.fromhex(ref["stsd"])
                + _stts(deltas)
                + (_full(b"stss", struct.pack(">I", len(keys)) + struct.pack(">%dI" % len(keys), *keys)) if keys else b"")
                + _full(b"stsc", struct.pack(">IIII", 1, 1, 1, 1))
                + _full(b"stsz", struct.pack(">II", 0, n) + struct.pack(">%dI" % n, *[s for _, s, _ in frames]))
                + _full(b"co64", struct.pack(">I", n) + struct.pack(">%dQ" % n, *[o + base for o, _, _ in frames])))
    dinf = _box(b"dinf", _full(b"dref", struct.pack(">I", 1) + _full(b"url ", b"", 0, 1)))
    minf = _box(b"minf", _full(b"vmhd", b"\0" * 8, 0, 1) + dinf + stbl)
    hdlr = _full(b"hdlr", b"\0" * 4 + b"vide" + b"\0" * 12 + b"VideoHandler\0")
    mdhd = _full(b"mdhd", struct.pack(">IIII", 0, 0, ref["ts"], dur) + b"\x55\xc4\0\0")
    ms = dur * 1000 // ref["ts"]
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    tkhd = _full(b"tkhd", struct.pack(">IIIII", 0, 0, 1, 0, ms) + b"\0" * 8 + b"\0" * 8 + matrix
                 + struct.pack(">II", ref["w"] << 16, ref["h"] << 16), 0, 3)
    mvhd = _full(b"mvhd", struct.pack(">IIII", 0, 0, 1000, ms) + struct.pack(">IH", 0x10000, 0x100)
                 + b"\0" * 10 + matrix + b"\0" * 24 + struct.pack(">I", 3))
    tracks = _box(b"trak", tkhd + _box(b"mdia", mdhd + hdlr + minf))
    if audio and afr:
        tracks += _audio_trak(audio, afr, base, ms)
    return _box(b"moov", mvhd + tracks)


FTYP = struct.pack(">I4s4sI", 24, b"ftyp", b"isom", 512) + b"isomavc1"


def write_video(dest, mm, frames, ref, media_from, media_to, audio=None, afr=None, deltas=None, progress=None):
    """ftyp + 64-bit mdat holding mm[media_from:media_to] + moov. Streams; returns nothing."""
    base = len(FTYP) + 16 - media_from
    with open(dest, "wb") as w:
        w.write(FTYP)
        w.write(struct.pack(">I4sQ", 1, b"mdat", 16 + media_to - media_from))
        p = media_from
        while p < media_to:
            q = min(media_to, p + (8 << 20)); w.write(mm[p:q]); p = q
            if progress: progress((p - media_from) / max(1, media_to - media_from))
        w.write(build_moov(ref, frames, base, audio, afr, deltas))


def decode_errors(path, ffmpeg):
    r = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", path, "-f", "null", "-"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return len([l for l in r.stderr.splitlines() if l.strip()])


def likely_order(refs, path):
    """References from the same folder first, then ones with the same file-name style."""
    folder = os.path.dirname(os.path.abspath(path)).lower()
    stem = re.sub(r"\d", "#", os.path.basename(path).split(".")[0])
    def score(r):
        rp = r.get("path", "")
        return (os.path.dirname(os.path.abspath(rp)).lower() != folder,
                re.sub(r"\d", "#", os.path.basename(rp).split(".")[0]) != stem)
    return sorted(refs, key=score)


def pick_reference(mm, frames, refs, ffmpeg, temp_dir, progress=None):
    """Try each reference on a short clip; return (ref, errors_per_frame) of the best, or (None, None).
    `frames` must start at a keyframe (otherwise every reference shows errors)."""
    clip = frames[:TEST_SAMPLES]
    lo, hi = clip[0][0], clip[-1][0] + clip[-1][1]
    scored = []
    for i, ref in enumerate(refs):
        if progress: progress(i, len(refs))
        fd, tmp = tempfile.mkstemp(suffix=".mp4", dir=temp_dir); os.close(fd)
        try:
            write_video(tmp, mm, clip, ref, lo, hi)
            rate = decode_errors(tmp, ffmpeg) / len(clip)
        finally:
            os.remove(tmp)
        scored.append((rate, ref))
        if rate == 0:
            break
    scored.sort(key=lambda t: t[0])
    if not scored:
        return None, None, None
    return scored[0][1], scored[0][0], (scored[1][0] if len(scored) > 1 else None)


def rebuild(path, dest, refs, temp_dir=None, ffmpeg=None, prefer=None, max_tries=None, progress=None):
    """Rebuild a video whose index was encrypted. Returns an info dict; raises ValueError if it can't.
    prefer: a reference to try first (e.g. the one that worked for the folder); max_tries: limit the
    references tried (used after earlier videos in the same folder found no match).
    progress(step, fraction): optional callback for the app's per-file progress."""
    say = progress or (lambda step, frac=None: None)
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    size = os.path.getsize(path); olen = size - TRAILER
    refs = [r for r in refs if r]
    if not refs:
        raise ValueError("no reference video from this phone yet: add an original video from the same phone "
                         "model via 'Learn cameras from…'")
    with open(path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            if not has_marker(mm[size - MARKER_LEN:]):
                raise ValueError("not a STOP/Djvu file")
            start = find_media_start(mm, olen)
            by_len = {}
            for r in refs: by_len.setdefault(r["nal_len"], []).append(r)
            # a lone frame start must end where audio begins: check against each audio format the
            # candidate references use (phones use only a few)
            ascs = {a["asc"] for a in (audio_settings(r) for r in refs) if a}
            checkers = [c for c in (aac_checker(bytes.fromhex(x)) for x in ascs) if c]
            end_ok = (lambda pos: any(c(mm, pos) for c in checkers)) if checkers else None
            frames, cands = [], []
            for nal_len, rs in by_len.items():
                fr = walk_frames(mm, start, olen, nal_len, lambda f: say("finding video frames", f), end_ok)
                if len(fr) > len(frames): frames, cands = fr, rs
            # frames before the first surviving keyframe depend on an encrypted one: drop them
            k0 = next((i for i, f in enumerate(frames) if f[2]), None)
            if k0 is not None: frames = frames[k0:]
            if len(frames) < 10 or k0 is None:
                raise ValueError("no H.264 video frames found after the encrypted part")
            cands = likely_order(cands, path)
            if prefer is not None and prefer in cands:
                cands = [prefer] + [c for c in cands if c is not prefer]
            if max_tries:
                cands = cands[:max_tries]
            if ffmpeg:
                ref, rate, runner_up = pick_reference(
                    mm, frames, cands, ffmpeg, temp_dir or tempfile.gettempdir(),
                    lambda i, n: say(f"choosing reference video ({i + 1}/{n})", i / max(1, n)))
                if ref is not None and rate <= MAX_ERROR_RATE:
                    status = "ok"
                elif ref is not None and rate <= REVIEW_ERROR_RATE and (runner_up is None or runner_up >= 5 * rate):
                    status = "review"   # clearly the right camera, but a few frames may glitch
                else:
                    raise ValueError("no reference video matches this phone: add an original video from the same "
                                     "phone model via 'Learn cameras from…'")
            elif len(cands) == 1:
                ref, rate, status = cands[0], None, "review"  # can't verify without ffmpeg
            else:
                raise ValueError("index was encrypted; choosing a reference video needs ffmpeg installed")
            # sound: split the gaps between video frames into AAC frames
            audio, afr, deltas = audio_settings(ref), None, None
            if audio:
                gaps = [(frames[i][0] + frames[i][1], frames[i + 1][0]) for i in range(len(frames) - 1)
                        if frames[i + 1][0] > frames[i][0] + frames[i][1]]
                last_end = frames[-1][0] + frames[-1][1]
                if olen > last_end: gaps.append((last_end, olen))
                afr = split_aac(mm, gaps, bytes.fromhex(audio["asc"]), lambda f: say("measuring sound", f))
                deltas = video_deltas(frames, gaps, afr, ref, audio)
            media_to = max(frames[-1][0] + frames[-1][1], (afr[-1][0] + afr[-1][1]) if afr else 0)
            write_video(dest, mm, frames, ref, frames[0][0], media_to, audio, afr, deltas,
                        lambda f: say("writing video", f))
        finally:
            mm.close()
    seconds = (sum(deltas) if deltas else len(frames) * ref["delta"]) / ref["ts"]
    keys = sum(1 for f in frames if f[2])
    return dict(method="video-rebuilt", status=status, frac=1.0, q=1.0, dims=f"{seconds:.0f} s", ref=ref,
                frames=len(frames), keyframes=keys,
                note=("picture and sound; index rebuilt" if afr else "picture only (no sound); index rebuilt")
                     + ("; a few frames may glitch" if status == "review" else ""), audio_frames=len(afr or []),
                reference=os.path.basename(ref.get("path", "")), resolution=f"{ref['w']}x{ref['h']}")
