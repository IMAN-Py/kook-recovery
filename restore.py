#!/usr/bin/env python3
"""restore.py - use recovered copies you already have, then remove the encrypted files.

Some files fail a recovery run even though an earlier run had already written a good copy of
them into another folder (for example /path/to/recovered-files). This tool goes through the
encrypted files, looks for their recovered copy by name, proves the copy really opens, puts it
next to the encrypted original under its proper name and only then deletes the encrypted file.

Nothing is ever deleted unless the replacement is already written and verified, and no
existing file is overwritten. Run without --apply first: that is a dry run that changes
nothing.
"""
import argparse
import collections
import errno
import hashlib
import os
import shutil
import sys
import tempfile

import recover
from photophoenix import video as _video

PHOTO_SUFFIX = (".jpg", ".jpeg", ".jfif")
VIDEO_SUFFIX = tuple(_video.VIDEO_EXT)


# ---------------------------------------------------------------- looking at files
def sniff(path):
    """-> "photo", "video" or None, read from the file's own first bytes."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return None
    if head[:3] == b"\xff\xd8\xff":
        return "photo"
    if head[4:8] == b"ftyp":
        return "video"
    ext = os.path.splitext(path)[1].lower()
    if ext in PHOTO_SUFFIX:
        return "photo"
    if ext in VIDEO_SUFFIX:
        return "video"
    return None


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def index_folder(folder):
    """name -> [paths] for every file under `folder` (sub-folders included)."""
    index = collections.defaultdict(list)
    for base, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs
                         if d.lower() not in recover.SKIP_DIRS
                         and not os.path.islink(os.path.join(base, d)))
        for name in sorted(names):
            if name.startswith("._") or name == ".DS_Store":
                continue
            index[name].append(os.path.join(base, name))
    return index


def target_name(source, kind):
    """The name the recovered file should carry, with a media extension."""
    name = recover.original_name(source, kind)
    if os.path.splitext(name)[1].lower() not in PHOTO_SUFFIX + VIDEO_SUFFIX:
        name += ".mp4" if kind == "video" else ".jpg"
    return name


def candidate_names(source, kind):
    """Names the recovered copy may have been written under, best first."""
    names = [target_name(source, kind), recover.original_name(source), os.path.basename(source)]
    seen, ordered = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    stem = os.path.splitext(target_name(source, kind))[0]
    for extra in (".jpg", ".jpeg", ".mp4", ".mov", ".m4v", ".3gp", ".3g2"):
        if stem + extra not in seen:
            seen.add(stem + extra)
            ordered.append(stem + extra)
    return ordered


def find_copy(source, kind, index):
    """-> (path, name) of a usable recovered copy of `source`, or (None, None)."""
    for name in candidate_names(source, kind):
        for path in index.get(name, ()):
            if sniff(path) != kind:
                continue
            try:
                recover.validate_result(path, kind)
            except Exception:
                continue
            return path, name
    return None, None


# ---------------------------------------------------------------- writing the result
class TargetTaken(FileExistsError):
    """Something else already uses the name the recovered file needs."""


def free_space(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def publish(recovered_path, target, move=False):
    """Put a recovered file next to the encrypted original; never overwrite anything.

    `move` renames when both files share a disk (instant, needs no space, and the recovered
    folder gives up the copy). Otherwise the file is copied; that needs room for a second copy.
    """
    if move:
        try:
            os.link(recovered_path, target)  # cheapest: same data, two names
            os.unlink(recovered_path)
            return target
        except FileExistsError:
            raise TargetTaken(target) from None
        except OSError:
            pass
        try:
            os.rename(recovered_path, target)
            return target
        except FileExistsError:
            raise TargetTaken(target) from None
        except OSError as exc:
            free = free_space(os.path.dirname(target))
            needed = os.path.getsize(recovered_path)
            if free is not None and free < needed:
                raise OSError(f"not enough free space ({free:,} bytes free, {needed:,} needed)") from exc
            raise
    staged = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".restore-", suffix=".tmp",
                                         dir=os.path.dirname(target), delete=False) as stream:
            staged = stream.name
            with open(recovered_path, "rb") as src:
                shutil.copyfileobj(src, stream, 1 << 20)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, target)
        except FileExistsError:
            raise TargetTaken(target) from None
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
                raise
            with open(target, "xb") as stream, open(staged, "rb") as src:  # FAT/exFAT
                shutil.copyfileobj(src, stream, 1 << 20)
                stream.flush()
                os.fsync(stream.fileno())
        return target
    except BaseException:
        if staged is not None:
            try:
                os.unlink(staged)
            except OSError:
                pass
        raise
    finally:
        if staged is not None and os.path.exists(staged):
            try:
                os.unlink(staged)
            except OSError:
                pass


def restore_one(source, kind, index, keep_encrypted, apply_changes, copy_only=False):
    """-> (status, detail). status: done / already / skipped / failed."""
    target = os.path.join(os.path.dirname(source), target_name(source, kind))
    if target.lower() == source.lower():
        return "skipped", "the attack did not rename this file - nothing to replace"
    copy, copy_name = find_copy(source, kind, index)
    if not os.path.exists(target):
        if copy is None:
            return "skipped", "no recovered copy found"
    else:
        try:
            recover.validate_result(target, kind)  # the file sitting there must really open
        except Exception as exc:
            return "skipped", f"'{os.path.basename(target)}' does not open ({exc}) - left alone"
        if copy is None:
            return "already", "the recovered file is already in place"
        if digest(target) != digest(copy):
            return "skipped", f"'{os.path.basename(target)}' is a different file - left alone"
    detail = f"from {copy_name} ({os.path.getsize(copy):,} bytes)"
    if not apply_changes:
        return "done", detail
    if copy is not None and not os.path.exists(target):
        move = not copy_only and not keep_encrypted
        try:
            publish(copy, target, move=move)
        except TargetTaken:
            return "skipped", f"'{os.path.basename(target)}' appeared meanwhile - left alone"
        try:
            recover.validate_result(target, kind)
        except Exception as exc:
            return "failed", f"the recovered copy did not survive being written ({exc})"
        if not move and digest(target) != digest(copy):
            try:
                os.unlink(target)
            except OSError:
                pass
            return "failed", "the recovered copy did not survive being written"
    if keep_encrypted:
        return "done", detail + " - encrypted file kept"
    try:
        os.unlink(source)
    except OSError as exc:
        return "failed", f"recovered, but the encrypted file could not be removed: {exc}"
    return "done", detail


# ---------------------------------------------------------------- talking to the user
def clean_path(raw):
    path = raw.strip().strip('"').strip("'").strip()
    path = os.path.expanduser(path)
    if len(path) > 1 and path[-1] in "\\/":
        path = path[:-1]
    return path


def ask_folder(prompt, must_have_recovery=False):
    """Ask for one folder until it exists. Returns None if the user gives nothing."""
    print(prompt)
    print("Tip: drag the folder into this window instead of typing it.")
    while True:
        try:
            raw = input("  folder (empty to cancel)> ")
        except EOFError:
            return None
        if not raw.strip():
            return None
        path = clean_path(raw)
        if os.path.isdir(path):
            return path
        print(f"    ! not a folder: {path}")


def ask_folders():
    folders = []
    print("\nFolder(s) holding the encrypted files - one per line, empty line when done.")
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


def ask_yes(question):
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ---------------------------------------------------------------- the work
def restore(folders, from_folder, apply_changes=False, keep_encrypted=False,
            copy_only=False, report_path=None, say=print):
    """-> counts dict. Walks `folders`, matches copies from `from_folder`."""
    say(f"Recovered copies  : {from_folder}")
    say("Reading that folder ...")
    index = index_folder(from_folder)
    say(f"  {sum(len(v) for v in index.values()):,} file(s) there.")
    say("Looking for encrypted files ...")
    # Never touch the folder the recovered copies are read from, nor anything hidden.
    protected = os.path.abspath(from_folder) + os.sep
    encrypted = []
    for folder in folders:
        for base, dirs, names in os.walk(folder):
            dirs[:] = sorted(d for d in dirs
                             if d.lower() not in recover.SKIP_DIRS and not d.startswith(".")
                             and not os.path.islink(os.path.join(base, d))
                             and (os.path.abspath(os.path.join(base, d)) + os.sep).startswith(protected) is False)
            for name in sorted(names):
                path = os.path.join(base, name)
                is_encrypted, kind = recover.inspect(path)
                if is_encrypted:
                    encrypted.append((path, kind or "photo"))
    say(f"  {len(encrypted)} encrypted file(s) found.")
    if not encrypted:
        return {"done": 0, "already": 0, "skipped": 0, "failed": 0}, None

    say("")
    counts = {"done": 0, "already": 0, "skipped": 0, "failed": 0}
    stayed, lines = [], []
    width = len(str(len(encrypted)))
    for number, (path, kind) in enumerate(encrypted, 1):
        try:
            status, detail = restore_one(path, kind, index, keep_encrypted, apply_changes, copy_only)
        except Exception as exc:
            status, detail = "failed", str(exc) or type(exc).__name__
        counts[status] += 1
        lines.append(f"{status}\t{path}\t{detail}")
        if status in ("skipped", "failed"):
            stayed.append((path, detail))
        say(f"[{number:>{width}}/{len(encrypted)}] {status:<7} {path}  ({detail})")

    say("")
    say("-" * 74)
    verb = "put in place" if apply_changes else "can be put in place"
    say(f"Recovered copies {verb} : {counts['done']}")
    say(f"Already in place      : {counts['already']}")
    say(f"Left encrypted        : {counts['skipped'] + counts['failed']}")
    if stayed:
        say("")
        say("Still encrypted (with the reason):")
        for path, detail in stayed:
            say(f"  - {path}: {detail}")

    if report_path:
        try:
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write(f"recovered copies read from: {from_folder}\n")
                fh.write(f"mode: {'apply' if apply_changes else 'dry run'}\n")
                fh.write(f"status\tfile\tdetail\n")
                fh.write("\n".join(lines) + "\n")
            say(f"\nReport written: {report_path}")
        except OSError as exc:
            say(f"  ! could not write the report: {exc}")
    return counts, report_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Put recovered copies in place of encrypted files, then remove the encrypted ones.")
    parser.add_argument("folders", nargs="*", help="folder(s) with the encrypted files")
    parser.add_argument("--from", dest="source_folder",
                        help="folder that already holds the recovered copies (e.g. LEXAR\\OUTPUT)")
    parser.add_argument("--apply", action="store_true", help="really write and delete (default is a dry run)")
    parser.add_argument("--keep-encrypted", action="store_true", help="write the recovered file but keep the .kook")
    parser.add_argument("--copy", dest="copy_only", action="store_true",
                        help="copy the recovered file instead of moving it (keeps it in the old folder)")
    parser.add_argument("--report", help="write a summary of every file to this text file")
    args = parser.parse_args(argv)

    print("=" * 74)
    print(" Recovered files you already have -> put them in place of the encrypted ones")
    print("=" * 74)
    print("For each encrypted file this looks for the copy an earlier run already recovered,")
    print("checks that it opens, writes it next to the encrypted file and deletes the encrypted")
    print("one. Nothing is deleted before the replacement is written and verified.")
    print()

    folders = [clean_path(p) for p in args.folders if os.path.isdir(clean_path(p))]
    if not folders:
        folders = ask_folders()
    if not folders:
        print("\nNo folder with encrypted files given - nothing to do.")
        return 1
    from_folder = clean_path(args.source_folder) if args.source_folder else None
    if not from_folder or not os.path.isdir(from_folder):
        from_folder = ask_folder("\nFolder that already holds the recovered copies (your previous output folder):")
    if not from_folder:
        print("\nNo folder with recovered copies given - nothing to do.")
        return 1

    apply_changes = args.apply
    if not args.apply:
        print("\nThis is a dry run: nothing will be written or deleted.")
        if sys.stdin.isatty() and ask_yes("\nDo it for real now?"):
            apply_changes = True
            print()
    counts, _ = restore(folders, from_folder, apply_changes=apply_changes,
                        keep_encrypted=args.keep_encrypted, copy_only=args.copy_only,
                        report_path=args.report)
    if not apply_changes:
        print("\nDry run only - run again with --apply (or answer yes) to make the changes.")
    return 0 if counts["failed"] == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
