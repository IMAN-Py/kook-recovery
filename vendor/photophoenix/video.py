"""Video recovery (MP4 / 3GP / MOV) for STOP/Djvu-encrypted files.

A phone video is an MP4-family file: a small `ftyp` box, the `mdat` box holding all audio and
video data, and the `moov` box: the index saying where every frame is. STOP/Djvu encrypts the
first 150 KB, which destroys `ftyp`, the `mdat` header and the first ~second of media.

* Index at the END (most Android camera apps): `moov` survived. We rebuild `ftyp` and the
  `mdat` header in front of the untouched media; the video plays at full length and only the
  first moments (whose data was encrypted) are glitchy. Method "video".
* Index at the START (iPhones, chat apps, editors): `moov` was destroyed. Not supported yet.

Videos can be gigabytes, so the output is streamed to disk; nothing big is held in memory.
"""
import os, struct

from .engine import ENC_END, TRAILER, MARKER, MARKER_LEN, has_marker

VIDEO_EXT = (".mp4", ".m4v", ".3gp", ".3g2", ".mov")
BRANDS = {".3gp": (b"3gp4", [b"3gp4", b"isom"]), ".3g2": (b"3g2a", [b"3g2a", b"isom"]),
          ".mov": (b"qt  ", [b"qt  "]), ".mp4": (b"isom", [b"isom", b"iso2", b"mp41"]),
          ".m4v": (b"M4V ", [b"M4V ", b"isom", b"mp41"])}
CONTAINERS = (b"trak", b"mdia", b"minf", b"stbl", b"edts")


def is_video(name):
    n = name.lower()
    if n.endswith(".coot"): n = n[:-5]
    return n.endswith(VIDEO_EXT)


def _boxes(buf, start, end):
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[i:i + 8]); hdr = 8
        if size == 1:
            size = struct.unpack(">Q", buf[i + 8:i + 16])[0]; hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            return
        yield typ, i, i + hdr, i + size
        i += size


def find_index(fh, olen, search=16 << 20):
    """(offset, size) of a top-level 'moov' box ending exactly at the end of the original file,
    or None if the index was at the start (encrypted)."""
    tail_from = max(ENC_END, olen - search)
    fh.seek(tail_from); tail = fh.read(olen - tail_from)
    i = len(tail)
    while True:
        i = tail.rfind(b"moov", 0, i)
        if i < 4:
            return None
        size = struct.unpack(">I", tail[i - 4:i])[0]
        if size >= 8 and tail_from + i - 4 + size == olen:
            return tail_from + i - 4, size


def index_info(moov):
    """Chunk offsets of every track, and duration in seconds (from mvhd)."""
    offs, duration = [], 0.0

    def walk(s, e):
        nonlocal duration
        for typ, b, c, d in _boxes(moov, s, e):
            if typ in CONTAINERS:
                walk(c, d)
            elif typ == b"mvhd":
                v = moov[c]
                if v == 1:
                    ts, du = struct.unpack(">IQ", moov[c + 20:c + 32])
                else:
                    ts, du = struct.unpack(">II", moov[c + 12:c + 20])
                duration = du / ts if ts else 0.0
            elif typ in (b"stco", b"co64"):
                n = struct.unpack(">I", moov[c + 4:c + 8])[0]
                w = 4 if typ == b"stco" else 8
                fmt = ">%d%s" % (n, "I" if w == 4 else "Q")
                offs.extend(struct.unpack(fmt, moov[c + 8:c + 8 + w * n]))
    walk(8, len(moov))
    return offs, duration


def _file_header(room, major, compat):
    """An ftyp box (plus a 'free' box if needed) filling exactly `room` bytes before mdat."""
    for k in range(len(compat), 0, -1):
        ftyp = b"ftyp" + major + b"\0\0\0\0" + b"".join(compat[:k])
        ftyp = struct.pack(">I", 4 + len(ftyp)) + ftyp
        gap = room - len(ftyp)
        if gap == 0:
            return ftyp
        if gap >= 8:
            return ftyp + struct.pack(">I4s", gap, b"free")  # its payload is whatever lies before mdat
    return None


def recover_video(path, dest, progress=None):
    """Rebuild a video whose index survived. Writes dest; returns an info dict.
    Raises ValueError with a plain-language reason when it can't."""
    size = os.path.getsize(path)
    olen = size - TRAILER
    with open(path, "rb") as fh:
        fh.seek(size - MARKER_LEN)
        if not has_marker(fh.read(MARKER_LEN)):
            raise ValueError("not a STOP/Djvu file")
        if olen <= ENC_END + 4096:
            raise ValueError("whole file is inside the encrypted 150 KB")
        idx = find_index(fh, olen)
        if not idx:
            raise ValueError("video index was at the start and got encrypted (not supported yet)")
        moov_at, moov_size = idx
        fh.seek(moov_at); moov = fh.read(moov_size)
        offs, duration = index_info(moov)
        if not offs:
            raise ValueError("video index has no media offsets")
        media_start = min(offs)
        mdat_size = moov_at - (media_start - 8)
        big = mdat_size > 0xFFFFFFFF
        mdat_at = media_start - (16 if big else 8)
        ext = os.path.splitext(path[:-5] if path.lower().endswith(".coot") else path)[1].lower()
        head = _file_header(mdat_at, *BRANDS.get(ext, BRANDS[".mp4"]))
        if head is None:
            raise ValueError("unexpected video layout")
        mdat_hdr = struct.pack(">I4sQ", 1, b"mdat", moov_at - mdat_at) if big else struct.pack(">I4s", moov_at - mdat_at, b"mdat")
        with open(dest, "wb") as w:
            w.write(head)
            w.write(b"\0" * (ENC_END - len(head)))   # the encrypted bytes are unrecoverable
            fh.seek(ENC_END)
            pos = ENC_END
            while pos < olen:
                chunk = fh.read(min(8 << 20, olen - pos))
                if not chunk: break
                w.write(chunk); pos += len(chunk)
                if progress: progress("writing video", (pos - ENC_END) / max(1, olen - ENC_END))
            w.seek(mdat_at); w.write(mdat_hdr)          # mdat header sits right before the media
    damaged = sum(1 for o in offs if o < ENC_END)
    lost_s = duration * damaged / len(offs) if offs else 0.0
    return dict(method="video", frac=(olen - ENC_END) / olen, q=1.0, dims=f"{duration:.0f} s",
                damaged_seconds=round(lost_s, 1), status="ok")
