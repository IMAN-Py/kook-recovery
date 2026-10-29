import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recover
import app_backend
from PIL import Image

TRAILER = bytes(256) + b'A'*40 + b'{36A698B9-D67C-4E07-BE82-0EC5B14B4DF5}'

class FormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fixture(self, name, head, tail=b'', length=None):
        p = self.root/name
        size = length or recover.engine.ENC_END
        p.write_bytes(head + bytes(size-len(head)) + tail + TRAILER)
        return p

    def test_supported_suffixes_and_video_names(self):
        for suffix in recover.RANSOMWARE_EXTENSIONS:
            with self.subTest(suffix=suffix):
                p = self.fixture('clip.mov' + suffix.upper(), b'\x00\x00\x00\x18f')
                self.assertEqual(recover.inspect(p), (True, 'video'))
                self.assertEqual(recover.original_name(p), 'clip.mov')
                self.assertTrue(recover.video.is_video(str(p)))
                p = self.fixture('document.pdf' + suffix, b'%PDF-')
                self.assertEqual(recover.original_name(p), 'document.pdf')
        for suffix in ['.djvu', '.udjvu', '.djvuu', '.unknown', '']:
            p = self.fixture('healthy.jpg' + suffix, b'\xff\xd8\xff\xe1\x00')
            self.assertEqual(recover.inspect(p), (False, None))

    def test_extensionless_video_and_name_preservation(self):
        p = self.fixture('20200207_193427 (1003).kook', b'\x00\x00\x00\x18f')
        self.assertEqual(recover.inspect(p), (True, 'video'))
        self.assertEqual(recover.original_name(p, 'video'), '20200207_193427 (1003).mp4')

    def test_misnamed_jpeg_and_small_thumbnail_detection(self):
        for name in ['movie.mpg.kook', 'Thumbs.db.kook', 'tiny.THM.kook']:
            p = self.fixture(name, b'\xff\xd8\xff\xe1\x00', length=100)
            self.assertEqual(recover.inspect(p), (True, 'photo'))
            self.assertTrue(recover.original_name(p,'photo').endswith('.jpg'))

    def test_all_kook_including_empty_are_reported(self):
        for name in ['unknown.kook', 'SONYEDIT.IND.kook']:
            (self.root/name).touch()
        found, unsupported = recover.scan([str(self.root)])
        self.assertEqual(len(found), 2)
        with self.assertRaisesRegex(ValueError, 'trailer'):
            recover.recover_salvage(found[0][0], str(self.root/'out'))

    def test_embedded_image_salvage_keeps_original_in_backend(self):
        image = io.BytesIO()
        Image.new('RGB', (32,32), 'red').save(image, 'PNG')
        p = self.fixture('container.tiff.kook', b'II*\x00\x08', image.getvalue())
        before = p.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = app_backend.run({'inputs':[str(p)]})
        self.assertEqual(code, 0)
        self.assertEqual(p.read_bytes(), before)
        result = next(e for e in map(json.loads, output.getvalue().splitlines()) if e['event']=='result')
        self.assertFalse(result['original_removed'])
        with Image.open(result['files'][0]) as image:
            image.load()
            self.assertEqual(image.size, (32,32))

    def test_corrupt_embedded_image_is_not_published(self):
        p = self.fixture('bad.png.kook', b'\x89PNG\r', b'\xff\xd8\xffgarbage\xff\xd9')
        with self.assertRaisesRegex(ValueError, 'No intact'):
            recover.recover_salvage(str(p), str(self.root/'out'))
        self.assertEqual(list(self.root.iterdir()), [p])

    def test_fully_encrypted_metadata_has_clear_reason(self):
        p = self.fixture('clip.modd.kook', b'<?xml', length=100)
        with self.assertRaisesRegex(ValueError, 'decryption key'):
            recover.recover_salvage(str(p), str(self.root/'out'))

    def test_jpeg_sync_does_not_read_past_short_stream(self):
        from photophoenix import fastjpeg
        self.assertIsNone(fastjpeg.sync(b'', [], 3))
        result = fastjpeg.decode(b'abc', [], 3, start_bit=1000)
        self.assertEqual(result[1], 'eof')

    def test_non_kook_encrypted_files_are_ignored(self):
        for name in ['photo.jpg', 'movie.mp4', 'other.locked']:
            p = self.fixture(name, b'\xff\xd8\xff\xe1\x00')
            self.assertEqual(recover.inspect(p), (False, None))
        found, unsupported = recover.scan([str(self.root)])
        self.assertEqual(found, [])
        self.assertEqual(unsupported, [])

    def test_failed_photo_can_salvage_preview_without_deleting_source(self):
        from unittest.mock import patch
        image = io.BytesIO()
        Image.new('RGB', (32,32), 'blue').save(image, 'JPEG')
        p = self.fixture('damaged.jpg.kook', b'\xff\xd8\xff\xe1\x00', image.getvalue())
        before = p.read_bytes()
        output = io.StringIO()
        with patch.object(recover, '_recover_photo', side_effect=ValueError('no matching camera')), patch.object(recover, 'build_library', return_value=object()), contextlib.redirect_stdout(output):
            code = app_backend.run({'inputs':[str(p)]})
        self.assertEqual(code, 0)
        self.assertEqual(p.read_bytes(), before)
        result = next(e for e in map(json.loads, output.getvalue().splitlines()) if e['event']=='result')
        self.assertFalse(result['original_removed'])
        self.assertEqual(result['engine_status'], 'salvaged')

    def test_selected_folder_is_automatically_used_for_references(self):
        from unittest.mock import patch
        p = self.fixture('damaged.jpg.kook', b'\xff\xd8\xff\xe1\x00')
        with patch.object(recover, 'build_library', return_value=object()) as build, patch.object(recover, 'recover_photo', side_effect=ValueError('no data')), contextlib.redirect_stdout(io.StringIO()):
            app_backend.run({'inputs':[str(p)]})
        self.assertEqual(build.call_args.args[0], [str(self.root.resolve())])
