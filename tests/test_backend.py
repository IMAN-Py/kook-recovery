import contextlib
import errno
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_backend as backend
from PIL import Image

MARKER = b"{36A698B9-D67C-4E07-BE82-0EC5B14B4DF5}"


def encrypted(path, head=b"\xff\xd8\xff\xe1\x00"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + bytes(5000) + bytes(256) + b"A" * 40 + MARKER)


def jpeg(path):
    Image.new('RGB', (64, 32), (40, 100, 200)).save(path, 'JPEG')


def success(path, dest, lib, grey, rotate):
    if grey != 'crop':
        raise AssertionError('Only cropped output is allowed')
    jpeg(dest)
    return 'ok', 'cropped test result', [dest]


class BackendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        patcher = patch.object(backend.recover, 'cache_dir', return_value=str(self.cache))
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, inputs, worker=success):
        output = io.StringIO()
        with patch.object(backend.recover, 'build_library', return_value=object()), patch.object(backend.recover, 'recover_photo', side_effect=worker), contextlib.redirect_stdout(output):
            code = backend.run({'inputs': list(map(str, inputs)), 'delete_original': True})
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        return code, events

    def safe_job(self, config, worker=success):
        output = io.StringIO()
        with patch.object(backend.recover, 'build_library', return_value=object()), patch.object(backend.recover, 'recover_photo', side_effect=worker), contextlib.redirect_stdout(output):
            code = backend.run(config)
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_default_preserves_original_and_journals_output(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        before = source.read_bytes()
        code, events = self.safe_job({'inputs': [str(source)]})
        self.assertEqual(code, 0)
        self.assertEqual(source.read_bytes(), before)
        row = next(e for e in events if e['event'] == 'result')
        self.assertFalse(row['original_removed'])
        self.assertEqual(row['quality'], 'partial_photo')
        self.assertEqual(row['output_sha256'][str(source.with_suffix(''))], backend.digest(source.with_suffix('')))

    def test_resume_after_interruption_and_retry_failed(self):
        sources = [self.root / (n + '.jpg.kook') for n in ['a', 'b', 'c']]
        for p in sources: encrypted(p)
        output = io.StringIO()
        calls = []
        def stop_on_second(*args):
            calls.append(args[0])
            if len(calls) == 2: raise KeyboardInterrupt()
            return success(*args)
        with patch.object(backend.recover, 'build_library', return_value=object()), patch.object(backend.recover, 'recover_photo', side_effect=stop_on_second), contextlib.redirect_stdout(output), self.assertRaises(KeyboardInterrupt):
            backend.run({'inputs': [str(self.root)]})
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        report = next(e['report'] for e in events if e['event'] == 'started')
        def fail_b(*args):
            if Path(args[0]).name.startswith('b'): raise ValueError('missing reference')
            return success(*args)
        code, events = self.safe_job({'resume_report': report}, fail_b)
        self.assertEqual(code, 2)
        self.assertEqual([e['source'] for e in events if e['event'] == 'result'], list(map(str, sources[1:])))
        report = events[-1]['report']
        code, events = self.safe_job({'resume_report': report, 'mode': 'retry_failed'})
        self.assertEqual(code, 0)
        self.assertEqual([e['source'] for e in events if e['event'] == 'result'], [str(sources[1])])
        self.assertTrue(all(p.exists() for p in sources))

    def test_preflight_reports_missing_optional_tool_once(self):
        messages = []
        with patch.object(backend.recover.shutil, 'which', return_value=None):
            backend.recover.preflight([(None, None, 'video')] * 3, str(self.root), messages.append)
        self.assertEqual(sum('ffmpeg missing' in text for text in messages), 1)

    def test_all_enabled_suffixes_replace_only_valid_sources(self):
        for suffix in backend.recover.RANSOMWARE_EXTENSIONS:
            with self.subTest(suffix=suffix):
                folder = self.root / suffix[1:]
                source = folder / ('photo.jpg' + suffix.upper())
                encrypted(source)
                code, _ = self.job([source])
                self.assertEqual(code, 0)
                self.assertFalse(source.exists())
                self.assertTrue((folder / 'photo.jpg').exists())
                bad = folder / ('bad.jpg' + suffix)
                bad.write_bytes(b'not encrypted')
                result = self.root / 'bad.jpg'
                jpeg(result)
                with self.assertRaises(ValueError):
                    backend.replace_source(bad, result, backend.digest(bad), 'photo')
                self.assertEqual(bad.read_bytes(), b'not encrypted')
                self.assertFalse((folder / 'bad.jpg').exists())

    def test_replaces_success_in_same_folder_and_keeps_only_one_file(self):
        source = self.root / 'Photos/photo.jpg.kook'
        encrypted(source)
        code, events = self.job([source])
        self.assertEqual(code, 0)
        self.assertFalse(source.exists())
        target = source.with_suffix('')
        with Image.open(target) as image:
            image.load()
            self.assertEqual(image.size, (64, 32))
        self.assertEqual(list(source.parent.iterdir()), [target])
        result = next(e for e in events if e['event'] == 'result')
        self.assertTrue(result['original_removed'])
        self.assertEqual(result['files'], [str(target)])
        self.assertFalse(list(self.root.rglob('*.kook')))

    def test_nested_folders_and_healthy_files_are_preserved(self):
        photos = self.root / 'Photos'
        a, b = photos / 'photo.jpg.kook', photos / 'Nested/photo.jpg.kook'
        encrypted(a)
        encrypted(b)
        healthy = photos / 'notes.txt'
        healthy.write_text('keep me')
        self.assertEqual(self.job([photos])[0], 0)
        files = {p.relative_to(photos).as_posix() for p in photos.rglob('*') if p.is_file()}
        self.assertEqual(files, {'photo.jpg', 'Nested/photo.jpg', 'notes.txt'})
        self.assertEqual(healthy.read_text(), 'keep me')

    def test_failed_and_unsupported_files_are_left_byte_for_byte(self):
        photos = self.root / 'Photos'
        failed = photos / 'bad.jpg.kook'
        unsupported = photos / 'document.pdf.kook'
        encrypted(failed)
        encrypted(unsupported, head=b'%PDF-')
        before = {p: p.read_bytes() for p in (failed, unsupported)}
        def fail(path, dest, *args):
            Path(dest).write_bytes(b'incomplete')
            raise ValueError('No matching camera')
        code, events = self.job([photos], worker=fail)
        self.assertEqual(code, 2)
        self.assertEqual(events[-1]['failed'], 2)
        self.assertEqual(set(photos.iterdir()), set(before))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_invalid_generated_jpeg_never_replaces_source(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        before = source.read_bytes()
        def broken(path, dest, *args):
            Path(dest).write_bytes(b'not a jpeg')
            return 'ok', 'test', [dest]
        self.assertEqual(self.job([source], broken)[0], 2)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(source.with_suffix('').exists())

    def test_review_result_keeps_original(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        before = source.read_bytes()
        def review(*args):
            _, detail, files = success(*args)
            return 'review', detail, files
        self.assertEqual(self.job([source], review)[0], 2)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(source.with_suffix('').exists())

    def test_existing_target_keeps_both_existing_files_unchanged(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        target = source.with_suffix('')
        target.write_bytes(b'existing')
        before = source.read_bytes()
        code, events = self.job([source], worker=lambda *a: self.fail('Should not run recovery'))
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]['skipped'], 1)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(target.read_bytes(), b'existing')

    def test_source_changed_during_recovery_is_not_deleted(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        def changed(path, *args):
            result = success(path, *args)
            Path(path).write_bytes(b'changed by another process')
            return result
        self.assertEqual(self.job([source], changed)[0], 2)
        self.assertEqual(source.read_bytes(), b'changed by another process')
        self.assertFalse(source.with_suffix('').exists())

    def test_unlink_failure_rolls_back_new_image(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        before = source.read_bytes()
        unlink = Path.unlink
        def guarded(path, *args, **kwargs):
            if path == source:
                raise PermissionError('Cannot delete original')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', guarded):
            self.assertEqual(self.job([source])[0], 2)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(source.with_suffix('').exists())

    def test_corrupted_saved_copy_rolls_back_without_deleting_original(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        before = source.read_bytes()
        def bad_publish(reconstructed, folder):
            target = folder / Path(reconstructed).name
            target.write_bytes(b'corrupted during write')
            return target
        with patch.object(backend, 'publish_file', side_effect=bad_publish):
            self.assertEqual(self.job([source])[0], 2)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(source.with_suffix('').exists())

    def test_overlapping_selections_and_symlinks_are_not_processed_twice(self):
        source = self.root / 'Photos/Nested/photo.jpg.kook'
        encrypted(source)
        (source.parent / 'alias.jpg.kook').symlink_to(source)
        items = backend.collect([str(source), str(source.parent), str(self.root / 'Photos')])
        self.assertEqual([p for p, _, _ in items], [source])

    def test_empty_selection_fails(self):
        with self.assertRaisesRegex(ValueError, 'Select'):
            backend.run({'inputs': []})

    def test_non_hardlink_filesystem_uses_safe_fallback(self):
        source = self.root / 'photo.jpg.kook'
        encrypted(source)
        with patch.object(backend.os, 'link', side_effect=OSError(errno.ENOTSUP, 'Unsupported')):
            self.assertEqual(self.job([source])[0], 0)
        self.assertFalse(source.exists())
        self.assertTrue(source.with_suffix('').exists())

    def test_cancellation_during_commit_cannot_remove_both_files(self):
        source = self.root / 'Photos/photo.jpg.kook'
        encrypted(source)
        reconstructed = self.root / 'photo.jpg'
        jpeg(reconstructed)
        expected = backend.digest(source)
        publish = backend.publish_file
        class Cancelled(BaseException):
            pass
        def handler(signum, frame):
            raise Cancelled()
        def cancelling_publish(*args):
            target = publish(*args)
            os.kill(os.getpid(), signal.SIGTERM)
            return target
        old_handler = signal.signal(signal.SIGTERM, handler)
        try:
            with patch.object(backend, 'publish_file', side_effect=cancelling_publish), self.assertRaises(Cancelled):
                backend.replace_source(source, reconstructed, expected, 'photo', delete_original=True)
        finally:
            signal.signal(signal.SIGTERM, old_handler)
        self.assertFalse(source.exists())
        with Image.open(source.with_suffix('')) as image:
            image.load()

    def test_invalid_video_is_rejected_without_external_tools(self):
        movie = self.root / 'movie.mp4'
        movie.write_bytes(b'unverified')
        with patch.object(backend.shutil, 'which', return_value=None), self.assertRaisesRegex(ValueError, 'too small'):
            backend.validate_result(movie, 'video')


if __name__ == '__main__':
    unittest.main()
