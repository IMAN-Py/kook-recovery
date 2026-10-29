#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
app="$PWD/dist/STOP-Djvu Recovery.app/Contents/MacOS/DjvuRecover"
photo="${DJVU_RECOVER_SAMPLE:-}"
if [[ -z "$photo" || ! -f "$photo" ]]; then
  echo "Set DJVU_RECOVER_SAMPLE to a disposable encrypted JPEG sample before running this suite." >&2
  exit 2
fi
mkdir -p ui-tests
test_root="$(mktemp -d "$PWD/ui-tests/in-place-XXXXXX")"
.venv/bin/python - "$test_root" "$photo" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path
root, sample = map(Path, sys.argv[1:])
name = sample.name
for rel in ['success', 'folder/Photos', 'folder/Photos/Nested', 'cancel', 'conflict', 'mixed']:
    dest = root / 'inputs' / rel
    dest.mkdir(parents=True)
    shutil.copyfile(sample, dest / 'sample.jpg.kook')
marker = b'{36A698B9-D67C-4E07-BE82-0EC5B14B4DF5}'
for rel in ['error', 'mixed']:
    dest = root / 'inputs' / rel
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'document.pdf.kook').write_bytes(b'%PDF-' + bytes(5000) + bytes(256) + b'A' * 40 + marker)
    (dest / 'notes.txt').write_text('Do not modify this healthy file.')
(root / 'inputs/conflict/sample.jpg').write_bytes(b'Existing file must remain untouched.')
manifest = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'inputs').rglob('*') if p.is_file()}
(root / 'before.json').write_text(json.dumps(manifest))
(root / 'sample-sha256.txt').write_text(hashlib.sha256(sample.read_bytes()).hexdigest())
PY
"$app" --smoke-test "$test_root/inputs/success/sample.jpg.kook" "$test_root/success" --rotate-180
"$app" --smoke-test "$test_root/inputs/folder/Photos" "$test_root/folder" --rotate-180
"$app" --smoke-test "$test_root/inputs/error" "$test_root/error" --expect-error
"$app" --smoke-test "$test_root/inputs/cancel/sample.jpg.kook" "$test_root/cancel" --cancel-test
"$app" --smoke-test "$test_root/inputs/conflict" "$test_root/conflict" --expect-skipped
"$app" --smoke-test "$test_root/inputs/mixed" "$test_root/mixed" --rotate-180 --expect-mixed
.venv/bin/python - "$test_root" "$photo" <<'PY'
import hashlib, json, sys
from pathlib import Path
from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = False
root, sample = map(Path, sys.argv[1:])
before = json.loads((root / 'before.json').read_text())
for name in ['success', 'folder', 'error', 'cancel', 'conflict', 'mixed']:
    suffix = '-rerun-diagnostics' if name == 'conflict' else '-diagnostics'
    report = json.loads((root / (name + suffix) / 'app-smoke-test.json').read_text())
    assert report['passed'], (name, report)
    source = root / 'inputs' / name
    originals = {Path(rel): sha for rel, sha in before.items() if Path(rel).is_relative_to(Path('inputs') / name)}
    expected = set()
    for rel, sha in originals.items():
        path = root / rel
        replaced = name in ['success', 'folder', 'mixed'] and path.name.endswith('.jpg.kook')
        if replaced:
            assert not path.exists(), path
            target = path.with_suffix('')
            expected.add(target)
            with Image.open(target) as image:
                image.load()
                assert image.size == (5312, 2860), image.size
        else:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, path
            expected.add(path)
    actual = {p for p in source.rglob('*') if p.is_file()}
    assert actual == expected, (name, actual, expected)
    assert not any(p.name.startswith('.djvu-') for p in source.rglob('*'))
    print(name.upper(), 'PASS', report['status'])
assert hashlib.sha256(sample.read_bytes()).hexdigest() == (root / 'sample-sha256.txt').read_text()
print('User sample unchanged. Tests used disposable copies only.')
print('Artifacts:', root)
PY
