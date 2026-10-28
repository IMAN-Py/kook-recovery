param(
    [string]$OutputRoot = (Join-Path $env:TEMP ("kook-recovery-synthetic-demo-" + [guid]::NewGuid().ToString("N"))),
    [string]$ExePath = (Join-Path (Split-Path -Parent $PSScriptRoot) "Kook-Recovery.exe")
)

$ErrorActionPreference = "Stop"
$exe = $ExePath
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Kook-Recovery.exe must be next to the demo folder. Extract the complete Windows release zip first."
}
if (Test-Path -LiteralPath $OutputRoot) {
    throw "The demo output path already exists. Choose a new path with -OutputRoot; existing files are never overwritten."
}

$inputFolder = Join-Path $OutputRoot "encrypted-input"
$outputFolder = Join-Path $OutputRoot "recovered-output"
New-Item -ItemType Directory -Path $OutputRoot | Out-Null
New-Item -ItemType Directory -Path $inputFolder, $outputFolder | Out-Null

Add-Type -AssemblyName System.Drawing
$syntheticJpeg = Join-Path $OutputRoot "synthetic-scene.jpg"
$bitmap = [System.Drawing.Bitmap]::new(160, 120)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.Clear([System.Drawing.Color]::FromArgb(235, 242, 250))
    $blue = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(45, 115, 190))
    $orange = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(245, 160, 55))
    try {
        $graphics.FillRectangle($blue, 16, 18, 76, 82)
        $graphics.FillEllipse($orange, 84, 30, 54, 54)
    } finally {
        $blue.Dispose()
        $orange.Dispose()
    }
    $bitmap.Save($syntheticJpeg, [System.Drawing.Imaging.ImageFormat]::Jpeg)
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}

$containerPath = Join-Path $inputFolder "synthetic-container.tiff.kook"
$prefix = [byte[]]::new(160 * 1024)
$keyAndId = [byte[]]::new(256 + 40)
$id = [System.Text.Encoding]::ASCII.GetBytes("A" * 40)
[Array]::Copy($id, 0, $keyAndId, 256, $id.Length)
$marker = [System.Text.Encoding]::ASCII.GetBytes("{36A698B9-D67C-4E07-BE82-0EC5B14B4DF5}")
$jpeg = [System.IO.File]::ReadAllBytes($syntheticJpeg)
$stream = [System.IO.File]::Open($containerPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $stream.Write($prefix, 0, $prefix.Length)
    $stream.Write($jpeg, 0, $jpeg.Length)
    $stream.Write($keyAndId, 0, $keyAndId.Length)
    $stream.Write($marker, 0, $marker.Length)
} finally {
    $stream.Dispose()
}

Write-Host "Running the CLI on synthetic test data..."
& $exe $inputFolder --output $outputFolder --grey crop
if ($LASTEXITCODE -ne 0) {
    throw "The CLI returned exit code $LASTEXITCODE. Demo files were kept at $OutputRoot"
}

$recovered = Join-Path $outputFolder "synthetic-container.tiff.salvaged-1.jpg"
if (-not (Test-Path -LiteralPath $recovered -PathType Leaf)) {
    throw "The expected synthetic image was not created. Demo files were kept at $OutputRoot"
}
Write-Host ""
Write-Host "Synthetic demo complete. Open the recovered image:"
Write-Host $recovered
Write-Host "All generated files are under: $OutputRoot"
