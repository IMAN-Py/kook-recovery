# macOS signing and notarization

Current builds are locally signed, not Developer ID signed or Apple notarized. No Apple Developer membership or signing identity is available for this release.

Once credentials are available:

1. Install a Developer ID Application certificate with its private key in Keychain.
2. Store notarization credentials using `xcrun notarytool store-credentials` (interactive). Never commit credentials or put them into release notes.
3. Build with `./build-macos.sh`.
4. Set `DEVELOPER_ID_APPLICATION` to the exact certificate name and `NOTARY_KEYCHAIN_PROFILE` to your stored profile name. Run `python3 scripts/notarize-macos.py`.
5. Test the signed app, then package the stapled app into the release DMG. Do not rebuild or modify the app after signing.

The script signs nested executables with hardened runtime and secure timestamps, submits only the built app archive, checks Apple's acceptance, staples the ticket, and verifies Gatekeeper assessment. The credential-dependent workflow has not been executed yet. No recovery inputs, reports, or samples are included in its upload.

See [Apple's notarization requirements](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
