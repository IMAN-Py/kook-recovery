"""JSON-lines worker for the macOS app. All outputs are partial reconstructions."""
import contextlib
import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import uuid

import recover


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def collect(inputs):
    found, seen = [], set()
    # Prefer the broadest selected folder when selections overlap, independently
    # of selection order. A file selected by itself has no enclosing source root.
    roots = {Path(value).expanduser().resolve(strict=True) for value in inputs}
    for path in sorted(roots, key=lambda p: (not p.is_dir(), len(p.parts), str(p))):
        candidates = [(path, None)] if path.is_file() else (
            (Path(base) / name, path)
            for base, dirs, names in os.walk(path)
            if not _prune(base, dirs)
            for name in sorted(names)
        )
        for item, root in candidates:
            real = item.resolve()
            if real in seen or item.is_symlink():
                continue
            seen.add(real)
            encrypted, kind = recover.inspect(str(item))
            if encrypted:
                found.append((item, root, kind))
    return found


def _prune(base, dirs):
    dirs[:] = sorted(d for d in dirs if d.lower() not in recover.SKIP_DIRS
                     and not (Path(base) / d).is_symlink())
    return False


class OutputExists(FileExistsError):
    """The exact output name is occupied; never rename or overwrite it."""


def publish_file(source, output):
    """Publish under the original name, without replacing or renaming existing files."""
    source = Path(source)
    target = output / source.name
    staged = None
    try:
        # Stage on the destination filesystem so the final hard link is atomic.
        with tempfile.NamedTemporaryFile(prefix=".djvu-", suffix=".tmp", dir=output, delete=False) as stream:
            staged = Path(stream.name)
            with source.open("rb") as src:
                shutil.copyfileobj(src, stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, target)
            return target
        except FileExistsError:
            raise OutputExists(str(target)) from None
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
                raise
            # FAT/exFAT destinations do not support hard links. Exclusive creation
            # still protects the exact existing name, including symlinks.
            try:
                stream = target.open("xb")
            except FileExistsError:
                raise OutputExists(str(target)) from None
            try:
                with stream, staged.open("rb") as src:
                    shutil.copyfileobj(src, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                return target
            except BaseException:
                target.unlink(missing_ok=True)
                raise
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def validate_result(path, kind):
    """Require a readable output before allowing removal of an encrypted source.

    Photo and video checks live in recover.py so the same rules apply on every platform
    (ffprobe is not needed, and most Windows machines do not have it).
    """
    recover.validate_result(path, kind)


@contextlib.contextmanager
def commit_guard():
    # Defer cancellation over the short commit/rollback boundary. A pending signal
    # is delivered after the transaction, so it cannot remove both files.
    if hasattr(signal, "pthread_sigmask"):
        old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        try:
            yield
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
    else:
        yield


def replace_source(source, reconstructed, before, kind):
    if source.suffix.lower() != ".kook":
        raise ValueError("Only .kook source files may be replaced.")
    target = source.with_name(recover.original_name(str(source), kind))
    if target == source or Path(reconstructed).name != target.name:
        raise ValueError("Cannot determine the original filename; encrypted file left unchanged.")
    if os.path.lexists(target):
        raise OutputExists(str(target))
    validate_result(reconstructed, kind)
    expected = digest(reconstructed)
    if source.is_symlink() or digest(source) != before:
        raise ValueError("Source changed during recovery; left unchanged.")
    with commit_guard():
        published = None
        identity = None
        try:
            published = publish_file(reconstructed, source.parent)
            info = published.lstat()
            identity = (info.st_dev, info.st_ino)
            if digest(published) != expected:
                raise ValueError("Saved file failed verification; original left unchanged.")
            if source.is_symlink() or digest(source) != before:
                raise ValueError("Source changed during recovery; left unchanged.")
            source.unlink()
        except BaseException:
            if published is not None and os.path.lexists(source):
                try:
                    info = published.lstat()
                    if identity == (info.st_dev, info.st_ino):
                        published.unlink()
                except OSError:
                    pass
            raise
    return target


def run(config):
    inputs = config.get("inputs", [])
    if not inputs:
        raise ValueError("Select at least one encrypted file or folder.")
    grey = "crop"  # Always publish one cropped result, including for legacy job settings.
    rotate = int(config.get("rotate", 0))
    if rotate not in (0, 90, 180, 270):
        raise ValueError("Invalid rotation setting.")
    donors = [str(Path(p).expanduser().resolve(strict=True)) for p in config.get("donors", [])]
    if any(not Path(p).is_dir() for p in donors):
        raise ValueError("The reference location must be a folder.")
    emit("message", text="Inspecting selected files…")
    found = collect(inputs)
    if not found:
        raise ValueError("No STOP/Djvu-encrypted files found in this selection.")
    if not donors:
        donors = list(dict.fromkeys(str(root or path.parent) for path, root, _ in found))
        emit("message", text="Learning camera references from nearby photos and videos (read-only)…")
    targets = [path.with_name(recover.original_name(str(path), kind)) for path, _, kind in found]
    output = found[0][1] or found[0][0].parent
    reports = Path(recover.cache_dir()) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report_path = reports / (datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + uuid.uuid4().hex + ".json")
    emit("started", total=len(found), output=str(output))
    lib, refs = None, None
    say = lambda text: emit("message", text=text)
    results = []
    for index, ((path, root, kind), target) in enumerate(zip(found, targets), 1):
        emit("progress", index=index, total=len(found), name=path.name)
        result = dict(source=str(path), destination=str(target), status="failed", files=[])
        try:
            if kind is None:
                raise ValueError("This file format is not supported for reconstruction; original left unchanged.")
            if target == path:
                raise ValueError("Cannot determine the original filename; encrypted file left unchanged.")
            if os.path.lexists(target):
                raise OutputExists(str(target))
            before = digest(path)
            result["source_sha256"] = before
            if kind == "photo" and lib is None:
                lib = recover.build_library(donors, say)
            if kind == "video" and refs is None:
                refs = recover.build_video_refs(donors, say)
            # Temporary work and reports stay outside the user's output folder.
            with tempfile.TemporaryDirectory(prefix="djvu-working-") as work:
                dest = str(Path(work) / recover.original_name(str(path), kind))
                with contextlib.redirect_stdout(sys.stderr):
                    if kind == "photo":
                        status, detail, written = recover.recover_photo(str(path), dest, lib, grey, rotate)
                    elif kind == "video":
                        status, detail, written = recover.recover_movie(str(path), dest, refs, work)
                    else:
                        status, detail, written = recover.recover_salvage(str(path), dest)
                if digest(path) != before:
                    raise RuntimeError("The source changed during recovery; output was not published.")
                fragments = kind == "salvage" or status == "salvaged"
                if not fragments and len(written) != 1:
                    raise RuntimeError("Expected exactly one recovered file for this source.")
                if status not in ("ok", "salvaged"):
                    raise ValueError("Reconstruction needs review; encrypted file left unchanged.")
                if fragments:
                    saved = [str(publish_file(item, path.parent)) for item in written]
                else:
                    saved = [str(replace_source(path, written[0], before, kind))]
                result.update(status="partial", engine_status=status, detail=detail,
                              files=saved, original_removed=not fragments)
        except OutputExists:
            result.update(status="skipped", detail=f"Target already exists; encrypted original left unchanged: {target}")
        except Exception as exc:
            result["error"] = str(exc) or type(exc).__name__
        results.append(result)
        emit("result", **result)
        report = dict(output=str(output), results=results, complete=index == len(found),
                      note="Partial reconstruction, not decryption. Missing pixels and original colours may not be recoverable.")
        temp = report_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temp.replace(report_path)
    recovered = sum(r["status"] == "partial" for r in results)
    skipped = sum(r["status"] == "skipped" for r in results)
    failed = len(results) - recovered - skipped
    emit("finished", recovered=recovered, skipped=skipped, failed=failed,
         output=str(output), report=str(report_path))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    def stop(signum, frame):
        raise SystemExit(130)
    signal.signal(signal.SIGTERM, stop)
    try:
        with open(sys.argv[1], encoding="utf-8") as stream:
            config = json.load(stream)
        code = run(config)
    except Exception as exc:
        emit("error", text=str(exc) or type(exc).__name__)
        code = 1
    sys.exit(code)
