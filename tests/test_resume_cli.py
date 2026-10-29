import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import recover

class RetryTests(unittest.TestCase):
    def test_retry_only_failures_and_preserve_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = [root / (name + '.bin.kook') for name in ('a', 'b')]
            for source in sources: source.write_bytes(b'encrypted fixture')
            found = [(str(p), str(root), 'salvage') for p in sources]
            output = root / 'output'
            def salvage(path, dest):
                if Path(path).name.startswith('b'): raise ValueError('retry me')
                Path(dest + '.salvaged-1.jpg').write_bytes(b'fixture')
                return 'ok', 'embedded image', [dest + '.salvaged-1.jpg']
            with patch.object(recover, 'recover_salvage', side_effect=salvage):
                result = recover.run_recovery(found, str(output), None, None, 'crop', 0, False, say=lambda _: None)
            self.assertEqual(result['counts']['failed'], 1)
            calls = []
            def retry(path, dest):
                calls.append(path)
                return 'ok', 'fixture', []
            with patch.object(recover, 'recover_salvage', side_effect=retry):
                recover.run_recovery(found, str(output), None, None, 'crop', 0, False, say=lambda _: None, retry_failed=True)
            self.assertEqual(calls, [str(sources[1])])
            self.assertTrue(all(p.read_bytes() == b'encrypted fixture' for p in sources))
