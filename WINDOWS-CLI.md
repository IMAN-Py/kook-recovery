# Windows command-line package

Download `STOP-Djvu-Recovery-Windows-x64.zip` from the [GitHub Releases page](https://github.com/IMAN-Py/stop-djvu-recovery/releases/latest) and extract it. The standalone `STOP-Djvu-Recovery.exe` does not require a separate Python install. Run `STOP-Djvu-Recovery.exe --help` to see all options.

## Basic recovery

Pass an encrypted input folder and a separate output folder:

```powershell
.\STOP-Djvu-Recovery.exe "E:\Encrypted" --output "E:\Recovered" --grey crop
```

You can pass more than one input folder:

```powershell
.\STOP-Djvu-Recovery.exe "E:\Phone backup" "F:\Camera card" --output "D:\Recovered" --grey crop
```

The CLI reads encrypted inputs and writes recovered files to a separate output folder. It never replaces or deletes those originals. Existing output files are kept; rerunning the command skips them.

## Reference photos and rotation

Healthy photos from the same camera help estimate its JPEG tables and colors. Use one or more reference folders with `--donors`:

```powershell
.\STOP-Djvu-Recovery.exe "E:\Encrypted" --output "E:\Recovered" --donors "C:\HealthyPhotos" --grey crop
```

Repeat `--donors` to add another folder. The reference photos are read only. If photos appear upside down because their original EXIF orientation was encrypted, set `--rotate 180`. The supported values are `0`, `90`, `180`, and `270` degrees:

```powershell
.\STOP-Djvu-Recovery.exe "E:\Encrypted" --output "E:\Recovered" --donors "C:\HealthyPhotos" --rotate 180 --grey crop
```

## Optional FFmpeg for video recovery

Some MP4 videos have their playback index near the beginning, inside the encrypted portion. Rebuilding those files may need a healthy reference video from the same phone and `ffmpeg` available on `PATH`. `ffmpeg` also helps the tool choose a reference and check video data. Without it, those videos can fail or be marked for review; photo recovery still works.

Install the Windows FFmpeg package with [winget](https://learn.microsoft.com/windows/package-manager/winget/):

```powershell
winget install --id Gyan.FFmpeg.Shared --exact
```

Open a new terminal, then check that Windows can find both tools:

```powershell
ffmpeg -version
ffprobe -version
```

Official FFmpeg download page lists the Windows builds and links to their providers: [ffmpeg.org/download.html](https://www.ffmpeg.org/download.html). This project does not bundle FFmpeg.

## Safe synthetic demo

The zip includes `demo\Run-Synthetic-Demo.ps1`. It creates a small geometric test image and a synthetic `.kook` container under your temporary folder, runs the CLI, and saves the recovered image in a separate demo output folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\demo\Run-Synthetic-Demo.ps1
```

This demo shows extraction of a complete image embedded beyond the encrypted region. It does not demonstrate full photo decryption or reconstruction, and it uses no personal media. The generated test files stay in your temporary folder.

## Limits and licenses

Recovery is partial reconstruction, not decryption. Missing encrypted data cannot be restored, and output needs review. Files too small to have surviving data or with unsupported formats may remain unrecovered.

The package includes third-party notices. PhotoPhoenix is licensed under the PolyForm Noncommercial License 1.0.0; commercial use requires a separate license from its author.

## Ransomware suffixes

Current source supports partial recovery attempts for `.kook`, `.coot`, `.nile`, `.geno`, `.moss`, `.usam`, and `.maas`. The additional suffixes have synthetic coverage; real samples per variant remain unverified. Compatible STOP/Djvu structure and successful output validation are required. Earlier release binaries must be rebuilt to include this support.
