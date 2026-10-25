#!/bin/bash
# Makes RenderBuddy.app in ~/Applications. Open it to turn RenderBuddy on or off, pair it with STEVE, or remove it.
# It also answers renderbuddy://start, renderbuddy://stop and renderbuddy://uninstall links.
# The app is a small native window (app/RenderBuddy.swift, built with the Mac's own Swift compiler). If that can't be
# built, it makes the simpler dialog version from app/RenderBuddy.applescript instead.
set -e
cd "$(dirname "$0")"
HERE="$(pwd)"
APP="$HOME/Applications/RenderBuddy.app"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' renderbuddy.py)"
mkdir -p "$HOME/Applications" "$HOME/.renderbuddy"
[ -x .venv/bin/python ] || bash install.sh
rm -rf "$APP" "$HOME/Applications/BlenderBuddy.app"   # also the app's old name
RES="$APP/Contents/Resources"
PLIST="$APP/Contents/Info.plist"
BUILD_LOG="$HOME/.renderbuddy/app-build.log"

build_native() {
  command -v swiftc >/dev/null 2>&1 || return 1
  mkdir -p "$APP/Contents/MacOS" "$RES"
  echo "Building the RenderBuddy app (about a minute)…"
  swiftc -O -swift-version 5 -parse-as-library app/RenderBuddy.swift \
    -o "$APP/Contents/MacOS/RenderBuddy" >"$BUILD_LOG" 2>&1 || return 1
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>RenderBuddy</string>
  <key>CFBundleIdentifier</key><string>studio.renderbuddy.launcher</string>
  <key>CFBundleName</key><string>RenderBuddy</string>
  <key>CFBundleDisplayName</key><string>RenderBuddy</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleIconFile</key><string>RenderBuddy</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleURLTypes</key>
  <array><dict>
    <key>CFBundleURLName</key><string>RenderBuddy</string>
    <key>CFBundleURLSchemes</key><array><string>renderbuddy</string></array>
  </dict></array>
  <key>RBFolder</key><string>$HERE</string>
</dict>
</plist>
PLIST
}

build_dialogs() {
  local script
  script="$(mktemp -t renderbuddy).applescript"
  sed "s|__FOLDER__|$HERE|" app/RenderBuddy.applescript > "$script"
  osacompile -o "$APP" "$script"
  rm -f "$script"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier studio.renderbuddy.launcher" "$PLIST" 2>/dev/null ||
    /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string studio.renderbuddy.launcher" "$PLIST"
  /usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes array" \
    -c "Add :CFBundleURLTypes:0 dict" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLName string RenderBuddy" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" \
    -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string renderbuddy" "$PLIST"
  # osacompile points the app at a built-in icon (CFBundleIconName + Assets.car), which wins over any .icns file.
  rm -rf "$RES/Assets.car" "$RES/applet.icns"
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile RenderBuddy" "$PLIST"
}

if build_native; then
  KIND="window"
else
  echo "Couldn't build the RenderBuddy window (details in $BUILD_LOG), so making the simpler version."
  rm -rf "$APP"
  build_dialogs
  KIND="dialogs"
fi

# The icon, built with the Mac's own tools (the shipped .icns is the fallback).
ICONSET="$(mktemp -d)/RenderBuddy.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s app/RenderBuddy-1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
  sips -z $((s * 2)) $((s * 2)) app/RenderBuddy-1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
done
iconutil -c icns -o "$RES/RenderBuddy.icns" "$ICONSET" 2>/dev/null || cp app/RenderBuddy.icns "$RES/RenderBuddy.icns"
rm -rf "$(dirname "$ICONSET")"

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
touch "$APP"
killall Dock 2>/dev/null || true   # so the new icon shows right away

echo
echo "Made $APP ($KIND)."
echo "Open it from Applications (or press Cmd-Space and type RenderBuddy)."
