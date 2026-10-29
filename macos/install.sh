#!/bin/bash
# One-command installer for STOP-Djvu Recovery (Apple Silicon, macOS 14+).
# Usage: curl -fsSL https://raw.githubusercontent.com/IMAN-Py/stop-djvu-recovery/main/macos/install.sh | bash
set -euo pipefail

REPO="IMAN-Py/stop-djvu-recovery"
APP_NAME="STOP-Djvu Recovery.app"
DMG_URL="https://github.com/${REPO}/releases/latest/download/STOP-Djvu-Recovery-macOS-arm64.dmg"
INSTALL_DIR="${INSTALL_DIR:-/Applications}"

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS only."
[ "$(uname -m)" = "arm64" ] || fail "the packaged app needs an Apple Silicon Mac (M1 or newer). Intel Macs can use the command-line launcher: https://github.com/${REPO}#command-line-launcher-separate-copy-based-workflow"

tmp_dir="$(mktemp -d)"
mount_point="${tmp_dir}/mount"
mkdir -p "$mount_point"

cleanup() {
    hdiutil detach "$mount_point" -quiet >/dev/null 2>&1 || true
    rm -rf "$tmp_dir"
}
trap cleanup EXIT

echo "Downloading the latest STOP-Djvu Recovery release..."
curl -fL --progress-bar "$DMG_URL" -o "$tmp_dir/app.dmg" \
    || fail "download failed. Check your connection and try again."

echo "Opening the disk image..."
hdiutil attach "$tmp_dir/app.dmg" -nobrowse -readonly -mountpoint "$mount_point" -quiet \
    || fail "the downloaded disk image could not be opened."

source_app="${mount_point}/${APP_NAME}"
[ -d "$source_app" ] || fail "the disk image did not contain ${APP_NAME}."

[ -d "$INSTALL_DIR" ] || mkdir -p "$INSTALL_DIR" 2>/dev/null || true
if [ ! -w "$INSTALL_DIR" ]; then
    INSTALL_DIR="${HOME}/Applications"
    mkdir -p "$INSTALL_DIR"
    echo "No write access to /Applications, installing for this user instead."
fi

target="${INSTALL_DIR}/${APP_NAME}"
echo "Installing to ${target}..."
rm -rf "$target"
ditto "$source_app" "$target" || fail "copying the app failed."

# The release is ad-hoc signed. Clearing the download flag lets Finder and
# Launchpad open it normally instead of showing the unidentified-developer dialog.
xattr -dr com.apple.quarantine "$target" >/dev/null 2>&1 || true

if [ "${NO_OPEN:-}" != "1" ]; then
    open "$target" || echo "Installed. Open it from Launchpad or Applications."
fi

echo "Done: ${target}"
