#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
project_dir="$PWD"
if [ ! -x .venv/bin/python ]; then
    echo "Create .venv and install requirements.txt first (see README.md)."
    exit 1
fi
.venv/bin/python recover.py --check
app_dir="$project_dir/dist/Djvu Recover.app"
rm -rf "$app_dir"
mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources/engine/vendor" "$project_dir/.swift-cache"
swiftc -swift-version 5 -module-cache-path "$project_dir/.swift-cache" -O macos/App.swift -o "$app_dir/Contents/MacOS/DjvuRecover" -framework AppKit
.venv/bin/python -m PyInstaller --noconfirm --clean --onedir --name DjvuPython \
    --distpath "$project_dir/build/pyinstaller-dist" \
    --workpath "$project_dir/build/pyinstaller-work" \
    --specpath "$project_dir/build" \
    --collect-all numpy --collect-all PIL macos/python-runtime.py
ditto "$project_dir/build/pyinstaller-dist/DjvuPython" "$app_dir/Contents/Resources/runtime"
cp recover.py app_backend.py "$app_dir/Contents/Resources/engine/"
cp -R vendor/photophoenix "$app_dir/Contents/Resources/engine/vendor/"
cp THIRD-PARTY-NOTICES.md "$app_dir/Contents/Resources/"
cat > "$app_dir/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>DjvuRecover</string>
<key>CFBundleIdentifier</key><string>local.djvu-recover.desktop</string>
<key>CFBundleName</key><string>Djvu Recover</string>
<key>CFBundleDisplayName</key><string>Djvu Recover</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSHumanReadableCopyright</key><string>Original app: Copyright 2026 Iman Batabaei. PhotoPhoenix: Copyright 2026 Ahmed Raza Bhatti. See bundled notices.</string>
</dict></plist>
PLIST
codesign --force --deep --sign - "$app_dir"
echo "Built: $app_dir"
echo "The app includes its Python runtime. ffprobe is still required for video validation."
