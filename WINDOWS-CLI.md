# Windows command-line package

Download `Kook-Recovery-Windows-x64.zip` from the GitHub Releases page and extract it. Run `Kook-Recovery.exe --help` for options or pass the encrypted folder and output folder directly:

```powershell
.\Kook-Recovery.exe "E:\Encrypted" --output "E:\Recovered" --grey crop
```

The CLI reads encrypted inputs and writes recovered files to a separate output folder. It does not replace or delete encrypted originals. Recovery is partial reconstruction, not decryption. Healthy reference photos from the same camera can be supplied with `--donors`. Video cases whose index is encrypted may also need `ffmpeg` installed and available on `PATH`.

The package includes third-party notices. PhotoPhoenix is licensed under the PolyForm Noncommercial License 1.0.0; commercial use requires a separate license from its author.
