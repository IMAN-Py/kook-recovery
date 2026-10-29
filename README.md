# STOP/Djvu Recovery

<!-- cumulative-traffic -->
[![All-time views](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FIMAN-Py%2Fstop-djvu-recovery%2Fmain%2F.github%2Ftraffic%2Fviews.json)](https://github.com/IMAN-Py/stop-djvu-recovery/blob/main/.github/traffic/history.json)
[![All-time clones](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FIMAN-Py%2Fstop-djvu-recovery%2Fmain%2F.github%2Ftraffic%2Fclones.json)](https://github.com/IMAN-Py/stop-djvu-recovery/blob/main/.github/traffic/history.json)

Traffic totals are archived from GitHub starting at repository creation and refreshed periodically from the maintainer’s Mac. They include automated activity and may lag. If collection misses GitHub’s retention window, badges switch to “tracked” rather than claiming a complete all-time total.
<!-- /cumulative-traffic -->

[![Stars](https://img.shields.io/github/stars/IMAN-Py/stop-djvu-recovery?style=flat)](https://github.com/IMAN-Py/stop-djvu-recovery/stargazers)
[![Forks](https://img.shields.io/github/forks/IMAN-Py/stop-djvu-recovery?style=flat)](https://github.com/IMAN-Py/stop-djvu-recovery/forks)
[![Downloads](https://img.shields.io/github/downloads/IMAN-Py/stop-djvu-recovery/total?style=flat)](https://github.com/IMAN-Py/stop-djvu-recovery/releases)
[![Latest release](https://img.shields.io/github/v/release/IMAN-Py/stop-djvu-recovery?style=flat)](https://github.com/IMAN-Py/stop-djvu-recovery/releases/latest)
[![Windows build](https://github.com/IMAN-Py/stop-djvu-recovery/actions/workflows/windows-release.yml/badge.svg)](https://github.com/IMAN-Py/stop-djvu-recovery/actions/workflows/windows-release.yml)

Recover photos and videos from STOP/Djvu ransomware files ending in `.kook`, `.coot`, `.nile`, `.geno`, `.moss`, `.usam`, or `.maas` with
local, partial reconstruction. No decryption key or network access is used.

**Original project code and app: Copyright © 2026 Iman .** Third-party
components retain their authorship and licenses; see [third-party notices](THIRD-PARTY-NOTICES.md).

Repository name: `stop-djvu-recovery`
Suggested GitHub description: `Local partial photo/video recovery for STOP/Djvu: .kook, .coot, .nile, .geno, .moss, .usam, .maas.`

Attempt partial reconstruction of photos and videos damaged by STOP/Djvu ransomware
(enabled suffixes: `.kook`, `.coot`, `.nile`, `.geno`, `.moss`, `.usam`, `.maas`). Recovery runs locally without
a key or an internet connection. This is not decryption: encrypted bytes remain lost,
and inferred colours, dimensions, and alignment may differ from the original.

## Ransomware suffix support

The app and CLI accept `.kook`, `.coot`, `.nile`, `.geno`, `.moss`, `.usam`, and `.maas` (case insensitive). Recovery requires the compatible STOP/Djvu layout: five surviving initial bytes, an encrypted 150 KB region, and a 334-byte trailer. A matching suffix or trailer alone does not prove recovery is possible; reconstructed outputs must also pass validation.

The six additional suffixes are enabled with synthetic regression coverage. Real encrypted samples from each variant have not yet been verified. Older `.djvu`, `.udjvu`, and `.djvuu` variants are not enabled. Fully encrypted files still require a key or an intact backup. Other suffixes and healthy files are ignored.

Available starting with **v1.1.0**. Download the latest release to get the additional suffix support.

## macOS desktop app

Download the latest [Apple Silicon macOS disk image](https://github.com/IMAN-Py/stop-djvu-recovery/releases/latest/download/STOP-Djvu-Recovery-macOS-arm64.dmg), open it, and drag **STOP-Djvu Recovery.app** to Applications. On first launch, macOS may require Control-click → **Open** because the app is not notarized.

To build from source, install Swift command-line tools, Python 3.11+, and the
packages in `requirements-build.txt` into `.venv`, then run `./build-macos.sh`.
Drop encrypted files or folders into the
drop area, or use **Add files / Add folder**. Selections accumulate; duplicates are
removed automatically, and individual entries can be removed with their × buttons.
Click **Recover in place**. There is no output-folder selection.

Photos are saved as **cropped results only**, with the missing strip removed where possible.
Expand **Recovery options** for rotation or healthy reference files from the same camera.
Your rotation setting is remembered.
The window shows progress by completed file and elapsed time; it does not estimate how
much of an individual photo has finished. Inputs are locked while recovery is running.

The **Recovered files** menu switches between the recovered files. Each source produces
one output: a cropped photo (or recovered video). **Open file** opens the selected result in its default app;
**Show results** opens the original folder for the first selected source. Activity details stay collapsed until requested
or an error occurs. Quitting during recovery asks before stopping the active job.

Recovered copies are saved alongside encrypted originals. **Originals are kept by default.** The optional “Delete originals after successful recovery” checkbox removes originals only after output validation and source-integrity checks. It starts unchecked each time the app launches. Existing files are never overwritten.

**Resume last job** continues files not yet recorded as finished in the last job. **Retry failed** processes only its failed files, using the saved rotation and reference settings. Job reports are checkpointed before processing and after each file, and the last report is remembered across app restarts. A file interrupted during publication may leave a valid output; existing outputs are preserved and reported as conflicts rather than overwritten. Missing source files cannot be retried. Reports and checksums remain in the local app cache.

Each result describes partial photo reconstruction, independently extracted embedded images (possibly previews), or a structurally checked video. No “100% of the original” claim is made. Video structure validation does not guarantee complete playback; check the result in a player. Uncertain reconstructions remain failures requiring review.

Preflight checks output-folder access and reports optional video capabilities once per batch. Python, NumPy, and Pillow are bundled with the macOS app. `ffprobe` is not required. Optional `ffmpeg` helps choose healthy reference videos when the encrypted index must be rebuilt; videos with surviving indexes can be processed without it.

The app is locally signed but **not Apple notarized**. A Developer ID certificate and Apple Developer membership are required to complete that step. See [SIGNING.md](SIGNING.md) for the prepared workflow.

To rebuild after changes, run `./build-macos.sh` again.

Validation:

```sh
.venv/bin/python -m unittest discover -s tests -v
"dist/STOP-Djvu Recovery.app/Contents/MacOS/DjvuRecover" --smoke-test "/path/disposable-copy.jpg.kook" "/path/test-artifacts" --rotate-180
```

The smoke test opens the native window, runs the same worker used by the Recover button,
loads the recovered preview, and saves `app-smoke-test.json` and `app-preview.png` in
`<test-artifacts>-diagnostics` before closing. It creates recovered copies beside the input;
use disposable copies for tests. Omit `--rotate-180` to test without rotation.

Run `./macos/test-app.sh` for the GUI suite. It creates disposable copies of the sample
and checks individual-file and nested-folder replacement, failure preservation, filename
conflicts, cancellation, and batches mixing successful and unsupported files. It verifies
cropped JPEGs, preservation of encrypted originals by default, and byte-for-byte
preservation of everything else. The user's supplied sample is never modified by this
suite. Reports and captures are under `ui-tests/in-place-*/`.

## Command-line launcher (separate, copy-based workflow)

The older `recover.py` launcher below still writes to a separate output folder. The
macOS app uses `app_backend.py` and the in-place behavior described above.

It asks for the folder(s) holding the encrypted files, then for an output folder, and
rebuilds everything it can.

## Windows command-line download (64-bit)

Download and extract [STOP-Djvu-Recovery-Windows-x64.zip](https://github.com/IMAN-Py/stop-djvu-recovery/releases/latest/download/STOP-Djvu-Recovery-Windows-x64.zip). It contains a standalone command-line executable; Python is not required.

In Command Prompt or PowerShell, choose an encrypted input folder and a separate output folder:

```powershell
.\STOP-Djvu-Recovery.exe "E:\Encrypted" --output "E:\Recovered" --grey crop
```

Add `--donors "C:\HealthyPhotos"` when you have healthy photos from the same camera. The encrypted inputs are read only by this command-line workflow. Recovery is partial reconstruction, not decryption; results may be incomplete and should be reviewed.

If a photo appears upside down, add `--rotate 180`. The Windows guide in the zip covers rotation, multiple donor folders, and installing FFmpeg for video cases that need it.

The Windows zip includes a PowerShell demo that generates its own synthetic image and `.kook` container under your temporary folder. Run `Set-ExecutionPolicy -Scope Process Bypass`, then `.\demo\Run-Synthetic-Demo.ps1` to try the workflow without using personal media. The demo illustrates embedded-image salvage only, not decryption or full-photo reconstruction.

## Windows: build from source

1. **`setup.bat`** - once. Finds Python 3.11+ (offers to install it), creates a private
   `.venv` next to the tool, installs numpy + Pillow, runs a self-check. Nothing else on the
   computer is touched.
2. **`run.bat`** - every time. Asks the two questions, shows progress, keeps the window open
   at the end.

```
====================================================================
 STOP/Djvu photo & video recovery
====================================================================
Folder(s) holding the encrypted files - one per line, empty line when done.

  folder> C:\Users\me\Downloads
    + C:\Users\me\Downloads

Where should the recovered files go?
  output folder [C:\Users\me\Downloads\recovered]:
```

macOS/Linux: `./setup.sh`, then `./.venv/bin/python recover.py`.

## Supported formats - and why not every format

STOP/Djvu overwrites only the **first ~150 KB** of a file, so for anything bigger the rest is
still on disk. Getting a usable file back means decoding that surviving data **without the
header that was destroyed**. That is possible when the format can be re-aligned from the
middle; it is not possible when the header is the only key to the rest.

| Format | Result |
|---|---|
| **JPEG** - `.jpg` (the usual phone/camera photo), also `.jpeg`, `.jfif` | partial reconstruction when surviving data can be matched; some image area is missing |
| **MP4 family** (`.mp4`, `.m4v`, `.mov`, `.3gp`, `.3g2`) with the index at the end (most Android) | rebuilt and plays; the first second or two glitches |
| **MP4 family** with the index at the start (iPhone, chat apps, editors) | rebuilt **with `--donors`**: one healthy video from the same phone |
| PNG, HEIC/HEIF, TIFF, RAW, GIF, BMP | **not rebuildable** - the compressed stream/palette/tile index lives in the destroyed header, so the surviving bytes cannot be decoded |
| AVI, MKV, WebM | **not rebuildable** - codec and track layout are only in the destroyed header |
| PDF, Office, ZIP/RAR, everything else | **not rebuildable** |

The tool tells you what it found and skips the rest rather than writing broken files:

```
Found 41 file(s) this tool can rebuild: 37 photo(s), 4 video(s).
  formats: .jpg x31, .jpeg x6, .mov x3, .mp4 x1
Found 9 more encrypted file(s) that cannot be rebuilt: .png x6, .pdf x2, .zip x1
```

It does **not** decrypt anything. The key is held by the attackers; nothing on your computer
can reverse those 150 KB. Keep the encrypted originals - if a key ever leaks, they can be
fully decrypted.

## Options

```
py recover.py [FOLDER ...] [-o OUTPUT] [--donors FOLDER] [--grey keep|crop|both]
              [--rotate 0|90|180|270] [--overwrite] [--check]
```

* `--donors FOLDER` - healthy, unencrypted photos/videos from the **same phone**. These can
  improve colour and camera-table estimates and help rebuild videos whose index was
  destroyed. Results still require visual review. Repeat the flag for several folders.
* `--grey keep` (default) keeps the lost strip as grey, `crop` cuts it off, `both` writes a
  normal file plus a ` (cropped)` copy.
* `--rotate 180` - photos taken with an EXIF rotation flag lose that flag, so they come out
  upside down; this turns them upright.
* `--overwrite` - redo files that are already in the output folder.
* `--check` - print the environment the engine sees and exit (used by `setup.bat`).
* Folders on the command line skip the first question; `-o` skips the second.

Output keeps the folder structure of the input and drops the ransomware extension:
`DCIM\2016\IMG_1234.jpg.kook` -> `<output>\DCIM\2016\IMG_1234.jpg`. Files already recovered
are skipped, so re-running is safe.

## Limits worth knowing

* A thin strip at the top of each photo is gone - about 4 % of a 3-4 MB photo, but up to half
  of a 300-500 KB one.
* EXIF, GPS and the capture date are inside the destroyed part; they are not restored.
* Without `--donors`, colours are estimated from the surviving data - usually right, but skin
  tones and skies can be slightly off.
* The Windows CLI needs `ffmpeg` on `PATH` for some MP4 videos whose playback index was encrypted; it can also help with sound. See the [Windows CLI guide](WINDOWS-CLI.md) for installation steps. Both interfaces validate video structure without requiring `ffprobe`.
* Camera tables learned from your photos are cached in `.cache\` next to the script (or in
  the temp folder if that is not writable).
* Files under ~150 KB were encrypted completely - nothing left to rebuild.

## After a STOP/Djvu infection

The malware usually arrives with cracked software or "activators". Scan the machine, and do
not pay: online-key files cannot be decrypted by anyone but the attackers, and this tool does
not need a key.

## Licence and credits

The recovery engine is **[PhotoPhoenix](https://github.com/ahmed-raza-bhatti/photophoenix)**
by Ahmed Raza Bhatti, vendored under `vendor/photophoenix` and used under the
**PolyForm Noncommercial License 1.0.0** - free for personal, research, education, charity and
public-sector use; **commercial use requires a separate licence** from its author. See
`THIRD-PARTY-NOTICES.md` and `vendor/photophoenix/LICENSE.txt`.

Modifications to the vendored copy: the hard-coded filemarker GUID was generalised to a
pattern to recognise compatible trailer shapes (`has_marker()` in `engine.py`).

The scanner can identify some JPEG and MP4-family files from their surviving
signatures even when their filename extension is missing or misleading. Other
formats may contain a separate intact embedded image that can be salvaged, but
this does not reconstruct or decrypt the original file.
