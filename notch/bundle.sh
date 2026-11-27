#!/usr/bin/env bash
# Build Machina Notch and wrap it in a minimal .app bundle.
set -euo pipefail

cd "$(dirname "$0")"
swift build -c release

APP="build/MachinaNotch.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp ".build/release/MachinaNotch" "$APP/Contents/MacOS/MachinaNotch"
cp "Info.plist" "$APP/Contents/Info.plist"

# Ad-hoc signature is enough for local use. Replace with a Developer ID
# identity for distribution and notarization.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "$APP"
