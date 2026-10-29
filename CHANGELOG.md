# Changelog

## 1.2.0

- Keep encrypted originals by default; optional deletion remains explicit.
- Checkpoint desktop jobs and add Resume last job / Retry failed controls.
- Add CLI failure reports and --retry-failed.
- Explain partial reconstruction quality without misleading original-completeness percentages.
- Preflight output access and optional ffmpeg capabilities.
- Prepare Developer ID signing and notarization tooling; notarization remains blocked pending Apple Developer credentials.
- Additional suffixes remain experimental until genuine samples are available.

## 1.1.1

- Rename the project to STOP/Djvu Recovery, covering the supported ransomware family.
- Update app branding, repository links, and macOS/Windows download names.
- Recovery capabilities and experimental variant status are unchanged.

## 1.1.0

- Enable partial recovery attempts for `.coot`, `.nile`, `.geno`, `.moss`, `.usam`, and `.maas`, alongside `.kook`, in the desktop app and CLI.
- Preserve original media filenames and video container selection across supported suffixes.
- Require a compatible STOP/Djvu trailer before replacing any encrypted source.
- Display the supported suffixes in the app and document their limits.
- Add regression coverage for supported suffixes, excluded files, and invalid-source preservation. All 26 tests pass.

The new suffixes have synthetic test coverage; real samples for each variant remain unverified. Recovery reconstructs surviving data and does not decrypt encrypted bytes. Older `.djvu`, `.udjvu`, and `.djvuu` variants remain excluded.

## 1.0.0

Initial public release.
