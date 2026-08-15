#!/usr/bin/env bash
# Build Argon.app and install it to /Applications.
#
# SwiftPM produces a bare executable; a SwiftUI App needs a real bundle around
# it or NSApplication never activates properly and the menu bar item does not
# appear. This assembles that bundle.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Applications/Argon.app}"

cd "$HERE"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/ArgonMac"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/ArgonMac"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>Argon</string>
  <key>CFBundleDisplayName</key>     <string>Argon</string>
  <key>CFBundleIdentifier</key>      <string>com.niranjanj.argonmac</string>
  <key>CFBundleExecutable</key>      <string>ArgonMac</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundleVersion</key>         <string>1</string>
  <key>LSMinimumSystemVersion</key>  <string>14.0</string>
  <!-- Menu bar only: no Dock icon, no window on launch. -->
  <key>LSUIElement</key>             <true/>
  <key>NSHumanReadableCopyright</key><string>Argon</string>
</dict>
</plist>
PLIST

# Ad-hoc signature. Without any signature at all macOS kills the process on
# launch on Apple Silicon; this is enough for a locally built tool.
codesign --force --sign - "$APP" >/dev/null 2>&1 || true

echo "built $APP"
