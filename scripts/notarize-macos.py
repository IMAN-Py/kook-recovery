#!/usr/bin/env python3
"""Sign the built app and submit only its archive to Apple (requires credentials)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run(*args):
    subprocess.run(args, check=True)


def main():
    identity = os.environ.get('DEVELOPER_ID_APPLICATION', '')
    profile = os.environ.get('NOTARY_KEYCHAIN_PROFILE', '')
    if not identity.startswith('Developer ID Application:') or not profile:
        raise SystemExit('Set DEVELOPER_ID_APPLICATION and NOTARY_KEYCHAIN_PROFILE after installing your Apple Developer ID certificate and storing notarization credentials in Keychain.')
    root = Path(__file__).resolve().parents[1]
    app = root / 'dist/STOP-Djvu Recovery.app'
    if not app.is_dir():
        raise SystemExit('Run ./build-macos.sh first.')
    identities = subprocess.check_output(['security', 'find-identity', '-v', '-p', 'codesigning'], text=True)
    if identity not in identities:
        raise SystemExit('Requested Developer ID identity is not installed.')
    # Sign nested Mach-O code first, then enclosing bundles, then the outer app.
    for path in sorted(app.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink() or not path.is_file():
            continue
        info = subprocess.check_output(['file', '-b', str(path)], text=True)
        if 'Mach-O' in info:
            run('codesign', '--force', '--options', 'runtime', '--timestamp', '--sign', identity, str(path))
    bundles = [p for p in app.rglob('*') if p.is_dir() and not p.is_symlink() and p.suffix in ('.framework', '.app')]
    for bundle in sorted(bundles, key=lambda p: len(p.parts), reverse=True) + [app]:
        run('codesign', '--force', '--options', 'runtime', '--timestamp', '--sign', identity, str(bundle))
    run('codesign', '--verify', '--deep', '--strict', str(app))
    with tempfile.TemporaryDirectory(prefix='djvu-notary-') as work:
        archive = str(Path(work) / 'app.zip')
        run('ditto', '-c', '-k', '--keepParent', str(app), archive)
        response = subprocess.check_output(['xcrun', 'notarytool', 'submit', archive, '--keychain-profile', profile, '--wait', '--output-format', 'json'], text=True)
        result = json.loads(response)
        if result.get('status') != 'Accepted':
            raise SystemExit('Notarization did not succeed. Review the submission in notarytool history/log before distribution.')
    run('xcrun', 'stapler', 'staple', str(app))
    run('xcrun', 'stapler', 'validate', str(app))
    run('spctl', '--assess', '--type', 'execute', '--verbose', str(app))
    print('App notarized and ticket stapled. Package this app into the release DMG.')


if __name__ == '__main__':
    main()
