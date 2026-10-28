#!/usr/bin/env python3
"""djvu-recover - rebuild photos and videos locked by STOP/Djvu ransomware.

Run it, type the folder(s) that hold the encrypted files, type an output folder, and it
recovers every photo and video it can without the attacker's key.

The files are not decrypted: STOP/Djvu only overwrites the first ~150 KB of each file, so
the rest of a JPEG or a movie is still intact and can be rebuilt around the damaged part.
"""
import argparse
import collections
import io
import json
import os
import re
import shutil
import struct
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "vendor"))

try:
    import numpy as np
    from PIL import Image, __version__ as pillow_version

    from photophoenix import engine, video, video_index
except ImportError as exc:  # pragma: no cover - friendly message for a missing dependency
    sys.exit(f"Missing dependency: {exc}\nInstall them first:  py -m pip install -r requirements.txt")

TRAILER = 334  # 256-byte wrapped key + 40-char ID + 38-byte filemarker
MARKER_LEN = 38
MARKER_RE = re.compile(rb"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}")
ID_RE = re.compile(rb"[A-Za-z0-9]{40}")

JPEG_EXT = (".jpg", ".jpeg", ".jfif")
VIDEO_EXT = tuple(video.VIDEO_EXT)
MEDIA_EXT = JPEG_EXT + VIDEO_EXT
SKIP_DIRS = {"$recycle.bin", "system volume information", ".git", "node_modules",
            "__pycache__", ".cache", ".venv", "venv", "env"}


def cache_dir():
    """Where the learned camera tables live (next to the tool, or the temp folder if read-only)."""
    paths = [os.environ.get("DJVU_RECOVER_CACHE_DIR"), os.path.join(BASE, ".cache"),
             os.path.join(tempfile.gettempdir(), "djvu-recover")]
    for path in filter(None, paths):
        try:
            os.makedirs(path, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except OSError:
            continue
    return tempfile.mkdtemp(prefix="djvu-recover-")


# ---------------------------------------------------------------- finding the files
def encrypted_payload_size(path):
    """Require an actual Djvu trailer before interpreting damaged bytes."""
    size = os.path.getsize(path)
    if size < TRAILER:
        raise ValueError("No valid STOP/Djvu trailer; file may be empty or truncated. Original preserved.")
    with open(path, "rb") as stream:
        stream.seek(-78, os.SEEK_END)
        tail = stream.read(78)
    if not ID_RE.fullmatch(tail[:40]) or not MARKER_RE.fullmatch(tail[40:]):
        raise ValueError("No valid STOP/Djvu trailer; cannot safely reconstruct this file. Original preserved.")
    return size - TRAILER


def inspect(path):
    if not str(path).lower().endswith(".kook"):
        return False, None
    try:
        encrypted_payload_size(path)
        with open(path, "rb") as stream:
            head = stream.read(5)
    except (OSError, ValueError):
        # Include every .kook in the report, even an empty or damaged file.
        return (True, "salvage") if str(path).lower().endswith(".kook") else (False, None)
    if head[:3] == b"\xff\xd8\xff":
        return True, "photo"
    # Only the first five bytes survived: plausible ftyp length + first letter.
    # Reconstruction and validation must still verify the container.
    if len(head) == 5 and head[4:] == b"f" and 16 <= int.from_bytes(head[:4], "big") <= 4096:
        return True, "video"
    if os.path.splitext(original_name(path))[1].lower() in VIDEO_EXT:
        return True, "video"
    return True, "salvage"


def original_name(path, kind=None):
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    if ext.lower() == ".kook" or (stem and os.path.splitext(stem)[1].lower() in MEDIA_EXT):
        name = stem
    if kind in ("photo", "video"):
        base, extension = os.path.splitext(name)
        allowed = JPEG_EXT if kind == "photo" else VIDEO_EXT
        if extension.lower() not in allowed:
            name = (base if extension else name) + (".jpg" if kind == "photo" else ".mp4")
    return name


def recover_salvage(path, dest):
    """Extract independently decodable images from surviving bytes of ANY format.

    This is fragment salvage, never a complete reconstruction of the enclosing file.
    The in-place worker must preserve the encrypted source for this route.
    """
    import mmap
    size = encrypted_payload_size(path)
    if size <= engine.ENC_END:
        raise ValueError("All file data after the surviving signature is encrypted (first 150 KB). A decryption key or intact backup is required; original preserved.")
    written = []
    with open(path, "rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for magic, end, extension in [(b"\xff\xd8\xff", b"\xff\xd9", ".jpg"),
                                       (b"\x89PNG\r\n\x1a\n", b"IEND\xaeB`\x82", ".png")]:
            pos = engine.ENC_END
            while len(written) < 100:
                start = data.find(magic, pos, size)
                if start < 0:
                    break
                stop = data.find(end, start + len(magic), min(size, start + (64 << 20)))
                pos = start + len(magic)
                if stop < 0:
                    continue
                blob = data[start:stop + len(end)]
                try:
                    with Image.open(io.BytesIO(blob)) as im:
                        im.verify()
                    with Image.open(io.BytesIO(blob)) as im:
                        im.load()
                        if min(im.size) < 16:
                            continue
                except Exception:
                    continue
                target = f"{dest}.salvaged-{len(written)+1}{extension}"
                with open(target, "xb") as output:
                    output.write(blob)
                written.append(target)
                pos = stop + len(end)
    if not written:
        raise ValueError("No intact embedded JPEG/PNG found beyond the encrypted 150 KB. This format cannot be reconstructed by the available methods; original preserved.")
    return "ok", f"Extracted {len(written)} intact embedded image(s); original container NOT recovered", written


def scan(roots):
    """-> (rebuildable [(path, root, kind)], encrypted-but-unsupported [(path, root)])."""
    found, unsupported = [], []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIP_DIRS)
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                is_encrypted, kind = inspect(path)
                if not is_encrypted:
                    continue
                if kind:
                    found.append((path, root, kind))
                else:
                    unsupported.append((path, root))
    found.sort(key=lambda item: (item[2] == "video", item[1].lower(), item[0].lower()))
    return found, unsupported


def original_ext(path):
    """The extension of the encrypted original: 'thesis.docx.kook' -> '.docx'."""
    stem, ext = os.path.splitext(os.path.basename(path))
    inner = os.path.splitext(stem)[1].lower()
    return inner or (ext.lower() or "(no extension)")


def output_path(path, root, out_root, kind):
    rel = os.path.relpath(path, root)
    return os.path.join(out_root, os.path.dirname(rel), original_name(path, kind))


# ---------------------------------------------------------------- camera knowledge
def make_reference_jpegs(folder):
    """Write a few standard-table JPEGs so recovery works with no photos from the camera."""
    sizes = [(5312, 2988), (4608, 3456), (4032, 3024), (3264, 2448), (1920, 1080)]
    rng = np.random.default_rng(1)
    count = 0
    for width, height in sizes:
        yy, xx = np.mgrid[0:height, 0:width]
        base = (128 + 100 * np.sin(xx / (width / 7.0)) * np.cos(yy / (height / 5.0))).astype(np.int16)
        base += rng.integers(-25, 26, (height, width), dtype=np.int16)
        grey = np.clip(base, 0, 255).astype(np.uint8)
        img = Image.merge("RGB", [Image.fromarray(grey),
                                  Image.fromarray(np.roll(grey, 7, 1)),
                                  Image.fromarray(np.roll(grey, 13, 0))])
        for subsampling in (2, 1, 0):  # 4:2:0, 4:2:2 and 4:4:4
            img.save(os.path.join(folder, f"ref_{width}x{height}_{subsampling}.jpg"),
                     quality=90, subsampling=subsampling, optimize=False)
            count += 1
        del yy, xx, base, grey, img
    return count


def build_library(donor_roots, say):
    """Camera/table library: from the user's own photos if any, otherwise built-in tables."""
    if donor_roots:
        say("Reading healthy photos from the donor folder(s) ...")
        entries = engine.scan_library(donor_roots, progress=lambda count: say(f"  Read {count} reference photo headers…"))
        if entries:
            say(f"  {len(entries)} camera/table set(s) learned from your photos.")
            cache = os.path.join(cache_dir(), "library.json")
            try:
                with open(cache, encoding="utf-8") as stream:
                    entries, _ = engine.merge_library(entries, json.load(stream))
            except (OSError, ValueError, TypeError, KeyError):
                pass
            return engine.Library(entries)
        say("  no usable JPEGs there - falling back to built-in tables.")

    cache = os.path.join(cache_dir(), "library.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                entries = json.load(fh)
            if entries:
                return engine.Library(entries)
        except (OSError, ValueError):
            pass

    say("No healthy photos from this camera - using built-in JPEG tables (colours estimated).")
    tmp = tempfile.mkdtemp(prefix="djvu-refs-")
    try:
        say(f"  preparing {make_reference_jpegs(tmp)} reference headers (one time only) ...")
        entries = engine.scan_library([tmp])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
    except OSError:
        pass
    return engine.Library(entries)


def build_video_refs(donor_roots, say):
    path = os.path.join(cache_dir(), "video_refs.json")
    if donor_roots:
        say("Reading healthy videos from the donor folder(s) ...")
        try:
            added, total = video_index.update_ref_library(path, donor_roots)
            say(f"  {added} new reference video setting(s); {total} in total.")
        except Exception as exc:
            say(f"  could not read video references: {exc}")
    try:
        return video_index.load_refs(path)
    except Exception:
        return []


# ---------------------------------------------------------------- checking the result
def validate_photo_file(path):
    """Raise unless this is a JPEG that really opens. Used before an original is removed."""
    from PIL import Image, ImageFile
    old = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with Image.open(path) as image:
            if image.format != "JPEG":
                raise ValueError("The rebuilt file is not a JPEG.")
            image.verify()
        with Image.open(path) as image:
            image.load()
            if min(image.size) < 1:
                raise ValueError("The rebuilt photo has invalid dimensions.")
    except OSError as exc:
        raise ValueError(f"The rebuilt photo does not open: {exc}") from None
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = old


MOVIE_BOXES = (b"ftyp", b"mdat", b"moov")


def validate_video_file(path):
    """Raise unless this is a playable MP4-family movie.

    Checked here instead of with ffprobe, which most Windows machines do not have: the file
    must carry an ftyp header, media data (mdat) and an index (moov) with a video track.
    """
    size = os.path.getsize(path)
    if size < 4096:
        raise ValueError("The rebuilt video is too small to be complete.")
    types, moov = set(), None
    with open(path, "rb") as fh:
        position = 0
        while position + 8 <= size:
            fh.seek(position)
            header = fh.read(8)
            if len(header) < 8:
                break
            length, kind = struct.unpack(">I4s", header)
            if length == 1:
                length = struct.unpack(">Q", fh.read(8))[0]
            elif length == 0:
                length = size - position
            if length < 8 or position + length > size:
                break  # truncated or damaged box: the boxes before it still count
            types.add(kind)
            if kind == b"moov" and moov is None:
                fh.seek(position)
                moov = fh.read(min(length, 64 << 20))
            position += length
    if b"ftyp" not in types:
        raise ValueError("The rebuilt video has no ftyp header.")
    if b"mdat" not in types:
        raise ValueError("The rebuilt video has no media data (mdat).")
    if moov is None:
        raise ValueError("The rebuilt video has no index (moov).")
    if b"vide" not in moov or b"stsd" not in moov:
        raise ValueError("The rebuilt video has no video track.")
    at = moov.find(b"mvhd")
    if at < 4:
        raise ValueError("The rebuilt video has no movie header.")
    version = moov[at + 4]
    timescale_at = at + (24 if version == 1 else 16)
    timescale = struct.unpack(">I", moov[timescale_at:timescale_at + 4])[0]
    if not timescale:
        raise ValueError("The rebuilt video reports no duration.")


def validate_result(path, kind):
    """Pick the right check for this kind of recovered file."""
    if kind == "video":
        validate_video_file(path)
    else:
        validate_photo_file(path)


# ---------------------------------------------------------------- recovering
def rotate_jpeg(data, degrees):
    if not degrees:
        return data
    with Image.open(io.BytesIO(data)) as img:
        rotated = img.rotate(-degrees, expand=True)  # PIL rotates counter-clockwise
        out = io.BytesIO()
        rotated.save(out, "JPEG", quality=95, subsampling=0, optimize=True)
        return out.getvalue()


def recover_photo(path, dest, lib, grey, rotate):
    # Do not abandon less common camera tables because earlier files failed.
    if hasattr(lib, "misses"):
        lib.misses.pop(os.path.dirname(path), None)
    try:
        return _recover_photo(path, dest, lib, grey, rotate)
    except (ValueError, RuntimeError, IndexError) as primary:
        # The intact part may contain an independent preview even when the main
        # image's camera tables are unavailable. Never replace the source for this.
        try:
            _, detail, written = recover_salvage(path, dest)
            return "salvaged", detail, written
        except ValueError:
            raise primary from None


def _recover_photo(path, dest, lib, grey, rotate):
    try:
        res = engine.recover(path, lib, None, crop=grey in ("crop", "both"))
    except ValueError as exc:
        if "no matching camera" in str(exc):
            raise RuntimeError(
                "could not match this camera's JPEG tables - rerun with --donors <folder of "
                "healthy photos from the same phone>") from None
        raise
    full, cropped = res["jpg"], res.get("jpg_cropped")
    if grey == "crop" and cropped:
        pairs = [(dest, cropped)]
    elif grey == "both" and cropped:
        stem, ext = os.path.splitext(dest)
        pairs = [(dest, full), (f"{stem} (cropped){ext}", cropped)]
    else:
        pairs = [(dest, full)]
    written = []
    for target, data in pairs:
        with open(target, "wb") as fh:
            fh.write(rotate_jpeg(data, rotate))
        written.append(target)
    detail = f"{res['method']}, {res.get('dims', '')}, ~{res['frac']:.0%} of the picture"
    if cropped and grey == "crop":
        detail += " (grey strip cropped)"
    elif cropped and grey == "both":
        detail += " (+ cropped copy)"
    return res.get("status", "ok"), detail, written


def recover_movie(path, dest, refs, temp_dir):
    try:
        res = video.recover_video(path, dest)
        return "ok", f"video {res['dims']}, first ~{res['damaged_seconds']:g} s damaged", [dest]
    except ValueError as exc:
        if "whole file is inside the encrypted" in str(exc):
            raise RuntimeError("the whole file was inside the encrypted 150 KB - nothing to rebuild") from None
        if "index was at the start" not in str(exc):
            raise
    try:
        res = video_index.rebuild(path, dest, refs, temp_dir=temp_dir)
    except ValueError as exc:
        text = str(exc)
        if "no reference video" in text:
            raise RuntimeError(
                "this video's index was inside the encrypted part, so it needs a healthy video "
                "from the same phone - rerun with --donors <folder of unencrypted videos>") from None
        if "whole file is inside the encrypted" in text:
            raise RuntimeError("the whole file was inside the encrypted 150 KB - nothing to rebuild") from None
        raise RuntimeError(text) from None
    detail = f"video {res.get('dims', '')}, {res.get('frames', '?')} frames rebuilt"
    if res.get("note"):
        detail += f" ({res['note']})"
    return res.get("status", "ok"), detail, [dest]


# ---------------------------------------------------------------- talking to the user
def clean_path(raw):
    p = raw.strip().strip('"').strip("'").strip()
    p = os.path.expanduser(p)
    if len(p) > 1 and p[-1] in "\\/":
        p = p[:-1]
    return p


def ask_folders(prefilled):
    folders = []
    for p in prefilled:
        p = clean_path(p)
        if os.path.isdir(p):
            folders.append(p)
        else:
            print(f"  ! not a folder, ignoring: {p}")
    if folders:
        return folders
    print("\nFolder(s) holding the encrypted files - one per line, empty line when done.")
    print("Tip: you can drag a folder into this window instead of typing it.\n")
    while True:
        try:
            raw = input("  folder> ")
        except EOFError:
            break
        if not raw.strip():
            break
        path = clean_path(raw)
        if os.path.isdir(path):
            folders.append(path)
            print(f"    + {path}")
        else:
            print(f"    ! not a folder: {path}")
    return folders


def ask_output(default):
    print("\nWhere should the recovered files go?")
    while True:
        try:
            raw = input(f"  output folder [{default}]: ")
        except EOFError:
            raw = ""
        path = clean_path(raw) if raw.strip() else default
        try:
            os.makedirs(path, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
            print("    ! that folder is not writable, try another")
        except OSError as exc:
            print(f"    ! cannot create it: {exc}")


def run_recovery(found, out_root, lib, refs, grey, rotate, overwrite,
                 say=print, progress=None, should_stop=None):
    """Rebuild every file in `found` ([(path, root, kind)]).  Shared by the command line
    and the app window: `say` prints one line per file, `progress(done, total)` is called
    after each, `should_stop()` is polled before the next file.

    -> {counts, failures, total} counts = ok/review/skipped/failed."""
    counts = {"ok": 0, "review": 0, "skipped": 0, "failed": 0}
    failures = []
    total = len(found)
    width = len(str(total)) or 1
    for index, (path, root, kind) in enumerate(found, 1):
        if should_stop and should_stop():
            say("Stopped - the files already recovered are kept.")
            break
        rel = os.path.relpath(path, root)
        dest = output_path(path, root, out_root, kind)
        if os.path.exists(dest):
            counts["skipped"] += 1
            say(f"[{index:>{width}}/{total}] skip  {rel}  (already recovered)")
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            existed = os.path.exists(dest)
            try:
                if kind == "video":
                    status, detail, _ = recover_movie(path, dest, refs,
                                                      os.path.join(cache_dir(), "tmp"))
                    validate_result(dest, kind)
                elif kind == "photo":
                    status, detail, written = recover_photo(path, dest, lib, grey, rotate)
                    if status != "salvaged":
                        for item in written:
                            validate_result(item, kind)
                else:
                    status, detail, _ = recover_salvage(path, dest)
                counts[status if status in counts else "ok"] += 1
                say(f"[{index:>{width}}/{total}] {status:<5} {rel}  ({detail})")
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                counts["failed"] += 1
                reason = str(exc) or exc.__class__.__name__
                failures.append((rel, reason))
                # a half-written file would look like a successful recovery next time: drop it
                if not existed and os.path.exists(dest):
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                say(f"[{index:>{width}}/{total}] FAIL  {rel}  ({reason})")
        if progress:
            progress(index, total)
    return {"counts": counts, "failures": failures, "total": total}


def self_check():
    """Print what the recovery engine sees; used by setup.bat."""
    print(f"  Python    : {sys.version.split()[0]} ({sys.platform})")
    print(f"  numpy     : {np.__version__}")
    print(f"  Pillow    : {pillow_version}")
    print(f"  cache dir : {cache_dir()}")
    print(f"  ffmpeg    : {shutil.which('ffmpeg') or 'not found (optional - video sound only)'}")
    print("  environment OK")
    return 0


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Recover photos and videos overwritten by STOP/Djvu ransomware "
                    "(no key, no ransom). Run without arguments for the guided questions.")
    ap.add_argument("folders", nargs="*", help="folder(s) with the encrypted files (skips the question)")
    ap.add_argument("-o", "--output", help="folder for the recovered files (skips the question)")
    ap.add_argument("--donors", action="append", default=[], metavar="FOLDER",
                    help="folder(s) of healthy photos/videos from the same phone - better colours")
    ap.add_argument("--grey", choices=("keep", "crop", "both"), default="keep",
                    help="the destroyed strip at the top: keep it grey (default), crop it, or write both")
    ap.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0,
                    help="turn photos upright (the original EXIF orientation is lost)")
    ap.add_argument("--overwrite", action="store_true", help="legacy option; existing output files are always preserved")
    ap.add_argument("--check", action="store_true", help="verify the installation and exit")
    args = ap.parse_args(argv)

    if args.check:
        return self_check()

    print("=" * 74)
    print(" STOP/Djvu photo & video recovery")
    print("=" * 74)
    print("These files were encrypted by ransomware. The key belongs to the attackers, so they")
    print("cannot be decrypted - but only the first ~150 KB of each file was overwritten, and")
    print("the rest is still there. This tool rebuilds what survived.")

    folders = ask_folders(args.folders)
    if not folders:
        print("\nNo input folder given - nothing to do.")
        return 1

    default_out = os.path.join(folders[0], "recovered")
    out_root = clean_path(args.output) if args.output else ask_output(default_out)
    os.makedirs(out_root, exist_ok=True)

    say = lambda text="": print(text)
    print()
    donor_roots = [clean_path(d) for d in args.donors] or folders
    lib = build_library(donor_roots, say)
    refs = build_video_refs(donor_roots, say)

    print("\nLooking for encrypted files ...")
    found, unsupported = scan(folders)
    if not found and not unsupported:
        print("No STOP/Djvu-encrypted files found in those folders.")
        return 1
    photos = sum(1 for item in found if item[2] == "photo")
    movies = sum(item[2] == "video" for item in found)
    salvage = sum(item[2] == "salvage" for item in found)
    print(f"Found {len(found)} candidate(s): {photos} photo(s), {movies} video(s), {salvage} other/damaged file(s) for embedded-image salvage.")
    if found:
        by_kind_ext = collections.Counter((item[2], original_ext(item[0])) for item in found)
        listed = ", ".join(f"{ext} x{n}" for (kind, ext), n in by_kind_ext.most_common(8))
        print(f"  formats: {listed}")
    if unsupported:
        by_ext = collections.Counter(original_ext(p) for p, _ in unsupported)
        listed = ", ".join(f"{ext} x{n}" for ext, n in by_ext.most_common(6))
        print(f"Found {len(unsupported)} more encrypted file(s) that cannot be rebuilt: {listed}")
        print("Dedicated reconstruction supports JPEG and MP4-family video; other formats are examined")
        print("for intact embedded images. Recovery is not guaranteed for any format.")
    print()
    if not found:
        print("Nothing rebuildable here - see the note above.")
        return 1

    result = run_recovery(found, out_root, lib, refs, args.grey, args.rotate,
                          args.overwrite, say=print)
    counts, failures = result["counts"], result["failures"]

    print("\n" + "-" * 74)
    print(f"Recovered : {counts['ok'] + counts['review']}"
          + (f"  ({counts['review']} need a look)" if counts["review"] else ""))
    print(f"Skipped   : {counts['skipped']}  (already in the output folder)")
    print(f"Failed    : {counts['failed']}")
    if unsupported:
        print(f"Left alone: {len(unsupported)}  (encrypted formats this tool cannot rebuild)")
    print(f"Output    : {out_root}")
    if failures:
        print("\nCould not recover:")
        for rel, reason in failures:
            print(f"  - {rel}: {reason}")
    if counts["ok"] or counts["review"]:
        print("\nNotes: the first ~150 KB of each file was overwritten, so a thin strip of every")
        print("photo is lost (shown grey, or cropped with --grey crop) and the first moment of a")
        print("video is glitchy. Photos taken with an EXIF rotation flag need --rotate 180.")
        print("Healthy photos/videos from the same phone (--donors FOLDER) give exact colours and")
        print("can rebuild videos whose index was destroyed.")
    return 0 if counts["failed"] == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
